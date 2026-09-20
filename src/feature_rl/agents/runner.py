"""Admission-bound source agent. Only reviewed M3 workers execute candidate actions."""
from datetime import datetime, timezone
import hashlib
import time
import uuid
from typing import Literal
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, ArtifactError, canonical_json
from feature_rl.environments import EnvironmentRuntime, PreparedEnvironment, SavedSource, EnvironmentError
from feature_rl.grading import GradingService, read_grade, GradePublicationFailed
from feature_rl.pipeline import TaskBuilder, TaskLifecycle, ReleasedTaskResolver, AdmissionRejected
from feature_rl.registry import Registry, JobSpec, Claim, CostObservation, RegistryError
from feature_rl.registry.core import references
from feature_rl.verifiers.loader import read_local
from feature_rl.verifiers.language import decode_json
from feature_rl.submission.source import Submission
from .backend import PolicyBackend, Completion, InvalidGeneration, GenerationUnavailable
from .protocol import HARNESS, INSTRUCTIONS, Submit, parse_action, execution_request

MAX_RECORD = 4*1024*1024

class RunRecoveryRequired(Exception):
    """An assigned run exists. Reconcile retained work; never resample it implicitly."""
    def __init__(self,message,claim,retained=None):
        super().__init__(message);self.claim=claim;self.retained=retained
class RunPublicationFailed(RunRecoveryRequired):
    def __init__(self,claim,payload):
        super().__init__('Retain frozen run bytes and replay publication only',claim,payload)
        self.sha256=hashlib.sha256(payload).hexdigest()
class RunGradePending(RunRecoveryRequired):
    def __init__(self,claim,pending,continuation):
        super().__init__('Replay exact saved-source grade publication; never rerun the agent',claim,pending)
        self.continuation=continuation
class RunSubmissionPending(RunRecoveryRequired):
    def __init__(self,claim,state):
        super().__init__('Republish submission from the exact closed saved source; never rerun the agent',claim,state)

class RunFreezePending(RunRecoveryRequired):
    def __init__(self,claim,state):
        super().__init__('Replay retained controller outcome publication only',claim,state)

class RunInput(c.StrictModel):
    version: Literal['m7-run-input-v1']='m7-run-input-v1'
    task: c.ArtifactRef
    policy: c.PolicyConfig
    limits: c.ResourceLimits
    case_seed: int
    invocation: str

class ControllerOutcome(c.StrictModel):
    version: Literal['m7-controller-outcome-v1']='m7-controller-outcome-v1'
    run_id: str
    request: c.ArtifactRef
    task: c.ArtifactRef
    policy: c.PolicyConfig
    case_seed: int
    stopping_reason: c.StopReason
    disposition: c.Disposition
    reward: int | None
    submission: c.ArtifactRef | None
    cleanup_verified: bool
    evidence: tuple[c.ArtifactRef,...]
    reason: str
    revision: str
    recorded_at: c.UTCDateTime

class FrozenRun(c.StrictModel):
    version: Literal['m7-frozen-run-v1']='m7-frozen-run-v1'
    claim: Claim
    request: c.ArtifactRef
    configuration: c.ArtifactRef
    outcome: c.ArtifactRef
    record: c.RolloutRecord
    observations: tuple[str,...]


def cost(category, *, wall=None, input_tokens=None, output_tokens=None, note):
    return c.CostRecord(category=category,wall_seconds=wall,cpu_seconds=None,gpu_seconds=None,
        input_tokens=input_tokens,output_tokens=output_tokens,human_minutes=None,usd=None,
        measurement='unknown' if all(v is None for v in (wall,input_tokens,output_tokens)) else 'partial',note=note)


def aggregate(costs):
    """One cumulative channel per category; unknown dimensions stay unknown."""
    result=[]
    for category in sorted({x.category for x in costs}):
        rows=[x for x in costs if x.category==category]
        values={name:None if any(getattr(x,name) is None for x in rows) else sum(getattr(x,name) for x in rows)
            for name in ('wall_seconds','cpu_seconds','gpu_seconds','input_tokens','output_tokens','human_minutes','usd')}
        result.append(c.CostRecord(category=category,**values,
            measurement='partial' if any(v is not None for v in values.values()) else 'unknown',
            note='Actual phase costs aggregated by category; unknown dimensions retained. Raw phase receipt preserves attribution.'))
    return tuple(result)


class AgentRunner:
    def __init__(self, *, store: ArtifactStore, registry: Registry, lifecycle: TaskLifecycle,
                 builder: TaskBuilder, runtime: EnvironmentRuntime, grader: GradingService,
                 backend: PolicyBackend, revision: str, owner: str='feature_rl.agents',
                 evidence_scope: Literal['real_integration','unit_diagnostic']='real_integration'):
        if (not isinstance(store,ArtifactStore) or store.role!=c.ActorRole.CONTROLLER
            or type(registry) is not Registry or registry.store is not store
            or type(lifecycle) not in (TaskLifecycle,ReleasedTaskResolver) or lifecycle.store is not store or lifecycle.registry is not registry
            or type(builder) is not TaskBuilder or builder.store is not store or builder.registry is not registry
            or type(runtime) is not EnvironmentRuntime or runtime.store is not store
            or type(grader) is not GradingService or grader.store is not store or grader.runtime is not runtime
            or not isinstance(backend,PolicyBackend)):
            raise TypeError('Actual same-store M3/M4/M6 services and trusted PolicyBackend required')
        if len(revision) not in (40,64) or any(x not in '0123456789abcdef' for x in revision):
            raise ValueError('exact runner revision required')
        if evidence_scope not in ('real_integration','unit_diagnostic'): raise ValueError('explicit evidence scope required')
        self.store,self.registry,self.lifecycle,self.builder=store,registry,lifecycle,builder
        self.runtime,self.grader,self.backend,self.revision,self.owner=runtime,grader,backend,revision,owner
        self.scope=evidence_scope
        self.configuration=self._put({'version':'m7-runner-v1','revision':revision,'harness':HARNESS,
            'lifecycle':lifecycle.configuration.model_dump(mode='json'),'builder_revision':builder.revision,
            'runtime_revision':runtime.revision,'grader_revision':grader.revision,'scope':evidence_scope,
            'tokenizer':backend.tokenizer_digest,'template':backend.template_digest,
            'max_seq_len':backend.max_seq_len,'vocab_size':backend.vocab_size},'m7-runner-config',
            dependencies=(lifecycle.configuration,))

    def _put(self,value,kind,*,dependencies=()):
        payload=value if isinstance(value,bytes) else canonical_json(value.model_dump(mode='json') if hasattr(value,'model_dump') else value)
        if len(payload)>MAX_RECORD: raise ValueError('runner record byte bound exceeded')
        ref=self.store.put_bytes(payload,kind,c.Visibility.PRIVATE)
        self.registry.register(ref,dependencies=tuple(dict.fromkeys(dependencies)))
        return ref

    def _bytes(self,ref,cap=1024*1024):
        return self.store.get_bytes(ref,max_envelope_bytes=2*cap+2048,max_payload_bytes=cap)

    def _messages(self,task):
        # Only M6's admitted SolverView is projected. Private Q/H/oracle fields never enter the prompt.
        if task.qualification is None: raise AdmissionRejected('accepted report missing')
        report=self.store.get_artifact(task.qualification,max_envelope_bytes=8*1024*1024)
        package=self.builder.solver_package(report.task)
        # Verification uses the already frozen M6 package, not a replacement archive.
        if not package: raise AdmissionRejected('empty frozen solver package')
        instruction=self._bytes(task.solver_view.instruction).decode('utf-8')
        runtime=self._bytes(task.solver_view.runtime_manifest).decode('utf-8')
        public=[self._bytes(ref,262144).decode('utf-8') for ref in task.solver_view.public_checks]
        inventory=self._bytes(task.solver_view.inventory).decode('utf-8')
        return [{'role':'user','content':INSTRUCTIONS+canonical_json({
            'instruction':instruction,'runtime':runtime,'inventory':inventory,'public_checks':public}).decode()}]

    def _declare_submission(self,ref):
        value=read_local(self.store,ref,Submission,'m4-submission',65536)
        self.registry.register(ref,dependencies=(value.baseline,value.changes))

    def _declare_grade(self,ref):
        grade=read_grade(self.store,ref)
        for runtime_ref in grade.runtime_evidence:
            self._declare_runtime(runtime_ref)
        self.registry.register(ref,dependencies=tuple(dict.fromkeys(references(grade.model_dump(mode='json')))))
        return grade

    def _declare_runtime(self,ref):
        if ref.kind=='environment-execution':
            value=decode_json(self._bytes(ref,MAX_RECORD),MAX_RECORD)
            self.registry.register(ref,dependencies=tuple(dict.fromkeys(references(value))))

    def _observe(self,claim,source,identifier,revision,receipts,costs):
        return self.registry.reconcile(claim,CostObservation(source=source,upstream_attempt_id=identifier,
            revision=revision,receipts=tuple(dict.fromkeys(receipts)),costs=aggregate(costs)))

    def _phase(self,claim,request,kind,index,call,measure):
        identifier=claim.attempt_id+':'+kind+':'+str(index)
        intent=self._put({'version':'m7-phase-v1','request':request.model_dump(mode='json'),
            'attempt':claim.attempt_id,'kind':kind,'index':index,'recorded_at':datetime.now(timezone.utc).isoformat()},
            'm7-phase-intent',dependencies=(request,))
        category={'generation':'rollout','action':'execution','grade':'verifier'}[kind]
        self._observe(claim,'m7-'+kind,identifier,1,(intent,),
            (cost(category,note='Dispatched phase; outcome/cost unknown until externally established'),))
        start=time.monotonic()
        try:
            value=call()  # Exactly once. A lost call is never retried as a policy sample.
        except Exception as exc:
            exc.m7_phase_ref=intent
            raise
        elapsed=time.monotonic()-start
        try:
            receipts=[intent]
            if kind=='grade':
                self._declare_grade(value.artifacts[0])
                receipts.extend(value.artifacts)
            elif kind=='action':
                self._declare_runtime(value.evidence)
                receipts.append(value.evidence)
            else:
                # Persist the actual returned tokens before interpretation. This
                # anchors cost and invalid-generation diagnostics to returned data.
                receipts.append(self._put({'version':'m7-generation-v1','intent':intent.model_dump(mode='json'),'context':value.context,
                    'tokens':value.tokens,'text':value.text,'logprobs':value.logprobs,
                    'policy_version':value.policy_version,'finish_reason':value.finish_reason},
                    'm7-generation',dependencies=(intent,)))
            values=measure(value,elapsed)
            if category not in {v.category for v in values}:
                values=(*values,cost(category,note='Dispatched phase category remains unmeasured'))
            self._observe(claim,'m7-'+kind,identifier,2,tuple(receipts),values)
        except (ArtifactError,OSError,RegistryError) as exc:
            raise RunRecoveryRequired('Completed external phase has unconfirmed accounting; retain its exact result',claim,value) from exc
        return value,elapsed,intent

    def run(self,task,policy,limits,*,case_seed=None,invocation=None):
        policy=c.PolicyConfig.model_validate_json(policy.model_dump_json())
        limits=c.ResourceLimits.model_validate_json(limits.model_dump_json())
        case_seed=policy.seed if case_seed is None else case_seed
        if type(case_seed) is not int or not 0<=case_seed<2**63: raise ValueError('exact frozen 63-bit case seed required')
        if policy.harness_version!=HARNESS: raise ValueError('unsupported fixed agent harness')
        if policy.require_token_probabilities and (policy.temperature!=1. or policy.top_p!=1.):
            raise ValueError('training runner supports unit-temperature full-support sampling only')
        if policy.system_prompt.visibility!=c.Visibility.PUBLIC:
            raise ValueError('fixed system prompt must be an explicitly public artifact')
        if limits.input_tokens>262144 or limits.output_tokens>65536 or limits.tool_calls>128:
            raise ValueError('runner record profile supports at most 262144 input tokens, 65536 output tokens and 128 tools')
        invocation=uuid.uuid4().hex if invocation is None else invocation
        request=RunInput(task=task,policy=policy,limits=limits,case_seed=case_seed,invocation=invocation)
        refs=(task,policy.system_prompt,*(() if policy.identity.weights is None else (policy.identity.weights,)))
        req=self._put(request,'m7-run-input',dependencies=refs)
        spec=JobSpec(operation='run',inputs=(task,req),configuration=self.configuration,
            implementation=self.revision,invocation=invocation,attempt_limit=1)
        job=self.registry.enqueue(spec)
        if job.state=='completed':
            self.lifecycle.resolve_released(task)
            return job.result
        if job.state!='queued':
            raise RunRecoveryRequired('Assigned run already dispatched; recover instead of sampling again',
                self.registry.attempts(job.job_id)[-1].claim)
        claim=self.registry.claim(job.job_id,owner=self.owner,claim_key=uuid.uuid4().hex)
        self._observe(claim,'m7-controller',claim.attempt_id,1,(req,),
            (cost('rollout',note='Controller attempt dispatched; work unknown before freeze'),
             cost('storage',note='Registry/CAS/recovery costs remain unmeasured')))
        started=time.monotonic();external=0.;steps=[];evidence=[];grading=();submission=None;handle=None
        used_in=used_out=tools=0;used_cpu=0.;cleanup=True;stop=c.StopReason.SUBMITTED
        disposition=c.Disposition.INVALID;reward=None;reason='episode did not complete'
        try:
            admitted=self.lifecycle.resolve_released(task)
            contract=self.store.get_artifact(admitted.contract,max_envelope_bytes=4*1024*1024)
            recipe=self.store.get_artifact(admitted.environment,max_envelope_bytes=4*1024*1024)
            for field in ('wall_seconds','cpu_seconds','memory_bytes','pids','output_bytes','disk_bytes','tool_calls','input_tokens','output_tokens'):
                if getattr(limits,field)>getattr(contract.episode_limits,field):
                    raise AdmissionRejected('requested episode limits exceed the admitted contract')
            for field in ('cpu_seconds','memory_bytes','pids','output_bytes','disk_bytes'):
                if getattr(recipe.limits,field)>getattr(limits,field):
                    raise AdmissionRejected('fixed M3 worker resource cap exceeds requested episode limits')
            if self.runtime.policy.cpus>1.:
                raise AdmissionRejected('remaining-CPU command wall cap requires an admitted quota of at most one CPU')
            self.backend.verify_policy(policy)
            messages=[{'role':'system','content':self._bytes(policy.system_prompt,262144).decode()}]+self._messages(admitted)
            policies=[r for r in recipe.provenance.inputs if r.kind=='sandbox-policy']
            if len(policies)!=1: raise AdmissionRejected('one exact M3 sandbox policy required')
            handle=self.runtime.open_workspace(PreparedEnvironment(recipe=admitted.environment,policy=policies[0]),
                role='candidate',allowed_changes=contract.allowed_changes)
            while True:
                remaining=limits.wall_seconds-(time.monotonic()-started)
                if remaining<=0: stop=c.StopReason.TIME_LIMIT;break
                if used_cpu>=limits.cpu_seconds: stop=c.StopReason.TIME_LIMIT;break
                if tools>=limits.tool_calls: stop=c.StopReason.TOOL_LIMIT;break
                self.lifecycle.resolve_released(task)
                self.backend.verify_policy(policy)
                rendered,context=self.backend.render(messages)
                max_tokens=min(limits.output_tokens-used_out,self.backend.max_seq_len-len(context))
                if max_tokens<=0 or used_in+len(context)>limits.input_tokens:
                    stop=c.StopReason.TOKEN_LIMIT;break
                completion,elapsed,intent=self._phase(claim,req,'generation',len(steps),
                    lambda:self.backend.generate(context,policy=policy,max_tokens=max_tokens,timeout=remaining,
                        session_id=job.job_id+':'+str(len(steps))),
                    lambda v,w:(cost('rollout',wall=w,input_tokens=len(context),output_tokens=len(v.tokens),
                        note='Actual generation latency and token counts; GPU/USD unmeasured'),))
                external+=elapsed;evidence.append(intent)
                action_ref=self._put({'version':'m7-action-v1','rendered_context':rendered,'text':completion.text,
                    'context_ids':context,'sampled_ids':completion.tokens,'logprobs':completion.logprobs,
                    'finish_reason':completion.finish_reason,'policy_version':completion.policy_version},'m7-action')
                evidence.append(action_ref)
                completion.validate(context=context,policy=policy,max_tokens=max_tokens,vocab_size=self.backend.vocab_size)
                used_in+=len(context);used_out+=len(completion.tokens)
                trace=None if completion.logprobs is None or policy.temperature!=1. or policy.top_p!=1. else c.TokenTrace(context_token_ids=context,
                    sampled_token_ids=completion.tokens,behavior_log_probabilities=completion.logprobs,
                    assistant_loss_mask=(True,)*len(completion.tokens),policy_version=policy.policy_version)
                obs={'status':'submitted'};terminal=False;step_refs=[intent];action_invalid=False
                try:
                    if completion.finish_reason=='length':
                        stop=c.StopReason.TOKEN_LIMIT;terminal=True;obs={'status':'token_limit'}
                    else:
                        action=parse_action(completion.text)
                        if isinstance(action,Submit): terminal=True
                        else:
                            remaining=limits.wall_seconds-(time.monotonic()-started)
                            if remaining<=0: stop=c.StopReason.TIME_LIMIT;terminal=True;obs={'status':'time_limit'}
                            else:
                                # At <=1 CPU, this conservatively bounds the
                                # command's CPU by remaining measured allowance.
                                # M3 setup/export/cleanup remain separately capped;
                                # this is not a hard aggregate session CPU override.
                                execution=execution_request(action,min(remaining,limits.cpu_seconds-used_cpu))
                                tools+=1
                                result,elapsed,phase=self._phase(claim,req,'action',len(steps),
                                    lambda:self.runtime.execute_development(handle,execution),lambda v,w:(v.cost,))
                                external+=elapsed;evidence.extend((phase,result.evidence));step_refs.extend((phase,result.evidence))
                                self._declare_runtime(result.evidence)
                                if result.cost.cpu_seconds is not None: used_cpu+=result.cost.cpu_seconds
                                obs={'status':result.reason,'exit_code':result.exit_code,
                                    'stdout':result.stdout[:65536].decode('utf-8',errors='replace'),
                                    'stderr':result.stderr[:65536].decode('utf-8',errors='replace'),
                                    'stdout_bytes':len(result.stdout),'stderr_bytes':len(result.stderr),
                                    'stdout_omitted_bytes':max(0,len(result.stdout)-65536),
                                    'stderr_omitted_bytes':max(0,len(result.stderr)-65536),'save_status':result.save_status}
                                if result.failure_category in ('infrastructure','unresolved') or not result.cleanup_verified:
                                    cleanup=result.cleanup_verified
                                    action_invalid=True
                except ValueError as exc:
                    stop=c.StopReason.MALFORMED_ACTION;terminal=True;obs={'status':'malformed_action','reason':str(exc)[:1024]}
                obs_ref=self._put(obs,'m7-observation')
                step_ev=c.EvidenceRecord(producer='feature_rl.agents',command=('AgentRunner.turn',job.job_id,str(len(steps))),
                    recorded_at=datetime.now(timezone.utc),exit_status=0,artifacts=tuple(step_refs),revision=self.revision,scope=self.scope)
                steps.append(c.EpisodeStep(index=len(steps),action=action_ref,observation=obs_ref,token_trace=trace,evidence=(step_ev,)))
                if action_invalid:
                    raise GenerationUnavailable('M3 established infrastructure/unresolved action outcome')
                messages.extend(({'role':'assistant','content':completion.text},{'role':'user','content':canonical_json(obs).decode()}))
                if terminal: break
            saved=self.runtime.close(handle);handle=None
            evidence.append(saved.artifact)
            state=dict(claim=claim,req=req,request=request,steps=steps,evidence=evidence,
                submission=None,stop=stop,cleanup=cleanup,grade_index=0,
                saved=saved,baseline=admitted.baseline,allowed_changes=contract.allowed_changes,
                controller_wall=max(0.,time.monotonic()-started-external))
            return self._after_saved(state)
        except RunRecoveryRequired: raise
        except (InvalidGeneration,AdmissionRejected,ValueError) as exc:
            if hasattr(exc,'m7_phase_ref'): evidence.append(exc.m7_phase_ref)
            reward=None;disposition=c.Disposition.INVALID;stop=c.StopReason.INVALID_TRAJECTORY
            reason=type(exc).__name__+': '+str(exc)[:1024]
        except (GenerationUnavailable,EnvironmentError,ArtifactError,OSError,RegistryError) as exc:
            if hasattr(exc,'m7_phase_ref'): evidence.append(exc.m7_phase_ref)
            reward=None;disposition=c.Disposition.INFRASTRUCTURE;stop=c.StopReason.INFRASTRUCTURE_FAILURE
            reason=type(exc).__name__+': '+str(exc)[:1024]
        finally:
            if handle is not None:
                try:self.runtime.close(handle)
                except Exception:
                    cleanup=False;reward=None;disposition=c.Disposition.INFRASTRUCTURE;stop=c.StopReason.INFRASTRUCTURE_FAILURE
                    reason='workspace cleanup unverified; no measured outcome'
        return self._freeze(claim=claim,req=req,request=request,steps=steps,evidence=evidence,grading=grading,
            submission=submission,stop=stop,cleanup=cleanup,disposition=disposition,reward=reward,reason=reason,
            controller_wall=max(0.,time.monotonic()-started-external))

    def _after_saved(self,state,recovered_grade=None):
        """No policy calls beyond this point; any grade retry uses the same source/seed."""
        request=state['request'];claim=state['claim'];req=state['req']
        self.lifecycle.resolve_released(request.task)
        if state['submission'] is None:
            try:
                state['submission']=self.grader.submissions.from_saved(state['baseline'],state['saved'].artifact,state['allowed_changes'])
                self._declare_submission(state['submission'])
            except (ArtifactError,OSError,RegistryError) as exc:
                raise RunSubmissionPending(claim,state) from exc
        submission=state['submission']
        for index in range(state['grade_index'],3):
            state['grade_index']=index
            self.lifecycle.resolve_released(request.task)
            try:
                if recovered_grade is None:
                    grade,_,intent=self._phase(claim,req,'grade',index,
                        lambda:self.grader.grade(request.task,submission,request.case_seed),lambda v,w:v.costs)
                else:
                    grade=recovered_grade;recovered_grade=None;intent=state['grade_intent']
                    values=grade.costs
                    if 'verifier' not in {v.category for v in values}:
                        values=(*values,cost('verifier',note='Dispatched grader category remains unmeasured'))
                    self._declare_grade(grade.artifacts[0])
                    self._observe(claim,'m7-grade',claim.attempt_id+':grade:'+str(index),2,(intent,*grade.artifacts),values)
                state['evidence'].append(intent)
            except GradePublicationFailed as pending:
                state['grade_intent']=pending.m7_phase_ref
                state['evidence'].append(pending.m7_phase_ref)
                raise RunGradePending(claim,pending,state) from pending
            receipt=self._declare_grade(grade.artifacts[0])
            state['evidence'].extend(ref for e in grade.evidence for ref in e.artifacts)
            if (receipt.task,receipt.submission,receipt.case_seed)!=(request.task,submission,request.case_seed):
                raise InvalidGeneration('M4 returned a different task/submission/case seed')
            if receipt.disposition!=c.Disposition.INFRASTRUCTURE:break
        reward=receipt.reward;disposition=receipt.disposition;stop=state['stop']
        if reward is None:
            disposition=c.Disposition.INFRASTRUCTURE if receipt.disposition==c.Disposition.INFRASTRUCTURE else c.Disposition.INVALID
            stop=c.StopReason.INFRASTRUCTURE_FAILURE if disposition==c.Disposition.INFRASTRUCTURE else c.StopReason.INVALID_TRAJECTORY
        self.lifecycle.resolve_released(request.task)
        return self._freeze(**{k:state[k] for k in ('claim','req','request','steps','evidence','submission','cleanup','controller_wall')},
            grading=grade.evidence,stop=stop,disposition=disposition,reward=reward,reason=receipt.reason)

    def _freeze(self,**state):
        state.setdefault('recorded_at',datetime.now(timezone.utc))
        try:return self._freeze_impl(**state)
        except (ArtifactError,OSError,RegistryError) as exc:
            raise RunFreezePending(state['claim'],state) from exc

    def _freeze_impl(self,*,claim,req,request,steps,evidence,grading,submission,stop,cleanup,disposition,reward,reason,controller_wall,recorded_at):
        outcome=ControllerOutcome(run_id=claim.job_id,request=req,task=request.task,policy=request.policy,
            case_seed=request.case_seed,stopping_reason=stop,disposition=disposition,reward=reward,submission=submission,
            cleanup_verified=cleanup,evidence=tuple(dict.fromkeys((*evidence,*(ref for e in grading for ref in e.artifacts)))),
            reason=reason,revision=self.revision,recorded_at=recorded_at)
        outcome_ref=self._put(outcome,'m7-controller-outcome',dependencies=(req,*outcome.evidence,*(() if submission is None else (submission,))))
        own=c.EvidenceRecord(producer='feature_rl.agents',command=('AgentRunner.run',claim.job_id,request.task.sha256,str(request.case_seed)),
            recorded_at=outcome.recorded_at,exit_status=0 if reward is not None else 1,artifacts=(outcome_ref,),revision=self.revision,scope=self.scope)
        observed=self._observe(claim,'m7-controller',claim.attempt_id,2,(req,outcome_ref),
            (cost('rollout',wall=controller_wall,note='Controller wall excluding completed timed external phases; failed-call wall may overlap unknown phase cost'),
             cost('storage',note='Registry/CAS/recovery costs unmeasured')))
        snapshots=sorted((x for x in self.registry.accounting(claim.job_id).observations if x.attempt_id==claim.attempt_id),key=lambda x:x.observation_id)
        costs=tuple(v for x in snapshots for v in x.observation.costs)
        record=c.RolloutRecord(kind='RolloutRecord',schema_version=1,visibility=c.Visibility.PRIVATE,
            provenance=c.Provenance(producer='feature_rl.agents',producer_version=self.revision,created_at=outcome.recorded_at,
                inputs=(request.task,req,self.configuration,outcome_ref),evidence=(own,)),costs=costs,run_id=claim.job_id,
            task=request.task,policy=request.policy,limits=request.limits,
            seeds=c.SeedPolicy(algorithm='m4-sha256-v1',seeds=(request.case_seed,),same_cases_within_group=True),
            steps=tuple(steps),submission=submission,stopping_reason=stop,disposition=disposition,reward=reward,
            grading_evidence=(*grading,own),training_eligible=reward is not None and bool(steps) and all(s.token_trace is not None for s in steps)
                and request.policy.identity.tokenizer_digest is not None)
        frozen=FrozenRun(claim=claim,request=req,configuration=self.configuration,outcome=outcome_ref,
            record=record,observations=tuple(x.observation_id for x in snapshots))
        return self._publish(claim,canonical_json(frozen.model_dump(mode='json')))

    def _publish(self,claim,payload):
        frozen=FrozenRun.model_validate_json(payload)
        if frozen.claim!=claim or frozen.configuration!=self.configuration or frozen.record.run_id!=claim.job_id:
            raise ValueError('frozen run belongs to different service/claim')
        try:
            ref=self._put(payload,'m7-frozen-run',dependencies=tuple(dict.fromkeys(references(frozen.model_dump(mode='json')))))
            # Freeze in the same actual Registry before publishing the public artifact model.
            self._observe(claim,'m7-freeze',claim.attempt_id,1,(ref,),
                (cost('storage',note='Frozen run publication/recovery overhead unmeasured'),))
            return self._finish(frozen,ref)
        except (ArtifactError,OSError,RegistryError) as exc: raise RunPublicationFailed(claim,payload) from exc

    def _finish(self,frozen,ref):
        job=self.registry.job(frozen.claim.job_id)
        if job.state=='completed': return job.result
        record_ref=self.store.put_artifact(frozen.record)
        self.registry.register(record_ref,dependencies=(ref,))
        snapshots=sorted((x for x in self.registry.accounting(job.job_id).observations if x.attempt_id==frozen.claim.attempt_id),key=lambda x:x.observation_id)
        result=c.OperationResult(operation='run',disposition=frozen.record.disposition,artifacts=(record_ref,ref),
            evidence=frozen.record.provenance.evidence,costs=tuple(v for x in snapshots for v in x.observation.costs),reason='Completed immutable agent episode; '+frozen.record.disposition.value)
        return self.registry.complete(frozen.claim,result,observations=tuple(x.observation_id for x in snapshots)).result

    def recover(self,claim):
        job=self.registry.job(claim.job_id)
        if job.spec.configuration!=self.configuration or not any(x.claim==claim for x in self.registry.attempts(job.job_id)):
            raise ValueError('unknown run claim/service')
        if job.state=='completed': return job.result
        refs={r for x in self.registry.accounting(job.job_id).observations if x.attempt_id==claim.attempt_id
            for r in x.observation.receipts if r.kind=='m7-frozen-run'}
        if len(refs)!=1: raise RunRecoveryRequired('No durably frozen outcome; reconcile unknown work without resampling',claim)
        ref=next(iter(refs));frozen=read_local(self.store,ref,FrozenRun,'m7-frozen-run',MAX_RECORD)
        return self._finish(frozen,ref)

    def retry_publication(self,pending):
        if not isinstance(pending,(RunSubmissionPending,RunGradePending,RunFreezePending,RunPublicationFailed)):
            raise ValueError('exact retained run publication required')
        job=self.registry.job(pending.claim.job_id)
        if job.spec.configuration!=self.configuration or not any(a.claim==pending.claim for a in self.registry.attempts(job.job_id)):
            raise ValueError('pending run belongs to different service/claim')
        if job.state=='completed':return job.result
        if isinstance(pending,RunSubmissionPending):return self._after_saved(pending.retained)
        if isinstance(pending,RunFreezePending):return self._freeze(**pending.retained)
        if isinstance(pending,RunGradePending):
            grade=self.grader.retry_publication(pending.retained)
            return self._after_saved(pending.continuation,recovered_grade=grade)
        if hashlib.sha256(pending.retained).hexdigest()!=pending.sha256:
            raise ValueError('exact retained run publication required')
        return self._publish(pending.claim,pending.retained)

    def validate_record(self,record):
        """Authenticate selected completed run and its controller outcome, including nulls."""
        record=c.RolloutRecord.model_validate_json(record.model_dump_json())
        job=self.registry.job(record.run_id)
        if (job.spec.operation!='run' or job.spec.configuration!=self.configuration or job.state!='completed'
                or job.result is None or len([x for x in job.result.artifacts if x.kind=='RolloutRecord'])!=1):
            raise ValueError('rollout lacks its exact selected Registry run')
        record_ref=next(x for x in job.result.artifacts if x.kind=='RolloutRecord')
        if self.store.get_artifact(record_ref,max_envelope_bytes=8*1024*1024)!=record: raise ValueError('selected rollout differs')
        refs=[x for x in job.result.artifacts if x.kind=='m7-frozen-run']
        if len(refs)!=1: raise ValueError('unique frozen run required')
        frozen=read_local(self.store,refs[0],FrozenRun,'m7-frozen-run',MAX_RECORD)
        if frozen.record!=record or frozen.configuration!=self.configuration or frozen.claim.job_id!=job.job_id:
            raise ValueError('frozen run identity differs')
        request=read_local(self.store,frozen.request,RunInput,'m7-run-input',MAX_RECORD)
        if (job.spec.inputs!=(record.task,frozen.request) or job.spec.implementation!=self.revision
                or job.spec.invocation!=request.invocation
                or (request.task,request.policy,request.limits,request.case_seed)!=(record.task,record.policy,record.limits,record.seeds.seeds[0])
                or job.result.disposition!=record.disposition or len(job.result.artifacts)!=2):
            raise ValueError('selected run request/configuration/result binding differs')
        if not any(a.claim==frozen.claim and a.state=='completed' for a in self.registry.attempts(job.job_id)):
            raise ValueError('frozen run lacks selected completed claim')
        outcome=read_local(self.store,frozen.outcome,ControllerOutcome,'m7-controller-outcome',MAX_RECORD)
        own=[e for e in record.grading_evidence if e.producer=='feature_rl.agents']
        if (outcome.request!=frozen.request or outcome.revision!=self.revision or len(own)!=1
                or own[0].command!=('AgentRunner.run',record.run_id,record.task.sha256,str(request.case_seed))
                or own[0].artifacts!=(frozen.outcome,) or own[0].recorded_at!=outcome.recorded_at
                or own[0].revision!=self.revision or own[0].scope!=self.scope):
            raise ValueError('runner controller receipt/evidence differs')
        if (outcome.run_id,outcome.task,outcome.policy,outcome.case_seed,outcome.reward,outcome.disposition,outcome.submission,outcome.stopping_reason)!=(
            record.run_id,record.task,record.policy,record.seeds.seeds[0],record.reward,record.disposition,record.submission,record.stopping_reason):
            raise ValueError('controller outcome differs from selected rollout')
        for ref in (record_ref,refs[0],frozen.outcome,self.configuration): self.registry.assert_usable(ref)
        return outcome

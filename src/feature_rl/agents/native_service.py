"""Ordinary native run ownership, selected child join and publication-only recovery."""
from datetime import datetime,timezone
import time
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore,canonical_json
from feature_rl.registry import Registry,JobSpec,CostObservation,Claim
from feature_rl.registry.core import references
from feature_rl.pipeline import TaskLifecycle,ReleasedTaskResolver,TaskBuilder
from feature_rl.environments import EnvironmentRuntime
from feature_rl.grading import GradingService
from feature_rl.training.factory import NativeSessionFactory
from feature_rl.training.checkpoints import validate_selected_checkpoint
from feature_rl.verifiers.language import decode_json
from .runner import AgentRunner,RunRecoveryRequired,RunPublicationFailed,RunGradePending,RunSubmissionPending,RunFreezePending
from .runner import validate_selected_record,aggregate,cost
from .protocol import HARNESS


class NativeRunRecoveryRequired(RuntimeError):
    def __init__(self,message,claim):super().__init__(message);self.claim=claim


class NativeRunService:
    def __init__(self,*,store,registry,native_factory,lifecycle,builder,runtime,grader,revision,
                 evidence_scope='real_integration'):
        if (type(store) is not ArtifactStore or store.role!=c.ActorRole.CONTROLLER
            or type(registry) is not Registry or registry.store is not store
            or type(native_factory) is not NativeSessionFactory or native_factory.store is not store or native_factory.registry is not registry
            or type(lifecycle) not in (TaskLifecycle,ReleasedTaskResolver) or lifecycle.store is not store or lifecycle.registry is not registry
            or type(builder) is not TaskBuilder or builder.store is not store or builder.registry is not registry
            or type(runtime) is not EnvironmentRuntime or runtime.store is not store
            or type(grader) is not GradingService or grader.store is not store or grader.runtime is not runtime):
            raise TypeError('Actual inert factory and same-store M0/M3/M4/M6 services required')
        if len(revision) not in (40,64) or any(x not in '0123456789abcdef' for x in revision):raise ValueError('Exact native run revision required')
        if evidence_scope not in ('real_integration','unit_diagnostic'):raise ValueError('Explicit evidence scope required')
        self.store,self.registry,self.native_factory=store,registry,native_factory
        self.lifecycle,self.builder,self.runtime,self.grader=lifecycle,builder,runtime,grader
        self.revision,self.scope=revision,evidence_scope;self._states={}
        self.configuration=self._put({'version':'m7-native-run-service-v1','native_factory':native_factory.configuration.model_dump(mode='json'),
            'lifecycle':lifecycle.configuration.model_dump(mode='json'),'builder_revision':builder.revision,
            'runtime_revision':runtime.revision,'grader_revision':grader.revision,'revision':revision,'scope':evidence_scope},
            'm7-native-run-configuration',(native_factory.configuration,lifecycle.configuration))

    def _put(self,value,kind,dependencies=()):
        payload=canonical_json(value)
        if len(payload)>4*1024*1024:raise ValueError('Native run receipt byte bound')
        ref=self.store.put_bytes(payload,kind,c.Visibility.PRIVATE)
        self.registry.register(ref,dependencies=tuple(dict.fromkeys(dependencies)));return ref

    def _read(self,ref):return decode_json(self.store.get_bytes(ref,max_envelope_bytes=8*1024*1024,max_payload_bytes=4*1024*1024),4*1024*1024)

    def _observe(self,claim,source,revision,refs,costs):
        return self.registry.reconcile(claim,CostObservation(source=source,upstream_attempt_id=claim.attempt_id,
            revision=revision,receipts=tuple(refs),costs=aggregate(costs)))

    def _checkpoint(self,policy):
        initial=self.native_factory.training_configuration.initial_policy
        if policy.identity.weights is None:raise ValueError('Exact imported native policy weights required')
        if policy.identity.weights==initial.identity.weights and policy.policy_version==initial.policy_version:
            return policy.identity.weights
        candidates=[]
        if policy.identity.weights is None:raise ValueError('Exact imported native policy weights required')
        for job_id in self.registry.trace(policy.identity.weights).jobs:
            job=self.registry.job(job_id)
            if job.spec.operation=='train' and job.state=='completed' and job.result and job.result.disposition==c.Disposition.SUCCESS:
                for ref in job.result.artifacts:
                    if ref.kind=='TrainingCheckpoint':
                        cp=self.store.get_artifact(ref)
                        if cp.weights==policy.identity.weights and cp.policy_version==policy.policy_version:
                            validate_selected_checkpoint(self.store,self.registry,ref)
                            candidates.append(ref)
        if len(set(candidates))!=1:raise ValueError('One selected completed training checkpoint must bind the native run policy')
        return candidates[0]

    def run(self,task,policy,limits,*,case_seed=None,invocation):
        policy=c.PolicyConfig.model_validate_json(policy.model_dump_json());limits=c.ResourceLimits.model_validate_json(limits.model_dump_json())
        case_seed=policy.seed if case_seed is None else case_seed
        if type(case_seed) is not int or not 0<=case_seed<2**63:raise ValueError('Frozen 63-bit case seed required')
        if policy.system_prompt.visibility!=c.Visibility.PUBLIC or policy.harness_version!=HARNESS:raise ValueError('Public native runner prompt/harness required')
        self.registry.assert_usable(task)
        self.lifecycle.resolve_released(task)
        checkpoint=self._checkpoint(policy)
        request=self._put({'version':'m7-native-run-request-v1','task':task.model_dump(mode='json'),
            'policy':policy.model_dump(mode='json'),'limits':limits.model_dump(mode='json'),'case_seed':case_seed,
            'checkpoint':checkpoint.model_dump(mode='json'),'invocation':invocation,
            'configuration':self.configuration.model_dump(mode='json')},'m7-native-run-request',
            (task,checkpoint,self.configuration,*references(policy.model_dump(mode='json'))))
        job=self.registry.enqueue(JobSpec(operation='run',inputs=(task,request),configuration=self.configuration,
            implementation=self.revision,invocation=invocation,attempt_limit=1))
        if job.state=='completed':return job.result
        if job.state!='queued':return self.recover(self.registry.attempts(job.job_id)[-1].claim)
        if any(s.get('handle') is not None for s in self._states.values()):raise ValueError('Close the retained native run before another startup')
        claim=self.registry.claim(job.job_id,owner='feature_rl.agents.native',claim_key='native-run-'+job.job_id)
        self.last_claim=claim
        state={'claim':claim,'request':request,'handle':None,'runner':None,'child':None,'pending':None,'outcome':None}
        self._states[claim.job_id]=state
        self._observe(claim,'m7-native-run-controller',1,(request,),
            (cost('rollout',note='Native run controller bookkeeping; incremental CPU/GPU/USD unmeasured'),
             cost('storage',note='Native run CAS/Registry storage unmeasured')))
        try:
            state['startup_dispatched']=True
            state['handle']=self.native_factory.create(claim,startup_key=claim.attempt_id+':init')
            state['startup_confirmed']=True
            handle=state['handle']
            self._observe(claim,'m7-native-run-activation',1,(request,handle.receipt),
                (cost('rollout',note='Native policy activation/probe dispatched; result/cost unknown'),))
            start=time.monotonic();activation=handle.session.activate_checkpoint(checkpoint,policy)
            state['activation']=activation
            self._observe(claim,'m7-native-run-activation',2,(request,handle.receipt,activation),
                (cost('rollout',wall=time.monotonic()-start,note='Actual policy activation/probe wall; incremental native resources unmeasured'),))
            runner=AgentRunner(store=self.store,registry=self.registry,lifecycle=self.lifecycle,builder=self.builder,
                runtime=self.runtime,grader=self.grader,backend=handle.session.backend,revision=self.revision,evidence_scope=self.scope)
            state['runner']=runner
            intent=self._put({'request':request.model_dump(mode='json'),'runner_configuration':runner.configuration.model_dump(mode='json'),
                'child_invocation':'native-child-'+claim.job_id,'activation':activation.model_dump(mode='json')},
                'm7-native-run-child-intent',(request,runner.configuration,activation))
            state['intent']=intent
            self._observe(claim,'m7-native-run-child',1,(intent,),
                (cost('rollout',note='One assigned child episode dispatched; outcome/cost unknown'),))
            handle.session.validate_activation(activation,checkpoint=checkpoint,policy=policy)
            self.registry.assert_usable(task)
            self.lifecycle.resolve_released(task)
            try:state['child']=runner.run(task,policy,limits,case_seed=case_seed,invocation='native-child-'+claim.job_id)
            except RunRecoveryRequired as exc:state['pending']=exc;raise
            self._publish_child(state)
        except BaseException as error:
            try:self._close(state)
            except BaseException as cleanup:error.native_cleanup_error=cleanup
            raise
        self._close(state)
        return self._finish(state)

    def close(self):
        """Terminal command cleanup, including unpublished startups; no model work."""
        for state in self._states.values():self._close(state)

    def _close(self,state):
        if state.get('startup_dispatched') and not state.get('startup_confirmed'):
            self.native_factory.close_startup(state['claim'],startup_key=state['claim'].attempt_id+':init',
                shutdown_key=state['claim'].attempt_id+':abort')
            state['startup_confirmed']=True
        if state.get('handle') is not None:
            self.native_factory.close(state['handle'],state['claim'],shutdown_key=state['claim'].attempt_id+':close')
            state['handle']=None

    def _validate(self,state):
        child=state['child'];request=self._read(state['request']);intent=self._read(state['intent'])
        refs=[r for r in child.artifacts if r.kind=='RolloutRecord']
        if child.operation!='run' or len(refs)!=1:raise ValueError('One actual selected child rollout required')
        record=self.store.get_artifact(refs[0])
        configuration=c.ArtifactRef.model_validate_json(canonical_json(intent['runner_configuration']))
        validate_selected_record(self.store,self.registry,record,configuration=configuration,revision=self.revision,scope=self.scope)
        if (record.task.model_dump(mode='json')!=request['task'] or record.policy.model_dump(mode='json')!=request['policy']
            or record.limits.model_dump(mode='json')!=request['limits'] or record.seeds.seeds!=(request['case_seed'],)
            or self.registry.job(record.run_id).result!=child
            or self.registry.job(record.run_id).spec.invocation!='native-child-'+state['claim'].job_id):
            raise ValueError('Selected native child differs from frozen parent assignment')
        self.lifecycle.resolve_released(record.task)
        return record

    def _publish_child(self,state):
        record=self._validate(state)
        if state['outcome'] is None:
            state['outcome']=self._put({'version':'m7-native-run-outcome-v1','claim':state['claim'].model_dump(mode='json'),
                'request':state['request'].model_dump(mode='json'),'intent':state['intent'].model_dump(mode='json'),
                'child':state['child'].model_dump(mode='json')},'m7-native-run-outcome',
                (state['request'],state['intent'],*state['child'].artifacts))
        self._observe(state['claim'],'m7-native-run-child',2,(state['intent'],state['outcome']),state['child'].costs)

    def _finish(self,state):
        record=self._validate(state);claim=state['claim']
        rows=sorted(self.registry.accounting(claim.job_id).observations,key=lambda r:r.observation_id)
        shutdown=[r for r in rows if r.observation.source=='m7-native-shutdown' and r.observation.revision==2]
        if len(shutdown)!=1:raise NativeRunRecoveryRequired('Owned native shutdown must be confirmed before parent completion',claim)
        evidence=c.EvidenceRecord(producer='feature_rl.agents.native',command=('NativeRunService.run',claim.job_id,record.run_id),
            recorded_at=record.provenance.created_at,exit_status=0,artifacts=(state['outcome'],),revision=self.revision,scope=self.scope)
        result=c.OperationResult(operation='run',disposition=state['child'].disposition,
            artifacts=(*state['child'].artifacts,state['outcome']),evidence=(*state['child'].evidence,evidence),
            costs=tuple(v for r in rows for v in r.observation.costs),reason='Selected native child episode and owned shutdown completed')
        return self.registry.complete(claim,result,observations=tuple(r.observation_id for r in rows)).result

    def recover(self,claim):
        job=self.registry.job(claim.job_id);self.last_claim=claim
        if job.spec.configuration!=self.configuration or not any(a.claim==claim for a in self.registry.attempts(job.job_id)):
            raise ValueError('Exact native run service/claim required')
        task=job.spec.inputs[0]
        state=self._states.get(claim.job_id)
        # Cleanup remains available when current admission has been revoked.
        if state is not None:self._close(state)
        self.registry.assert_usable(task)
        self.lifecycle.resolve_released(task)
        if job.state=='completed':return job.result
        if state is None:
            rows=self.registry.accounting(claim.job_id).observations
            intents={r for o in rows for r in o.observation.receipts if r.kind=='m7-native-run-child-intent'}
            outcomes={r for o in rows for r in o.observation.receipts if r.kind=='m7-native-run-outcome'}
            if len(intents)!=1:raise NativeRunRecoveryRequired('No frozen child assignment; reconcile native startup/activation without restarting',claim)
            intent=next(iter(intents));value=self._read(intent)
            state={'claim':claim,'request':job.spec.inputs[1],'intent':intent,'handle':None,'runner':None,'pending':None,'outcome':None,'child':None}
            if len(outcomes)==1:
                state['outcome']=next(iter(outcomes));frozen=self._read(state['outcome'])
                if frozen['claim']!=claim.model_dump(mode='json') or frozen['request']!=state['request'].model_dump(mode='json') or frozen['intent']!=intent.model_dump(mode='json'):
                    raise ValueError('Frozen native run outcome differs from selected claim')
                state['child']=c.OperationResult.model_validate_json(canonical_json(frozen['child']))
            elif outcomes:raise ValueError('Multiple frozen native run outcomes')
            else:
                configuration=c.ArtifactRef.model_validate_json(canonical_json(value['runner_configuration']))
                selected=[self.registry.job(j) for j in self.registry.trace(configuration).jobs
                    if self.registry.job(j).spec.invocation=='native-child-'+claim.job_id]
                if len(selected)!=1 or selected[0].state!='completed':raise NativeRunRecoveryRequired('Child outcome is unknown; never generate a replacement episode',claim)
                state['child']=selected[0].result
            self._states[claim.job_id]=state
        if state['child'] is None and state.get('pending') is not None:
            pending=state['pending'];runner=state['runner']
            if isinstance(pending,(RunPublicationFailed,RunGradePending,RunSubmissionPending,RunFreezePending)):
                state['child']=runner.retry_publication(pending)
            else:state['child']=runner.recover(pending.claim)
        if state['child'] is None:raise NativeRunRecoveryRequired('Native phase has no retained child outcome; no implicit restart',claim)
        self._publish_child(state)
        return self._finish(state)

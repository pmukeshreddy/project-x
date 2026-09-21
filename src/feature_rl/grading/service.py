"""Source-only rebuild, fresh observations and external grading via reviewed M3.

This mechanical operation does not admit a task for release/training. In
particular it may grade BUILT diagnostic/control tasks without inspecting H.
"""
from datetime import datetime, timezone
import time
from feature_rl.artifacts import ArtifactError, ArtifactSizeLimitError, canonical_json
from feature_rl.contracts import (ArtifactRef, CostRecord, Disposition, EvidenceRecord,
    GradeRequest, OperationResult, Visibility, CommandSpec)
from feature_rl.environments import (EnvironmentRuntime, PreparedEnvironment, ExecutionRequest,
    BuildFailed, EnvironmentError, SourceRejected, DependencyUnavailable, EvidencePublicationFailed)
from feature_rl.submission import SubmissionService
from feature_rl.verifiers import load_verifier, materialize_manifest, parse_observations
from feature_rl.verifiers.language import compare, operand_value, check_value, decode_json
from feature_rl.verifiers.loader import read_local, read_bytes
from .models import GradeReceipt, CaseResult, AssertionResult
from .bootstrap import adapter_argv


class GradePublicationFailed(Exception):
    """Exact retained grade and accounting; retry publication without re-execution."""
    def __init__(self,receipt,costs,scope,runtime_publications=()):
        super().__init__('M4 grade publication failed; replay retained receipt without worker execution')
        self.receipt=receipt;self.costs=costs;self.scope=scope
        self.runtime_publications=runtime_publications


def read_grade(store,ref):
    return read_local(store,ref,GradeReceipt,'m4-grade-receipt',4*1024*1024)


class GradingService:
    def __init__(self,*,store,runtime,revision,max_wall_seconds=600.0):
        if not isinstance(runtime,EnvironmentRuntime) or runtime.store is not store:
            raise ValueError('reviewed M3 runtime using the same controller store required')
        if type(revision) is not str or len(revision) not in (40,64) or any(c not in '0123456789abcdef' for c in revision):
            raise ValueError('implementation revision required')
        if type(max_wall_seconds) is not float or not 0<max_wall_seconds<=3600:
            raise ValueError('bounded positive grading wall budget required')
        self.store=store;self.runtime=runtime;self.revision=revision;self.max_wall_seconds=max_wall_seconds
        self.submissions=SubmissionService(store=store,policy=runtime.policy)

    def select_task(self,checked):
        """Bind inert submission checks to this task's frozen repository policy."""
        from feature_rl.environments import SandboxPolicy
        from feature_rl.environments.profiles import validate_recipe_profile
        policies=[r for r in checked.recipe.provenance.inputs if r.kind=='sandbox-policy']
        if len(policies)!=1:raise ValueError('recipe must bind exactly one M3 sandbox policy')
        policy=SandboxPolicy.model_validate_json(self.runtime.read_bytes(policies[0],65536))
        if policy.model_dump(exclude={'image','profile'})!=self.runtime.base_policy.model_dump(exclude={'image','profile'}):
            raise ValueError('task runtime exceeds configured sandbox policy')
        validate_recipe_profile(checked.recipe,policy,self.store)
        self.submissions=SubmissionService(store=self.store,policy=policy)
        return PreparedEnvironment(recipe=checked.task.environment,policy=policies[0])

    def _publish(self,receipt,costs,scope):
        try:
            receipt=GradeReceipt.model_validate(receipt)
            ref=self.store.put_bytes(canonical_json(receipt.model_dump(mode='json')),'m4-grade-receipt',Visibility.PRIVATE)
        except Exception as exc:raise GradePublicationFailed(receipt,costs,scope) from exc
        evidence=EvidenceRecord(producer='feature_rl.grading',command=('GradingService.grade',receipt.task.sha256,
            receipt.submission.sha256,str(receipt.case_seed)),recorded_at=receipt.recorded_at,exit_status=0,
            artifacts=(ref,*receipt.runtime_evidence),revision=self.revision,scope=scope)
        return OperationResult(operation='grade',disposition=receipt.disposition,artifacts=(ref,),evidence=(evidence,),
            costs=costs,reason=receipt.reason)

    def retry_publication(self,error):
        if not isinstance(error,GradePublicationFailed):raise TypeError('grade publication recovery required')
        references=list(error.receipt.runtime_evidence)
        for index,pending in enumerate(error.runtime_publications):
            try:references.append(self.runtime.retry_publication(pending))
            except EvidencePublicationFailed as exc:
                data=error.receipt.model_dump(mode='json')
                data['runtime_evidence']=[r.model_dump(mode='json') for r in references]
                retained=GradeReceipt.model_validate_json(canonical_json(data))
                raise GradePublicationFailed(retained,error.costs,error.scope,(exc,*error.runtime_publications[index+1:])) from exc
        data=error.receipt.model_dump(mode='json');data['runtime_evidence']=[r.model_dump(mode='json') for r in references]
        return self._publish(GradeReceipt.model_validate_json(canonical_json(data)),error.costs,error.scope)

    def grade(self,task_version,submission,case_seed):
        request=GradeRequest(task_version=task_version,submission=submission,case_seed=case_seed)
        if case_seed>=2**63:raise ValueError('case seed exceeds supported 63-bit domain')
        start=time.monotonic();cpu_start=time.process_time();worker_wall=0.0
        failed_call=None;runtime_publications=[]
        verifier_ref=None;manifest_ref=None;source_ref=None;build_evidence=None;runtime_evidence=[];costs=[]
        checked=None;handle=None;results=[];cleanup=True
        disposition=Disposition.INVALID;reward=None;reason='grading did not complete'
        def timed(call,category):
            nonlocal worker_wall,failed_call
            t=time.monotonic()
            try:return call()
            except BaseException:
                failed_call=(category,time.monotonic()-t)
                raise
            finally:worker_wall+=time.monotonic()-t
        try:
            checked=load_verifier(self.store,task_version)
            prepared=self.select_task(checked)
            verifier_ref=checked.task.private_oracle
            manifest=materialize_manifest(checked,case_seed)
            results=[CaseResult(case_id=c.case_id,mandatory=c.mandatory,status='not_run',passed=None,
                assertions=(),evidence=None,reason='not executed') for c in manifest.cases]
            manifest_ref=self.store.put_bytes(canonical_json(manifest.model_dump(mode='json')),'m4-case-manifest',Visibility.PRIVATE)
            try:
                source=self.submissions.resolve(submission,checked.task.baseline,checked.contract.allowed_changes)
            except (SourceRejected,ArtifactSizeLimitError) as exc:
                disposition=Disposition.REJECTED;reward=0;reason='source submission rejected: '+str(exc)[:800]
            else:
                source_ref=self.store.put_bytes(source.to_tar(),'source-archive',Visibility.PRIVATE)
                self.runtime.recipe(prepared)
                handle=self.runtime.open_workspace(prepared,source=source_ref,role='candidate',allowed_changes=checked.contract.allowed_changes)
                try:
                    build=timed(lambda:self.runtime.build_snapshot(handle),'construction')
                except BuildFailed as exc:
                    build_evidence=exc.evidence;runtime_evidence.append(exc.evidence)
                    cleanup=exc.cleanup_verified
                    disposition=Disposition.REJECTED if exc.failure_category=='candidate' and cleanup else Disposition.INFRASTRUCTURE
                    if exc.failure_category=='unresolved' and cleanup:disposition=Disposition.INVALID
                    reward=0 if disposition==Disposition.REJECTED else None
                    reason='build '+exc.reason
                else:
                    build_evidence=build.evidence;runtime_evidence.append(build.evidence);costs.append(build.cost)
                    disposition=Disposition.SUCCESS;reward=1;reason='all required externally compared probes passed'
                    for index,(case,comparison) in enumerate(zip(manifest.cases,checked.comparisons)):
                        if time.monotonic()-start>=self.max_wall_seconds:
                            disposition=Disposition.REJECTED;reward=0;reason='declared total grading wall budget exceeded';break
                        stdin=canonical_json({'case_id':case.case_id,'inputs':case.model_dump(mode='json')['inputs']})
                        command=CommandSpec(argv=adapter_argv(checked.adapter,self.runtime.profile.environment),working_directory='/workspace',timeout_seconds=comparison.timeout_seconds)
                        output=timed(lambda:self.runtime.execute(handle,ExecutionRequest(command=command,stdin=stdin,save_source=False),build=build),'execution')
                        runtime_evidence.append(output.evidence);costs.append(output.cost)
                        cleanup=output.cleanup_verified
                        if not cleanup or output.failure_category in {'infrastructure','unresolved'}:
                            disposition=Disposition.INVALID if cleanup and output.failure_category=='unresolved' else Disposition.INFRASTRUCTURE
                            reward=None;reason='worker '+output.reason
                            results[index]=CaseResult(case_id=case.case_id,mandatory=case.mandatory,status='infrastructure_failure',
                                passed=None,assertions=(),evidence=output.evidence,reason=reason);break
                        # A CLI process may deliberately exit nonzero. JSON adapter
                        # failure and every resource termination are terminal zeros.
                        if output.reason not in {'completed','command_failed'} or (comparison.mode=='json' and output.exit_code!=0):
                            disposition=Disposition.REJECTED;reward=0;reason='candidate '+output.reason
                            results[index]=CaseResult(case_id=case.case_id,mandatory=case.mandatory,status='candidate_failure',
                                passed=None,assertions=(),evidence=output.evidence,reason=reason)
                            continue
                        try:
                            cap=checked.verifier.permissions.output_limit_bytes
                            if len(output.stdout)+len(output.stderr)>cap:raise ValueError('combined output byte cap')
                            if comparison.mode=='json':
                                observations=parse_observations(output.stdout,case.case_id,comparison.observations,cap)
                            else:
                                raw={'exit_code':output.exit_code,'stdout':output.stdout.decode('utf-8'),'stderr':output.stderr.decode('utf-8')}
                                observations={f.name:raw[f.name] for f in comparison.observations}
                                if any(not check_value(observations[f.name],f.type) for f in comparison.observations):raise ValueError('process observation type/size mismatch')
                            assertions=tuple(AssertionResult(assertion_id=a.assertion_id,requirement_ids=a.requirement_ids,
                                passed=compare(a.operator,observations[a.actual],operand_value(a.expected,case.inputs,observations))) for a in comparison.assertions)
                            passed=all(a.passed for a in assertions)
                            results[index]=CaseResult(case_id=case.case_id,mandatory=case.mandatory,status='completed',passed=passed,
                                assertions=assertions,evidence=output.evidence,reason='compared externally')
                            if case.mandatory and not passed:
                                disposition=Disposition.REJECTED;reward=0;reason='required comparison failed'
                        except (ValueError,KeyError,UnicodeError) as exc:
                            disposition=Disposition.REJECTED;reward=0;reason='observation protocol violation: '+str(exc)[:800]
                            results[index]=CaseResult(case_id=case.case_id,mandatory=case.mandatory,status='protocol_failure',passed=None,
                                assertions=(),evidence=output.evidence,reason=reason)
        except DependencyUnavailable as exc:
            disposition=Disposition.INVALID;reward=None;reason='offline dependency resolution unavailable: '+str(exc)[:800]
        except SourceRejected as exc:
            disposition=Disposition.REJECTED;reward=0;reason='candidate declarations rejected: '+str(exc)[:800]
        except EvidencePublicationFailed as exc:
            # Preserve M3's exact bounded pending payload. A permanent archive
            # outage raises replayable publication recovery, never drops logs.
            disposition=Disposition.INFRASTRUCTURE;reward=None;reason=str(exc)[:900]
            try:runtime_evidence.append(self.runtime.retry_publication(exc))
            except EvidencePublicationFailed as pending:runtime_publications.append(pending)
        except (ArtifactError,OSError,EnvironmentError) as exc:
            disposition=Disposition.INFRASTRUCTURE;reward=None;reason=type(exc).__name__+': '+str(exc)[:800]
        except ValueError as exc:
            disposition=Disposition.UNSUPPORTED;reward=None;reason='invalid or unsupported verifier: '+str(exc)[:800]
        finally:
            if handle is not None:
                try:self.runtime.close(handle)
                except Exception as exc:
                    cleanup=False;disposition=Disposition.INFRASTRUCTURE;reward=None
                    reason='workspace cleanup unverified: '+type(exc).__name__+': '+str(exc)[:700]
        if failed_call is not None:
            category,elapsed=failed_call
            observed_cpu=None
            if category=='construction' and build_evidence is not None:
                # The failed M3 call has no BuildResult.cost. Retain its actual
                # cgroup sample when available plus the measured call interval.
                try:
                    raw=decode_json(read_bytes(self.store,build_evidence,32*1024*1024),32*1024*1024)
                    observed_cpu=raw.get('maximum_cpu_seconds')
                    if type(observed_cpu) not in (float,int):observed_cpu=None
                    elif observed_cpu is not None:observed_cpu=float(observed_cpu)
                except (ArtifactError,OSError,ValueError):
                    observed_cpu=None  # Keep the observed call wall time; CPU is unavailable.
            costs.append(CostRecord(category=category,wall_seconds=elapsed,cpu_seconds=observed_cpu,
                gpu_seconds=None,input_tokens=None,output_tokens=None,human_minutes=None,usd=None,measurement='partial',
                note='Failed runtime call: measured external wall interval; cgroup CPU retained when a build receipt exists'))
        costs.append(CostRecord(category='verifier',wall_seconds=max(0.0,time.monotonic()-start-worker_wall),
            cpu_seconds=max(0.0,time.process_time()-cpu_start),gpu_seconds=None,input_tokens=None,output_tokens=None,
            human_minutes=None,usd=None,measurement='partial',note='Controller wall excludes measured runtime call intervals; controller CPU only; worker costs separate'))
        receipt=GradeReceipt(version='m4-grade-v1',task=task_version,submission=submission,verifier=verifier_ref,
            case_seed=case_seed,manifest=manifest_ref,source=source_ref,disposition=disposition,reward=reward,reason=reason,
            expected_case_ids=tuple(c.case_id for c in results),cases=tuple(results),build_evidence=build_evidence,
            runtime_evidence=tuple(runtime_evidence),cleanup_verified=cleanup,implementation_revision=self.revision,
            recorded_at=datetime.now(timezone.utc))
        scope='real_integration' if runtime_evidence else 'source_inspection'
        if runtime_publications:raise GradePublicationFailed(receipt,tuple(costs),scope,tuple(runtime_publications))
        return self._publish(receipt,tuple(costs),scope)

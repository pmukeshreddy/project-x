"""Execute qualification through M4/M3 and freeze admission evidence.

Registry job selection, not arbitrary stored report booleans, is the operation
origin. This module never signs human evidence or constructs a solver package.
"""
from datetime import datetime, timezone
import uuid
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, ArtifactError, canonical_json
from feature_rl.environments import ExecutionRequest, CommandSpec, EnvironmentError
from feature_rl.grading import GradingService
from feature_rl.registry import Registry, JobSpec, CostObservation
from feature_rl.verifiers import load_verifier
from feature_rl.verifiers.loader import read_local, read_bytes
from .models import (QualificationRejected, QualificationPolicy, RunBinding, ResetReceipt,
    ReferenceProjection, QualificationPublicationFailed, QualificationUnavailable)
from .projection import derive_reference
from .controls import assess_outcome, validate_control_plan
from .evidence import put_record, unknown_cost, collapse_costs, evidence, validate_grade, validate_reset, check_consumed, assert_reference_determinism


class QualificationService:
    def __init__(self,*,store,registry,grader,builder,revision,policy=None):
        if not isinstance(store,ArtifactStore) or store.role!=c.ActorRole.CONTROLLER or not isinstance(registry,Registry) or registry.store is not store:
            raise TypeError('M5 requires the same actual controller store and Registry')
        if not isinstance(grader,GradingService) or grader.store is not store:raise TypeError('actual same-store M4 GradingService required')
        if builder is not None:
            from feature_rl.pipeline.build import TaskBuilder
            if not isinstance(builder,TaskBuilder) or builder.store is not store or builder.registry is not registry:
                raise TypeError('actual same-store M6 TaskBuilder required')
        if type(revision) is not str or len(revision) not in (40,64) or any(ch not in '0123456789abcdef' for ch in revision):raise ValueError('exact M5 implementation revision required')
        self.store=store;self.registry=registry;self.grader=grader;self.builder=builder;self.revision=revision
        self.policy=QualificationPolicy() if policy is None else QualificationPolicy.model_validate(policy)
        self.policy_ref=put_record(store,self.policy,'m5-qualification-policy')
        self.configuration=put_record(store,{'version':'m5-configuration-v1','policy':self.policy_ref.model_dump(mode='json'),
            'grading_revision':grader.revision,'runtime_revision':grader.runtime.revision,
            'builder_revision':builder.revision if builder is not None else None},'m5-qualification-configuration')
        self.registry.register(self.policy_ref)
        self.registry.register(self.configuration,dependencies=(self.policy_ref,))

    def _job(self,task,invocation,*,configuration=None,inputs=None,operation='qualify'):
        return self.registry.enqueue(JobSpec(operation=operation,inputs=inputs or (task,),configuration=configuration or self.configuration,
            implementation=self.revision,invocation=invocation,attempt_limit=1))

    def _claim(self,job):
        if job.state=='running':raise QualificationUnavailable('qualification attempt already running; it cannot execute twice',self.registry.attempts(job.job_id)[-1].claim)
        if job.state!='queued':raise QualificationUnavailable('qualification attempt is not queued')
        return self.registry.claim(job.job_id,owner='feature_rl.qualification',claim_key=uuid.uuid4().hex)

    def _complete(self,claim,result):
        try:
            normalized=result.model_copy(update={'costs':collapse_costs(result.costs)})
            observation=self.registry.reconcile(claim,CostObservation(source='m5-operation',upstream_attempt_id=claim.attempt_id,
                revision=1,receipts=normalized.artifacts,costs=normalized.costs))
            return self.registry.complete(claim,normalized,observations=(observation.observation_id,)).result
        except Exception as exc:
            raise QualificationPublicationFailed('Qualification publication failed; do not repeat execution',claim=claim) from exc

    def qualify(self,task_version):
        task_version=c.ArtifactRef.model_validate(task_version)
        job=self._job(task_version,'m5-qualify')
        self.registry.assert_usable(task_version)
        if job.state=='completed':return job.result
        claim=self._claim(job)
        return self._execute(task_version,claim)

    def _execute(self,task_version,claim):
        checked=None;projection_ref=None;bindings=[];assessments={};costs=[];issues=[]
        started=datetime.now(timezone.utc);seen=set();signatures={}
        try:
            checked=load_verifier(self.store,task_version)
            self.grader.select_task(checked)
            if checked.task.state!=c.TaskState.BUILT or checked.task.qualification is not None:
                raise QualificationRejected('invalid_evidence','qualification requires exact unqualified BUILT task')
            if any(a.disposition=='unresolved' for a in checked.contract.ambiguities):
                raise QualificationRejected('ambiguous_requirement','unresolved contract ambiguity')
            if self.builder is None:raise QualificationRejected('provisional','actual final package validator is unavailable')
            self.builder.solver_package(task_version)
            missing,_=validate_control_plan(checked)
            if missing:raise QualificationRejected('provisional','; '.join(missing))
            projection=derive_reference(self.store,task_version,self.grader.submissions.policy)
            projection_ref=put_record(self.store,projection,'m5-reference-projection')
            self.registry.register(projection_ref,dependencies=(task_version,projection.submission,projection.projected_source))
            from .admission import expected_runs
            runs,_=expected_runs(self,checked,projection)
            for name,submission,seed,mode,targets,reset in runs:
                if (datetime.now(timezone.utc)-started).total_seconds()>=self.policy.max_wall_seconds:
                    raise QualificationRejected('budget_exhausted','qualification wall budget reached')
                binding,result,receipt=self._run(checked,projection_ref,submission,seed,name,mode,targets,claim,reset,seen)
                bindings.append(binding);costs.extend(result.costs)
                outcome=assess_outcome(checked,receipt,mode,targets)
                assessments[name]=c.RunAssessment(name=name,subject=task_version,
                    disposition=c.Disposition.SUCCESS if outcome.passed else c.Disposition.REJECTED,
                    passed=outcome.passed,requirement_ids=targets,reason=outcome.detail,
                    evidence=(evidence(binding,self.revision,('QualificationService.execute',name,task_version.sha256),scope='real_integration'),))
                if name=='baseline_absence':
                    healthy=assess_outcome(checked,receipt,'baseline_health',())
                    assessments['baseline_health']=c.RunAssessment(name='baseline_health',subject=task_version,
                        disposition=c.Disposition.SUCCESS if healthy.passed else c.Disposition.REJECTED,passed=healthy.passed,
                        requirement_ids=tuple(r.requirement_id for r in checked.contract.compatibility_obligations if r.mandatory),
                        reason=healthy.detail,evidence=assessments[name].evidence)
                    if not healthy.passed:raise QualificationRejected(healthy.code,'baseline_health: '+healthy.detail)
                if not outcome.passed:raise QualificationRejected(outcome.code,name+': '+outcome.detail)
                if name in {'fresh_0','reset_0'}:assert_reference_determinism(self.store,checked,receipt,signatures)
        except QualificationRejected as exc:issues.append(exc.code+': '+exc.detail)
        except (ArtifactError,ValueError,EnvironmentError) as exc:issues.append('environment_failure: '+type(exc).__name__+': '+str(exc)[:1200])
        frozen_time=datetime.now(timezone.utc)
        elapsed=(frozen_time-started).total_seconds()
        if elapsed<0 or elapsed>self.policy.max_wall_seconds:issues.append('budget_exhausted: qualification wall budget exceeded or clock moved backwards')
        return self._publish_qualification(claim,{'task':task_version,'projection':projection_ref,'bindings':tuple(bindings),
            'assessments':assessments,'costs':tuple(costs),'issues':tuple(sorted(set(issues))),
            'recorded_at':frozen_time,'wall_seconds':max(0.0,elapsed)})

    def _publish_qualification(self,claim,frozen):
        from .models import QualificationSummary
        from .controls import disposition_for
        task_version=frozen['task'];projection_ref=frozen['projection'];bindings=frozen['bindings']
        assessments=frozen['assessments'];issues=frozen['issues'];frozen_time=frozen['recorded_at']
        summary=put_record(self.store,QualificationSummary(task=task_version,policy=self.policy_ref,projection=projection_ref,
            bindings=bindings,issues=issues,qualification_job=claim.job_id,wall_seconds=frozen['wall_seconds']),'m5-qualification-summary')
        self.registry.register(summary,dependencies=(task_version,self.policy_ref,*bindings,*([projection_ref] if projection_ref else [])))
        ev=evidence(summary,self.revision,('QualificationService.qualify',task_version.sha256),scope='real_integration' if bindings else 'source_inspection').model_copy(update={'recorded_at':frozen_time})
        disposition=disposition_for(issues)
        report=c.QualificationReport(kind='QualificationReport',schema_version=1,visibility=c.Visibility.PRIVATE,
            provenance=c.Provenance(producer='feature_rl.qualification',producer_version=self.revision,created_at=frozen_time,
                inputs=(task_version,self.policy_ref,summary),evidence=(ev,)),costs=collapse_costs((*frozen['costs'],unknown_cost())),
            task=task_version,disposition=disposition,baseline_health=assessments.get('baseline_health'),
            baseline_absence=assessments.get('baseline_absence'),reference_run=assessments.get('fresh_0'),
            controls=tuple(v for name,v in assessments.items() if name.startswith('control_')),
            fresh_runs=tuple(v for name,v in assessments.items() if name.startswith('fresh_')),
            interrupted_reset_runs=tuple(v for name,v in assessments.items() if name.startswith('reset_')),
            rejection_reasons=issues,policy_version=self.policy.policy_id)
        report_ref=self.store.put_artifact(report)
        result=c.OperationResult(operation='qualify',disposition=disposition,artifacts=(report_ref,summary),
            evidence=(ev,),costs=report.costs,reason='; '.join(issues) or 'Baseline, gold, wrong implementations and clean reset passed')
        if disposition==c.Disposition.SUCCESS:
            from .admission import validate_pending_admission
            self.registry.register(report_ref)
            validate_pending_admission(self,result,claim)
        return self._complete(claim,result)

    def _source_dependencies(self,checked,submission,*,register=False):
        from feature_rl.submission.source import Submission
        from feature_rl.verifiers.language import decode_json
        try:
            value=Submission.model_validate_json(canonical_json(decode_json(
                read_bytes(self.store,submission,65536,'m4-submission'),65536)))
        except ValueError as exc:
            raise QualificationRejected('invalid_evidence','invalid qualification submission manifest') from exc
        if (value.baseline!=checked.task.baseline or value.changes.kind!='m4-source-delta'
                or value.changes.encoding!='bytes'):
            raise QualificationRejected('invalid_evidence','qualification submission baseline or source delta mismatch')
        refs=(checked.task.baseline,value.changes)
        check_consumed(self.registry,refs,register=register)
        return refs

    def _run(self,checked,projection_ref,submission,seed,name,mode,targets,parent_claim,reset,seen):
        source_refs=self._source_dependencies(checked,submission,register=True)
        config=put_record(self.store,{'version':'m5-run-configuration-v2','configuration':self.configuration.model_dump(mode='json'),
            'projection':projection_ref.model_dump(mode='json'),'submission':submission.model_dump(mode='json'),
            'source_dependencies':[ref.model_dump(mode='json') for ref in source_refs],
            'seed':seed,'name':name,'mode':mode,'targets':list(targets),'reset':reset},'m5-run-configuration')
        self.registry.register(config,dependencies=tuple(dict.fromkeys((self.configuration,projection_ref,submission,*source_refs))))
        self.registry.assert_usable(config)
        job=self._job(checked.task_ref,'m5-run:'+parent_claim.job_id+':'+name,configuration=config,operation='grade')
        if job.state=='completed':result=job.result
        else:
            claim=self._claim(job)
            reset_ref=None;extra_costs=()
            try:
                if reset:reset_ref,extra_costs=self._reset(checked,projection_ref,submission)
                result=self.grader.grade(checked.task_ref,submission,seed)
            except QualificationRejected:
                raise
            except Exception as exc:
                raise QualificationUnavailable('Grading/reset attempt is incomplete; execution cannot be repeated',claim) from exc
            if reset_ref is not None:
                result=c.OperationResult(operation='grade',disposition=result.disposition,artifacts=(*result.artifacts,reset_ref),
                    evidence=result.evidence,costs=(*extra_costs,*result.costs),reason=result.reason)
            result=self._complete(claim,result)
        receipt,ids=validate_grade(self.store,checked,submission,seed,result,self.grader,seen=seen)
        reset_refs=[ref for ref in result.artifacts if ref.kind=='m5-reset']
        if reset and len(reset_refs)!=1:raise QualificationRejected('invalid_evidence','reset grade lacks exact reset receipt')
        if reset:
            validate_reset(self.store,checked,projection_ref,reset_refs[0],self.grader,seen=seen)
        binding=RunBinding(name=name,task=checked.task_ref,projection=projection_ref,submission=submission,seed=seed,
            grade=result.artifacts[0],mode=mode,targets=targets,operation_ids=ids,grade_job=job.job_id,reset=reset_refs[0] if reset_refs else None)
        ref=put_record(self.store,binding,'m5-run-binding')
        self.registry.register(ref,dependencies=tuple(dict.fromkeys((checked.task_ref,projection_ref,submission,*source_refs,config,*result.artifacts))))
        return ref,result,receipt

    def _reset(self,checked,projection_ref,submission):
        from .evidence import reset_probe, RESET_PROBE_BYTES
        projection=read_local(self.store,projection_ref,ReferenceProjection,'m5-reference-projection',1024*1024)
        prepared=self.grader.select_task(checked)
        runtime=self.grader.runtime
        handle=runtime.open_workspace(prepared,
            source=projection.projected_source,role='candidate',allowed_changes=checked.contract.allowed_changes)
        try:
            _,_,_,initial,initial_source=runtime.workspace(handle)
            # A fixed controller diagnostic mutates only an allowed source file;
            # the original confirmed source must be restored before grading.
            path,_=reset_probe(initial_source,checked.contract.allowed_changes,runtime.profile)
            mutation=runtime.execute_development(handle,ExecutionRequest(command=CommandSpec(
                argv=('/usr/local/bin/python','-I','-c','import pathlib,sys\nwith pathlib.Path(sys.argv[1]).open("ab") as stream: stream.write(sys.stdin.buffer.read())','/workspace/source/'+path),
                working_directory='/workspace',timeout_seconds=2.0),stdin=RESET_PROBE_BYTES,save_source=True))
            if mutation.reason!='completed' or not mutation.cleanup_verified or mutation.saved_source.artifact==initial.artifact:
                raise QualificationRejected('environment_failure','reset canary was not actually saved with verified cleanup')
            before=runtime.workspace(handle,bind=False)[0]['generation']
            restored=runtime.reset(handle)
            after=runtime.workspace(handle,bind=False)[0]['generation']
            if restored!=initial or after!=before+1:
                raise QualificationRejected('environment_failure','reset did not restore exact initial saved source and advance generation')
            reset_submission=self.grader.submissions.from_saved(checked.task.baseline,restored.artifact,checked.contract.allowed_changes)
            if reset_submission!=submission:raise QualificationRejected('invalid_evidence','reset snapshot changes the assigned reference submission')
        finally:runtime.close(handle)
        record=ResetReceipt(task=checked.task_ref,projection=projection_ref,workspace_id=handle.workspace_id,
            mutation=mutation.evidence,mutation_operation=mutation.operation_id,initial_source=initial.artifact,
            reset_source=restored.artifact,generation_before=before,generation_after=after,cleanup_verified=True,recorded_at=datetime.now(timezone.utc))
        ref=put_record(self.store,record,'m5-reset')
        self.registry.register(ref,dependencies=(checked.task_ref,projection_ref,mutation.evidence,restored.artifact))
        return ref,(mutation.cost,)


    def verify_accepted(self,task_ref,report_ref):
        from .admission import verify_accepted
        return verify_accepted(self,task_ref,report_ref)

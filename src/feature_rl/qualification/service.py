"""Execute qualification through M4/M3 and freeze external-review subjects.

Registry job selection, not arbitrary stored report booleans, is the operation
origin. This module never signs human evidence or constructs a solver package.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import time
import uuid
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, ArtifactError, canonical_json
from feature_rl.environments import PreparedEnvironment, ExecutionRequest, CommandSpec, EnvironmentError, SourceArchive
from feature_rl.grading import GradingService, GradePublicationFailed
from feature_rl.registry import Registry, JobSpec, CostObservation, RegistryError
from feature_rl.verifiers import load_verifier
from feature_rl.verifiers.loader import read_local, read_bytes
from .models import (QualificationRejected, QualificationPolicy, RepairHistory, ReviewRequest,
    RunBinding, ResetReceipt, ReferenceProjection, QualificationPublicationFailed, QualificationRecoveryRequired,
    CompletionPending, FrozenPublication, GradePending, RunCompletionPending)
from .projection import derive_reference
from .controls import assess_outcome, validate_control_plan, validate_repairs
from .evidence import put_record, unknown_cost, collapse_costs, evidence, validate_grade, validate_reset, check_consumed


class QualificationService:
    def __init__(self,*,store,registry,grader,builder,revision,policy=None,attestation_verifier=None):
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
        self.attestation_verifier=attestation_verifier
        self.policy_ref=put_record(store,self.policy,'m5-qualification-policy')
        self.configuration=put_record(store,{'version':'m5-configuration-v1','policy':self.policy_ref.model_dump(mode='json'),
            'grading_revision':grader.revision,'runtime_revision':grader.runtime.revision,
            'builder_revision':builder.revision if builder is not None else None},'m5-qualification-configuration')
        policy_inputs=tuple(dict.fromkeys((*tuple(r for r in (self.policy.repair_history,) if r is not None),
            *(r for d in self.policy.controls for e in (*d.evidence,*d.independence_evidence) for r in e.artifacts))))
        self.registry.register(self.policy_ref,dependencies=policy_inputs)
        self.registry.register(self.configuration,dependencies=(self.policy_ref,))

    def _job(self,task,invocation,*,configuration=None,inputs=None,operation='qualify'):
        return self.registry.enqueue(JobSpec(operation=operation,inputs=inputs or (task,),configuration=configuration or self.configuration,
            implementation=self.revision,invocation=invocation,attempt_limit=3))

    def _claim(self,job):
        if job.state=='running':raise QualificationRecoveryRequired('qualification attempt already running; recover/reconcile without redispatch',self.registry.attempts(job.job_id)[-1].claim)
        if job.state!='queued':raise QualificationRecoveryRequired('qualification attempt requires explicit reconciled retry')
        return self.registry.claim(job.job_id,owner='feature_rl.qualification',claim_key=uuid.uuid4().hex)

    def _complete(self,claim,result):
        try:
            original=put_record(self.store,{'version':'m5-operation-costs-v1','claim':claim.model_dump(mode='json'),
                'operation':result.operation,'outputs':[ref.model_dump(mode='json') for ref in result.artifacts],
                'costs':[value.model_dump(mode='json') for value in result.costs]},'m5-operation-costs')
            self.registry.register(original,dependencies=result.artifacts)
            snapshots=[x for x in self.registry.accounting(claim.job_id).observations if x.attempt_id==claim.attempt_id]
            if any(x.observation.source!='m5-operation' for x in snapshots):
                raise QualificationRecoveryRequired('unexpected accounting source needs supervisor reconciliation',claim)
            latest=max(snapshots,key=lambda x:x.observation.revision) if snapshots else None
            intent_refs=tuple(r for x in snapshots for r in x.observation.receipts if r.kind=='m5-operation-intent')
            artifacts=tuple(dict.fromkeys((*result.artifacts,original,*intent_refs)))
            normalized=c.OperationResult(operation=result.operation,disposition=result.disposition,
                artifacts=artifacts,evidence=result.evidence,costs=collapse_costs(result.costs),reason=result.reason)
            selected=self.registry.job(claim.job_id)
            if selected.state=='completed':
                if selected.result!=normalized:raise QualificationRecoveryRequired('completed result differs from retained publication',claim)
                return selected.result
            if latest and latest.observation.receipts==artifacts and latest.observation.costs==normalized.costs:
                observation=latest
            else:
                observation=self.registry.reconcile(claim,CostObservation(source='m5-operation',upstream_attempt_id=claim.attempt_id,
                    revision=latest.observation.revision+1 if latest else 1,receipts=artifacts,costs=normalized.costs))
            return self.registry.complete(claim,normalized,observations=(observation.observation_id,)).result
        except QualificationRecoveryRequired:raise
        except Exception as exc:
            raise QualificationPublicationFailed('retain exact result and costs for publication-only recovery',
                pending=CompletionPending(claim,result),claim=claim) from exc

    def _intent(self,claim):
        """Durably expose unknown attempted work before any runtime invocation."""
        try:
            snapshots=[x for x in self.registry.accounting(claim.job_id).observations if x.attempt_id==claim.attempt_id]
            refs={r for x in snapshots for r in x.observation.receipts if r.kind=='m5-operation-intent'}
            if refs:
                if len(refs)!=1:raise QualificationRecoveryRequired('attempt has conflicting execution intents',claim)
                value=__import__('json').loads(read_bytes(self.store,next(iter(refs)),65536,'m5-operation-intent',True))
                if value['claim']!=claim.model_dump(mode='json'):raise QualificationRecoveryRequired('intent claim mismatch',claim)
                return datetime.fromisoformat(value['started_at'])
            if snapshots:raise QualificationRecoveryRequired('old attempt lacks a durable pre-execution intent',claim)
            started=datetime.now(timezone.utc)
            ref=put_record(self.store,{'version':'m5-operation-intent-v1','claim':claim.model_dump(mode='json'),
                'started_at':started.isoformat()},'m5-operation-intent')
            self.registry.reconcile(claim,CostObservation(source='m5-operation',upstream_attempt_id=claim.attempt_id,
                revision=1,receipts=(ref,),costs=(unknown_cost(note='Attempt dispatched; incurred costs remain unknown until reconciled'),)))
            return started
        except QualificationRecoveryRequired:raise
        except Exception as exc:raise QualificationRecoveryRequired('execution intent publication ambiguous; recover Registry before dispatch',claim) from exc

    def retry_publication(self,error):
        if not isinstance(error,QualificationPublicationFailed):raise TypeError('M5 retained publication failure required')
        pending=error.pending
        if isinstance(pending,RunCompletionPending):
            try:self.retry_publication(pending.completion)
            except QualificationPublicationFailed as exc:
                raise QualificationPublicationFailed('retain grade completion and parent across repeated storage failure',
                    pending=RunCompletionPending(pending.parent_claim,exc),claim=pending.parent_claim) from exc
            return self.recover(pending.parent_claim)
        if isinstance(pending,CompletionPending):
            if pending.result.operation=='qualify' and pending.result.disposition==c.Disposition.SUCCESS:
                from .admission import validate_pending_admission
                validate_pending_admission(self,pending.result)
            selected=self._complete(pending.claim,pending.result)
            if selected.operation=='qualify' and selected.artifacts[0].kind=='QualificationReport':
                report=self.store.get_artifact(selected.artifacts[0],max_envelope_bytes=4*1024*1024)
                if report.provenance.producer=='feature_rl.qualification':
                    self._quarantine_defect(report.task,report.provenance.inputs[-1],pending.claim.job_id,report.rejection_reasons)
            return selected
        if isinstance(pending,GradePending):
            try:result=self.grader.retry_publication(pending.pending)
            except GradePublicationFailed as exc:
                raise QualificationPublicationFailed('retain remaining M4 publication without another execution',pending=GradePending(
                    pending.parent_claim,pending.grade_claim,exc,pending.reset_ref,pending.reset_costs),claim=pending.parent_claim) from exc
            if pending.reset_ref is not None:
                result=c.OperationResult(operation='grade',disposition=result.disposition,artifacts=(*result.artifacts,pending.reset_ref),
                    evidence=result.evidence,costs=(*pending.reset_costs,*result.costs),reason=result.reason)
            try:self._complete(pending.grade_claim,result)
            except QualificationPublicationFailed as exc:
                raise QualificationPublicationFailed('retain published grade and parent continuation',
                    pending=RunCompletionPending(pending.parent_claim,exc),claim=pending.parent_claim) from exc
            return self.recover(pending.parent_claim)
        if isinstance(pending,FrozenPublication):
            try:
                if pending.purpose=='qualification':return self._publish_qualification(pending.claim,pending.payload)
                from .admission import publish_verification,publish_admission
                if pending.purpose=='human_verification':return publish_verification(self,pending.claim,pending.payload)
                if pending.purpose=='admission':return publish_admission(self,pending.claim,pending.payload)
            except QualificationPublicationFailed:raise
            except Exception as exc:raise error from exc
        raise TypeError('unknown M5 publication payload')

    def qualify(self,task_version):
        task_version=c.ArtifactRef.model_validate(task_version)
        job=self._job(task_version,'m5-qualify')
        self.registry.assert_usable(task_version)
        if job.state=='completed':return job.result
        claim=self._claim(job)
        return self._execute(task_version,claim)

    def recover(self,claim):
        job=self.registry.job(claim.job_id)
        if job.state=='completed':return job.result
        attempts=self.registry.attempts(claim.job_id)
        if not any(a.claim==claim and a.state=='running' for a in attempts) or job.spec.invocation!='m5-qualify':
            raise QualificationRecoveryRequired('current M5 qualification claim required',claim)
        # Completed subjobs are reused. Any unobserved running subjob refuses
        # redispatch and preserves its separate attempt identity.
        return self._execute(job.spec.inputs[0],claim)

    def _history(self,checked):
        policy=self.policy
        if policy.repair_history is None or policy.repair_history_job is None or policy.factory_revision is None:
            return None,'global candidate repair history is missing/unverified'
        history=read_local(self.store,policy.repair_history,RepairHistory,'m5-repair-history',1024*1024)
        job=self.registry.job(policy.repair_history_job)
        pair=self.store.get_artifact(checked.task.source_pair,max_envelope_bytes=1024*1024)
        if job.state!='completed' or job.result is None or policy.repair_history not in job.result.artifacts or job.spec.implementation!=policy.factory_revision or job.spec.operation!='construct' or pair.candidate not in job.spec.inputs or policy.repair_history_job not in self.registry.trace(pair.candidate).jobs:
            raise QualificationRejected('invalid_evidence','repair history is not selected by the configured M6 candidate operation')
        for ref in history.journal_refs:self.registry.assert_usable(ref)
        count=validate_repairs(history,pair.candidate,checked.recipe.neutral_repairs)
        return count,None if count is not None else 'global candidate repair history is explicitly incomplete'

    def _execute(self,task_version,claim):
        checked=None;projection_ref=None;bindings=[];assessments={};costs=[];issues=[];count=None
        started=self._intent(claim);seen=set();calls=0;signatures={}
        def run(name,submission,seed,mode,targets,reset=False):
            nonlocal calls
            calls+=1
            if calls>self.policy.max_grade_calls or (datetime.now(timezone.utc)-started).total_seconds()>=self.policy.max_wall_seconds:
                raise QualificationRejected('budget_exhausted','declared qualification grade-call/wall budget reached')
            binding,result,receipt=self._run(checked,projection_ref,submission,seed,name,mode,targets,claim,reset,seen)
            bindings.append(binding);costs.extend(result.costs)
            if name.startswith(('fresh_','reset_')):
                signature=tuple((item.status,tuple(a.passed for a in item.assertions)) for item in receipt.cases)
                if seed in signatures and signatures[seed]!=signature:issues.append('flaky_task: repeated reference inputs produced different outcomes')
                signatures[seed]=signature
            outcome=assess_outcome(checked,receipt,mode,targets,store=self.store)
            if not outcome.passed:issues.append(outcome.code+': '+name+': '+outcome.detail)
            gate=c.RunAssessment(name=name,subject=task_version,disposition=c.Disposition.SUCCESS if outcome.passed else c.Disposition.REJECTED,
                passed=outcome.passed,requirement_ids=targets,reason=outcome.detail,
                evidence=(evidence(binding,self.revision,('QualificationService.execute',name,task_version.sha256),scope='real_integration'),))
            assessments[name]=gate
            return receipt
        try:
            checked=load_verifier(self.store,task_version)
            if checked.task.state!=c.TaskState.BUILT or checked.task.qualification is not None:
                raise QualificationRejected('invalid_evidence','qualification requires exact unqualified BUILT T0')
            if any(a.disposition=='unresolved' for a in checked.contract.ambiguities):
                raise QualificationRejected('ambiguous_requirement','unresolved contract ambiguity')
            if self.builder is None:raise QualificationRejected('provisional','actual M6 final package validator is unavailable')
            self.builder.solver_package(task_version)
            projection=derive_reference(self.store,task_version,self.grader.runtime.policy)
            projection_ref=put_record(self.store,projection,'m5-reference-projection')
            self.registry.register(projection_ref,dependencies=(task_version,projection.submission,projection.projected_source))
            missing,targets=validate_control_plan(checked,self.policy);issues.extend(missing)
            count,history_issue=self._history(checked)
            if history_issue:issues.append(history_issue)
            baseline=self.grader.submissions.create(checked.task.baseline,SourceArchive({}).to_tar(),(),checked.contract.allowed_changes)
            first=run('baseline_absence',baseline,self.policy.fresh_seeds[0],'semantic_negative',targets)
            healthy=assess_outcome(checked,first,'baseline_health',())
            if not healthy.passed:issues.append(healthy.code+': baseline_health: '+healthy.detail)
            assessments['baseline_health']=c.RunAssessment(name='baseline_health',subject=task_version,
                disposition=c.Disposition.SUCCESS if healthy.passed else c.Disposition.REJECTED,passed=healthy.passed,
                requirement_ids=tuple(r.requirement_id for r in checked.contract.compatibility_obligations if r.mandatory),
                reason=healthy.detail,evidence=assessments['baseline_absence'].evidence)
            for index,seed in enumerate(self.policy.fresh_seeds):run('fresh_'+str(index),projection.submission,seed,'positive',())
            by_id={d.control_id:d for d in self.policy.controls}
            for control in checked.verifier.controls:
                diagnosis=by_id.get(control.control_id)
                if diagnosis is None or diagnosis.validity in {'equivalent','unresolved'}:continue
                run('control_'+control.control_id,control.patch,self.policy.fresh_seeds[0],diagnosis.mode,diagnosis.targets)
            for index,seed in enumerate(self.policy.reset_seeds):run('reset_'+str(index),projection.submission,seed,'positive',(),True)
        except QualificationRejected as exc:issues.append(exc.code+': '+exc.detail)
        except (ArtifactError,ValueError,EnvironmentError) as exc:issues.append('environment_failure: '+type(exc).__name__+': '+str(exc)[:1200])
        frozen_time=datetime.now(timezone.utc)
        elapsed=(frozen_time-started).total_seconds()
        if elapsed<0 or elapsed>self.policy.max_wall_seconds:issues.append('budget_exhausted: qualification wall budget exceeded or clock moved backwards')
        issues.append('unverified_human_review: external authenticated human review is required')
        frozen={'task':task_version,'projection':projection_ref,'bindings':tuple(bindings),'assessments':assessments,
            'costs':tuple(costs),'issues':tuple(sorted(set(issues))),'count':count,'recorded_at':frozen_time,
            'wall_seconds':max(0.0,elapsed),'challenge':uuid.uuid4().hex+uuid.uuid4().hex}
        return self._publish_qualification(claim,frozen)

    def _publish_qualification(self,claim,frozen):
        try:
            from .models import QualificationSummary
            from .controls import disposition_for
            task_version=frozen['task'];projection_ref=frozen['projection'];bindings=frozen['bindings']
            assessments=frozen['assessments'];issues=frozen['issues'];count=frozen['count'];frozen_time=frozen['recorded_at']
            summary=put_record(self.store,QualificationSummary(task=task_version,policy=self.policy_ref,projection=projection_ref,
                bindings=bindings,issues=issues,repair_count=count,qualification_job=claim.job_id,
                wall_seconds=frozen['wall_seconds']),'m5-qualification-summary')
            dependencies=tuple(dict.fromkeys((task_version,self.policy_ref,*bindings,*([projection_ref] if projection_ref else []))))
            self.registry.register(summary,dependencies=dependencies)
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
                human_reviews=(),rejection_reasons=issues,repair_attempts=count or 0,policy_version=self.policy.policy_id)
            report_ref=self.store.put_artifact(report)
            outputs=[report_ref,summary]
            if issues==('unverified_human_review: external authenticated human review is required',) and count is not None:
                request=ReviewRequest(task=task_version,report=report_ref,policy=self.policy_ref,challenge=frozen['challenge'],
                    issued_at=frozen_time,expires_at=frozen_time+timedelta(seconds=self.policy.review_seconds),qualification_job=claim.job_id)
                request_ref=put_record(self.store,request,'m5-review-request')
                self.registry.register(request_ref,dependencies=(task_version,report_ref,self.policy_ref))
                outputs.append(request_ref)
            result=c.OperationResult(operation='qualify',disposition=disposition,artifacts=tuple(outputs),
                evidence=(ev,),costs=report.costs,reason='; '.join(issues))
            selected=self._complete(claim,result)
            self._quarantine_defect(task_version,summary,claim.job_id,issues)
            return selected
        except QualificationPublicationFailed:raise
        except Exception as exc:
            raise QualificationPublicationFailed('retain frozen qualification package without new controls, challenge or timestamps',
                pending=FrozenPublication(claim,frozen,'qualification'),claim=claim) from exc

    def _quarantine_defect(self,task,summary,job_id,issues):
        defects=tuple(issue for issue in issues if issue.split(':',1)[0] in {'false_acceptance','false_rejection','oracle_disagreement','flaky_task'})
        if defects:self.registry.quarantine(task,notice_id='m5:'+job_id,reason='; '.join(defects)[:4096],evidence=(summary,))

    def affected_versions(self,task_version):
        """Current registry trace, including active defect notices and dependents."""
        return self.registry.trace(task_version)

    def _source_dependencies(self,checked,submission,*,register=False):
        from feature_rl.submission.source import Submission
        from feature_rl.verifiers.language import decode_json
        refs=[checked.task.baseline]
        value=None
        if submission.kind=='m4-submission' and submission.encoding=='bytes':
            # Match M4's controller read permissions; PUBLIC/AUTHORING byte
            # envelopes can also carry a consumed delta and must not bypass it.
            try:value=Submission.model_validate_json(canonical_json(decode_json(read_bytes(self.store,submission,65536,'m4-submission'),65536)))
            except ValueError:pass
        # Preserve M4 source-rejection controls: malformed/wrong-baseline/wrong-
        # kind manifests reject before their alleged changes blob is consumed.
        if value is not None and value.baseline==checked.task.baseline and value.changes.kind=='m4-source-delta' and value.changes.encoding=='bytes':
            refs.append(value.changes)
        check_consumed(self.registry,refs,register=register)
        return tuple(refs)

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
            self._intent(claim)
            reset_ref=None;extra_costs=()
            try:
                if reset:reset_ref,extra_costs=self._reset(checked,projection_ref,submission)
                result=self.grader.grade(checked.task_ref,submission,seed)
            except GradePublicationFailed as exc:
                raise QualificationPublicationFailed('grade publication must recover without execution retry',pending=GradePending(
                    parent_claim,claim,exc,reset_ref,extra_costs),claim=parent_claim) from exc
            except Exception as exc:
                raise QualificationRecoveryRequired('runtime/reset attempt outcome is incomplete; reconcile retained M3/Registry evidence before any retry',claim) from exc
            if reset_ref is not None:
                result=c.OperationResult(operation='grade',disposition=result.disposition,artifacts=(*result.artifacts,reset_ref),
                    evidence=result.evidence,costs=(*extra_costs,*result.costs),reason=result.reason)
            try:result=self._complete(claim,result)
            except QualificationPublicationFailed as exc:
                raise QualificationPublicationFailed('retain selected grade completion and parent continuation',
                    pending=RunCompletionPending(parent_claim,exc),claim=parent_claim) from exc
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
        projection=read_local(self.store,projection_ref,ReferenceProjection,'m5-reference-projection',1024*1024)
        policies=[r for r in checked.recipe.provenance.inputs if r.kind=='sandbox-policy']
        if len(policies)!=1:raise QualificationRejected('invalid_evidence','exact runtime policy required for reset')
        runtime=self.grader.runtime
        handle=runtime.open_workspace(PreparedEnvironment(recipe=checked.task.environment,policy=policies[0]),
            source=projection.projected_source,role='candidate',allowed_changes=checked.contract.allowed_changes)
        try:
            initial_state,_,_,initial,_=runtime.workspace(handle)
            # A fixed controller diagnostic mutates only an allowed source file;
            # the original confirmed source must be restored before grading.
            from feature_rl.submission.source import change_path
            path=checked.contract.allowed_changes.source_roots[0]+'/__m5_reset_probe__.py'
            change_path(path,checked.contract.allowed_changes)
            mutation=runtime.execute_development(handle,ExecutionRequest(command=CommandSpec(
                argv=('python','-I','-c','import pathlib,sys;p=pathlib.Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(sys.stdin.buffer.read())','/workspace/source/'+path),
                working_directory='/workspace',timeout_seconds=2.0),stdin=b'# M5 temporary reset probe\n',save_source=True))
            if mutation.reason!='completed' or not mutation.cleanup_verified or mutation.saved_source.artifact==initial.artifact:
                raise QualificationRejected('environment_failure','reset canary was not actually saved with verified cleanup')
            interrupted=runtime.execute_development(handle,ExecutionRequest(command=CommandSpec(argv=('python','-I','-c','import time;time.sleep(5)'),
                working_directory='/workspace',timeout_seconds=0.25),save_source=False))
            if interrupted.reason!='timeout' or not interrupted.cleanup_verified:
                raise QualificationRejected('environment_failure','actual bounded interruption and cleanup required')
            before=runtime.workspace(handle)[0]['generation']
            restored=runtime.reset(handle)
            after=runtime.workspace(handle)[0]['generation']
            if restored!=initial or after<=before:
                raise QualificationRejected('environment_failure','reset did not restore exact initial saved source and advance generation')
            reset_submission=self.grader.submissions.from_saved(checked.task.baseline,restored.artifact,checked.contract.allowed_changes)
            if reset_submission!=submission:raise QualificationRejected('invalid_evidence','reset snapshot changes the assigned reference submission')
        finally:runtime.close(handle)
        record=ResetReceipt(task=checked.task_ref,projection=projection_ref,workspace_id=handle.workspace_id,
            interruption=interrupted.evidence,interruption_operation=interrupted.operation_id,initial_source=initial.artifact,
            reset_source=restored.artifact,generation_before=before,generation_after=after,cleanup_verified=True,recorded_at=datetime.now(timezone.utc))
        ref=put_record(self.store,record,'m5-reset')
        self.registry.register(ref,dependencies=(checked.task_ref,projection_ref,mutation.evidence,interrupted.evidence,restored.artifact))
        return ref,(mutation.cost,interrupted.cost)

    def accept(self,review_request,attestation):
        from .admission import accept
        return accept(self,review_request,attestation)

    def verify_accepted(self,task_ref,report_ref):
        from .admission import verify_accepted
        return verify_accepted(self,task_ref,report_ref)

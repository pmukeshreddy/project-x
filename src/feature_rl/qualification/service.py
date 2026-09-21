"""Run baseline, gold, wrong solutions and reset once, then freeze the result."""
from datetime import datetime, timezone
import time
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, ArtifactError
from feature_rl.environments import ExecutionRequest, CommandSpec, EnvironmentError, SourceArchive
from feature_rl.grading import GradingService, read_grade
from feature_rl.registry import Registry
from feature_rl.verifiers import load_verifier
from feature_rl.verifiers.loader import read_local
from .models import QualificationRejected, QualificationPolicy, ResetReceipt, ReferenceProjection
from .projection import derive_reference
from .controls import assess_outcome, validate_control_plan, validate_wrong_sources, disposition_for
from .evidence import put_record, unknown_cost, collapse_costs, evidence, assert_reference_determinism


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
        self.registry.register(self.policy_ref)

    def _runs(self,checked,projection):
        missing,targets=validate_control_plan(checked)
        if missing:raise QualificationRejected('provisional','; '.join(missing))
        validate_wrong_sources(self,checked)
        baseline=self.grader.submissions.create(checked.task.baseline,SourceArchive({}).to_tar(),(),checked.contract.allowed_changes)
        seed=self.policy.seed
        return (('baseline_absence',baseline,seed,'baseline_absence',targets,False),
                ('fresh_0',projection.submission,seed,'positive',(),False),
                *(('control_'+control.control_id,control.patch,seed,'negative',control.requirement_ids,False)
                  for control in checked.verifier.controls),
                ('reset_0',projection.submission,seed,'positive',(),True))

    def qualify(self,task_version):
        task_version=c.ArtifactRef.model_validate(task_version)
        self.registry.register(task_version)
        self.registry.assert_usable(task_version)
        projection_ref=None;assessments={};costs=[];issues=[]
        started=time.monotonic();signatures={}
        try:
            checked=load_verifier(self.store,task_version)
            self.grader.select_task(checked)
            if checked.task.state!=c.TaskState.BUILT or checked.task.qualification is not None:
                raise QualificationRejected('invalid_evidence','qualification requires an unqualified BUILT task')
            if any(a.disposition=='unresolved' for a in checked.contract.ambiguities):
                raise QualificationRejected('ambiguous_requirement','unresolved contract ambiguity')
            if self.builder is None:raise QualificationRejected('provisional','actual final package validator is unavailable')
            self.builder.solver_package(task_version)
            projection=derive_reference(self.store,task_version,self.grader.submissions.policy)
            projection_ref=put_record(self.store,projection,'m5-reference-projection')
            self.registry.register(projection_ref,dependencies=(task_version,projection.submission,projection.projected_source))
            for name,submission,seed,mode,targets,reset in self._runs(checked,projection):
                if time.monotonic()-started>=self.policy.max_wall_seconds:
                    raise QualificationRejected('budget_exhausted','qualification wall budget reached')
                result,receipt=self._run(checked,projection_ref,submission,seed,reset)
                costs.extend(result.costs)
                outcome=assess_outcome(checked,receipt,mode,targets)
                run_evidence=evidence(result.artifacts[0],self.revision,
                    ('QualificationService.grade',name,task_version.sha256),scope='real_integration')
                run_evidence=run_evidence.model_copy(update={'artifacts':result.artifacts})
                assessments[name]=c.RunAssessment(name=name,subject=task_version,
                    disposition=c.Disposition.SUCCESS if outcome.passed else c.Disposition.REJECTED,
                    passed=outcome.passed,requirement_ids=targets,reason=outcome.detail,evidence=(run_evidence,))
                if name=='baseline_absence':
                    healthy=assess_outcome(checked,receipt,'baseline_health',())
                    assessments['baseline_health']=c.RunAssessment(name='baseline_health',subject=task_version,
                        disposition=c.Disposition.SUCCESS if healthy.passed else c.Disposition.REJECTED,passed=healthy.passed,
                        requirement_ids=tuple(r.requirement_id for r in checked.contract.compatibility_obligations if r.mandatory),
                        reason=healthy.detail,evidence=(run_evidence,))
                    if not healthy.passed:raise QualificationRejected(healthy.code,'baseline_health: '+healthy.detail)
                if not outcome.passed:raise QualificationRejected(outcome.code,name+': '+outcome.detail)
                if name in {'fresh_0','reset_0'}:assert_reference_determinism(self.store,checked,receipt,signatures)
        except QualificationRejected as exc:issues.append(exc.code+': '+exc.detail)
        except (ArtifactError,ValueError,EnvironmentError) as exc:issues.append('environment_failure: '+type(exc).__name__+': '+str(exc)[:1200])
        if time.monotonic()-started>self.policy.max_wall_seconds:
            issues.append('budget_exhausted: qualification wall budget exceeded')
        return self._publish_qualification({'task':task_version,'projection':projection_ref,
            'assessments':assessments,'costs':tuple(costs),'issues':tuple(issues)})

    def _publish_qualification(self,frozen):
        task=frozen['task'];projection=frozen['projection'];assessments=frozen['assessments'];issues=frozen['issues']
        ev=evidence(projection or task,self.revision,('QualificationService.qualify',task.sha256),
            scope='real_integration' if assessments else 'source_inspection')
        disposition=disposition_for(issues)
        report=c.QualificationReport(kind='QualificationReport',schema_version=1,visibility=c.Visibility.PRIVATE,
            provenance=c.Provenance(producer='feature_rl.qualification',producer_version=self.revision,
                created_at=ev.recorded_at,inputs=(task,self.policy_ref,*((projection,) if projection else ())),evidence=(ev,)),
            costs=collapse_costs((*frozen['costs'],unknown_cost())),task=task,disposition=disposition,
            baseline_health=assessments.get('baseline_health'),baseline_absence=assessments.get('baseline_absence'),
            reference_run=assessments.get('fresh_0'),controls=tuple(v for name,v in assessments.items() if name.startswith('control_')),
            fresh_runs=tuple(v for name,v in assessments.items() if name.startswith('fresh_')),
            interrupted_reset_runs=tuple(v for name,v in assessments.items() if name.startswith('reset_')),
            rejection_reasons=issues,policy_version=self.policy.policy_id)
        report_ref=self.store.put_artifact(report)
        self.registry.register(report_ref)
        return c.OperationResult(operation='qualify',disposition=disposition,artifacts=(report_ref,),
            evidence=(ev,),costs=report.costs,reason='; '.join(issues) or 'Baseline, gold, wrong implementations and clean reset passed')

    def _run(self,checked,projection_ref,submission,seed,reset):
        reset_ref=None;extra_costs=()
        if reset:reset_ref,extra_costs=self._reset(checked,projection_ref,submission)
        result=self.grader.grade(checked.task_ref,submission,seed)
        if result.operation!='grade' or not result.artifacts:
            raise QualificationRejected('invalid_evidence','grader returned no grade receipt')
        receipt=read_grade(self.store,result.artifacts[0])
        if (receipt.task,receipt.submission,receipt.case_seed,receipt.verifier,receipt.implementation_revision,receipt.disposition)!=(
                checked.task_ref,submission,seed,checked.task.private_oracle,self.grader.revision,result.disposition):
            raise QualificationRejected('invalid_evidence','grader returned a receipt for another task, solution or seed')
        if reset_ref is not None:
            result=result.model_copy(update={'artifacts':(*result.artifacts,reset_ref),'costs':(*extra_costs,*result.costs)})
        return result,receipt

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
            path,dirty_entry=reset_probe(initial_source,checked.contract.allowed_changes,runtime.profile)
            mutation=runtime.execute_development(handle,ExecutionRequest(command=CommandSpec(
                argv=('/usr/local/bin/python','-I','-c','import pathlib,sys\nwith pathlib.Path(sys.argv[1]).open("ab") as stream: stream.write(sys.stdin.buffer.read())','/workspace/source/'+path),
                working_directory='/workspace',timeout_seconds=2.0),stdin=RESET_PROBE_BYTES,save_source=True))
            if mutation.reason!='completed' or not mutation.cleanup_verified or mutation.saved_source.artifact==initial.artifact:
                raise QualificationRejected('environment_failure','reset canary was not actually saved with verified cleanup')
            dirty=self.grader.submissions.source(mutation.saved_source.artifact)
            if dirty.files!=dict(initial_source.files)|{path:dirty_entry}:
                raise QualificationRejected('environment_failure','reset canary changed unexpected source bytes')
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

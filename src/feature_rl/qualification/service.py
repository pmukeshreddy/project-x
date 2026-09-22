"""Admit the frozen B/H evidence and exact solver package without re-running H."""
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, ArtifactError
from feature_rl.registry import Registry
from feature_rl.verifiers import load_verifier
from .models import QualificationRejected, QualificationPolicy, disposition_for
from .evidence import put_record, unknown_cost, evidence
from feature_rl.verifiers.loader import validate_reference



class QualificationService:
    def __init__(self,*,store,registry,builder,revision,policy=None):
        if not isinstance(store,ArtifactStore) or store.role!=c.ActorRole.CONTROLLER or not isinstance(registry,Registry) or registry.store is not store:
            raise TypeError('M5 requires the same actual controller store and Registry')
        if builder is not None:
            from feature_rl.pipeline.build import TaskBuilder
            if not isinstance(builder,TaskBuilder) or builder.store is not store or builder.registry is not registry:
                raise TypeError('actual same-store M6 TaskBuilder required')
        if type(revision) is not str or len(revision) not in (40,64) or any(ch not in '0123456789abcdef' for ch in revision):raise ValueError('exact M5 implementation revision required')
        self.store=store;self.registry=registry;self.builder=builder;self.revision=revision
        self.policy=QualificationPolicy() if policy is None else QualificationPolicy.model_validate(policy)
        self.policy_ref=put_record(store,self.policy,'m5-qualification-policy')
        self.registry.register(self.policy_ref)

    def qualify(self,task_version):
        task_version=c.ArtifactRef.model_validate(task_version)
        self.registry.register(task_version)
        self.registry.assert_usable(task_version)
        validation=None;assessments={};issues=[]
        try:
            checked=load_verifier(self.store,task_version)
            if checked.task.state!=c.TaskState.BUILT or checked.task.qualification is not None:
                raise QualificationRejected('invalid_evidence','qualification requires the original BUILT task')
            if self.builder is None:raise QualificationRejected('provisional','final package validator unavailable')
            self.builder.solver_package(task_version)
            pair=self.store.get_artifact(checked.task.source_pair)
            proof=validate_reference(self.store,checked.verifier,checked.task.environment,pair.baseline,pair.reference)
            validation=checked.verifier.validation
            runs=(proof.baseline,proof.reference,proof.repeated_reference)
            self.registry.register(validation,dependencies=tuple(dict.fromkeys((proof.source_pair,proof.environment,
                *proof.inputs,*proof.expected,*(ref for run in runs for ref in
                    (run.source,run.build,*run.executions,*run.outputs))))))
            for name,detail in (
                    ('baseline_health','B builds and executes the selected commands'),
                    ('baseline_absence','B fails at least one selected feature input'),
                    ('reference_run','H passes all frozen outputs and repeats identically in clean executions')):
                ev=evidence(validation,self.revision,('QualificationService.validate',name),scope='real_integration')
                assessments[name]=c.RunAssessment(name=name,subject=task_version,disposition=c.Disposition.SUCCESS,
                    passed=True,requirement_ids=(),reason=detail,evidence=(ev,))
        except QualificationRejected as exc:issues.append(exc.code+': '+exc.detail)
        except (ArtifactError,ValueError) as exc:issues.append('invalid_evidence: '+str(exc)[:1200])
        ev=evidence(validation or task_version,self.revision,('QualificationService.qualify',task_version.sha256))
        disposition=disposition_for(issues)
        report=c.QualificationReport(kind='QualificationReport',schema_version=2,visibility=c.Visibility.PRIVATE,
            provenance=c.Provenance(producer='feature_rl.qualification',producer_version=self.revision,
                created_at=ev.recorded_at,inputs=(task_version,self.policy_ref,*((validation,) if validation else ())),evidence=(ev,)),
            costs=(unknown_cost(),),task=task_version,disposition=disposition,
            baseline_health=assessments.get('baseline_health'),baseline_absence=assessments.get('baseline_absence'),
            reference_run=assessments.get('reference_run'),rejection_reasons=tuple(issues),policy_version=self.policy.policy_id)
        report_ref=self.store.put_artifact(report)
        self.registry.register(report_ref)
        return c.OperationResult(operation='qualify',disposition=disposition,artifacts=(report_ref,),
            evidence=(ev,),costs=report.costs,reason='; '.join(issues) or 'Frozen B/H evidence and solver package validated')

    def verify_accepted(self,task_ref,report_ref):
        from .admission import verify_accepted
        return verify_accepted(self,task_ref,report_ref)

"""Resolve frozen private qualification reports through trusted service profiles."""
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactError, canonical_json
from feature_rl.qualification import QualificationPolicy, QualificationService, QualificationRejected
from feature_rl.registry import RegistryError
from .lifecycle import TaskLifecycle, AdmissionRejected, unchanged
from .packaging import checked, document, read_record, typed


class ReleasedTaskResolver:
    """Read frozen reports using controller-supplied qualification implementations."""
    def __init__(self, *, profiles: tuple[TaskLifecycle, ...], revision: str):
        if type(profiles) is not tuple or not 1 <= len(profiles) <= 32 or any(type(p) is not TaskLifecycle for p in profiles):
            raise TypeError('one to 32 actual TaskLifecycle profiles required')
        if type(revision) is not str or len(revision) not in (40, 64) or any(ch not in '0123456789abcdef' for ch in revision):
            raise ValueError('exact resolver implementation revision required')
        self.store, self.registry = profiles[0].store, profiles[0].registry
        if any(p.store is not self.store or p.registry is not self.registry for p in profiles):
            raise TypeError('all trusted profiles must share the actual store and Registry')
        if len({p.qualification.revision for p in profiles}) != len(profiles):
            raise ValueError('duplicate qualification implementation profiles')
        self.profiles, self.revision = profiles, revision
        refs = tuple(p.configuration for p in profiles)
        for ref in refs:
            self.registry.assert_usable(ref)
        payload = canonical_json({'version': 'm6-released-resolver-v1', 'revision': revision,
            'profiles': [document(ref) for ref in refs]})
        self.configuration = self.store.put_bytes(payload, 'm6-resolver-configuration', c.Visibility.PRIVATE)
        self.registry.register(self.configuration, dependencies=refs)

    def resolve_released(self, task_ref: c.ArtifactRef) -> c.TaskBundle:
        """Check report/task hashes and policy without replaying qualification."""
        try:
            task_ref = checked(c.ArtifactRef, task_ref)
            self.registry.assert_usable(self.configuration)
            self.registry.assert_usable(task_ref)
            task = typed(self.store, task_ref, c.TaskBundle)
            if task.state != c.TaskState.RELEASED or task.qualification is None:
                raise AdmissionRejected('released manifest and accepted qualification required')
            report_ref = task.qualification
            if report_ref.visibility != c.Visibility.PRIVATE:
                raise AdmissionRejected('qualification report must retain private visibility')
            self.registry.assert_usable(report_ref)
            report = typed(self.store, report_ref, c.QualificationReport)
            profile = next((p for p in self.profiles
                if p.qualification.revision == report.provenance.producer_version), None)
            if profile is None:
                raise AdmissionRejected('unsupported qualification implementation revision')
            if report.provenance.producer != 'feature_rl.qualification' or len(report.provenance.inputs) not in (2, 3):
                raise AdmissionRejected('qualification report lacks controller origin and policy')
            policy_ref = report.provenance.inputs[1]
            if policy_ref.visibility != c.Visibility.PRIVATE:
                raise AdmissionRejected('qualification policy must retain private visibility')
            self.registry.assert_usable(policy_ref)
            policy = read_record(self.store, policy_ref, QualificationPolicy, 'm5-qualification-policy')
            template = profile.qualification
            qualification = QualificationService(store=self.store, registry=self.registry,
                builder=template.builder, revision=template.revision, policy=policy)
            accepted = qualification.verify_accepted(task_ref, report_ref)
            built = typed(self.store, accepted.task, c.TaskBundle)
            if built.state != c.TaskState.BUILT or built.qualification is not None:
                raise AdmissionRejected('accepted report must bind an unqualified BUILT task')
            unchanged(built, task)
            return task
        except (ArtifactError, RegistryError, QualificationRejected, OSError, ValueError) as exc:
            raise AdmissionRejected(str(exc)) from exc

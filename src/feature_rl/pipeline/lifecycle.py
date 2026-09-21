"""Freeze accepted task manifests without rerunning qualification."""
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, ArtifactError, canonical_json
from feature_rl.qualification import QualificationService, QualificationRejected
from feature_rl.registry import Registry, RegistryError
from .packaging import MAX_DOCUMENT, document, checked, typed


class AdmissionRejected(ValueError):
    """The immutable task/report pair is not currently usable."""


def unchanged(built, later):
    if canonical_json(built.model_dump(mode='json', exclude={'state', 'qualification'})) != canonical_json(
            later.model_dump(mode='json', exclude={'state', 'qualification'})):
        raise AdmissionRejected('later manifest changed the complete frozen BUILT payload')


class TaskLifecycle:
    def __init__(self, *, store: ArtifactStore, registry: Registry,
                 qualification: QualificationService, revision: str):
        if (not isinstance(store, ArtifactStore) or store.role != c.ActorRole.CONTROLLER
                or not isinstance(registry, Registry) or registry.store is not store
                or type(qualification) is not QualificationService or qualification.store is not store
                or qualification.registry is not registry):
            raise TypeError('lifecycle requires the same actual store, Registry and QualificationService')
        if type(revision) is not str or len(revision) not in (40, 64) or any(ch not in '0123456789abcdef' for ch in revision):
            raise ValueError('exact lifecycle implementation revision required')
        self.store, self.registry, self.qualification, self.revision = store, registry, qualification, revision
        self.configuration_bytes = canonical_json({'version': 'm6-lifecycle-policy-v1', 'revision': revision,
            'qualification_revision': qualification.revision, 'qualification_policy': document(qualification.policy_ref),
            'sequence': ['built', 'qualified', 'released']})
        self.configuration = store.put_bytes(self.configuration_bytes, 'm6-lifecycle-policy', c.Visibility.PRIVATE)
        registry.register(self.configuration, dependencies=(qualification.policy_ref,))

    def _task(self, reference):
        self.registry.assert_usable(reference)
        return typed(self.store, reference, c.TaskBundle)

    def _accepted(self, task_ref, task, report_ref):
        self.registry.assert_usable(self.configuration)
        report = self.qualification.verify_accepted(task_ref, report_ref)
        built = self._task(report.task)
        if built.state != c.TaskState.BUILT or built.qualification is not None:
            raise AdmissionRejected('accepted report must bind an unqualified BUILT task')
        unchanged(built, task)
        return report

    def resolve_released(self, task_ref: c.ArtifactRef) -> c.TaskBundle:
        """Read the frozen task/report pair; no execution or history replay."""
        try:
            task_ref = checked(c.ArtifactRef, task_ref)
            task = self._task(task_ref)
            if task.state != c.TaskState.RELEASED or task.qualification is None:
                raise AdmissionRejected('released manifest with accepted qualification is required')
            self._accepted(task_ref, task, task.qualification)
            return task
        except (ArtifactError, OSError, RegistryError, QualificationRejected, ValueError) as exc:
            raise AdmissionRejected(str(exc)) from exc

    def qualify(self, built_task: c.ArtifactRef, accepted_report: c.ArtifactRef) -> c.OperationResult:
        return self._transition(checked(c.ArtifactRef, built_task), checked(c.ArtifactRef, accepted_report), 'qualified')

    def release(self, qualified_task: c.ArtifactRef) -> c.OperationResult:
        return self._transition(checked(c.ArtifactRef, qualified_task), None, 'released')

    def _transition(self, predecessor, report_ref, destination):
        costs = (c.CostRecord(category='storage', wall_seconds=None, cpu_seconds=None,
            gpu_seconds=None, input_tokens=None, output_tokens=None, human_minutes=None,
            usd=None, measurement='unknown', note='Frozen manifest publication cost is unmeasured.'),)
        operation = 'qualify' if destination == 'qualified' else 'release'
        try:
            prior = self._task(predecessor)
            if destination == 'qualified':
                if prior.state != c.TaskState.BUILT or prior.qualification is not None:
                    raise AdmissionRejected('qualification transition requires original BUILT task')
            else:
                if prior.state != c.TaskState.QUALIFIED or prior.qualification is None:
                    raise AdmissionRejected('release requires a QUALIFIED predecessor')
                report_ref = prior.qualification
            report = self._accepted(predecessor, prior, report_ref)
            if destination == 'qualified' and report.task != predecessor:
                raise AdmissionRejected('accepted report task differs from BUILT predecessor')
            target = c.TaskBundle.model_validate_json(canonical_json(document(prior) | {
                'state': destination, 'qualification': document(report_ref)}))
            envelope = {'kind': target.kind, 'schema_version': target.schema_version,
                'visibility': target.visibility.value, 'encoding': 'json', 'payload': document(target)}
            if len(canonical_json(envelope)) > MAX_DOCUMENT:
                raise AdmissionRejected('transition target exceeds its reader byte limit')
            reference = self.store.put_artifact(target)
            self.registry.register(reference)
            return c.OperationResult(operation=operation, disposition=c.Disposition.SUCCESS,
                artifacts=(reference,), evidence=report.provenance.evidence, costs=costs,
                reason='Accepted frozen task marked '+destination+'; solver bytes unchanged')
        except (ArtifactError, OSError, RegistryError, QualificationRejected, ValueError) as exc:
            if isinstance(exc, QualificationRejected):
                disposition = c.Disposition.PROVISIONAL if exc.code == 'provisional' else c.Disposition.INVALID
            elif isinstance(exc, RegistryError):
                disposition = c.Disposition.BLOCKED
            elif isinstance(exc, OSError):
                disposition = c.Disposition.INFRASTRUCTURE
            else:
                disposition = c.Disposition.INVALID
            return c.OperationResult(operation=operation, disposition=disposition, artifacts=(),
                evidence=(), costs=costs, reason=(type(exc).__name__+': '+str(exc))[:2048])

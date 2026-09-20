"""Resolve candidate-specific frozen M5 policies through concrete trusted profiles."""
from typing import Literal

from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactError, canonical_json
from feature_rl.qualification import QualificationPolicy, QualificationService, QualificationRejected
from feature_rl.registry import RegistryError
from .lifecycle import TaskLifecycle, AdmissionRejected
from .packaging import checked, document, read_record, typed


class LifecyclePolicy(c.StrictModel):
    version: Literal['m6-lifecycle-policy-v1']
    revision: c.Revision
    qualification_revision: c.Revision
    qualification_configuration: c.ArtifactRef
    qualification_policy: c.ArtifactRef
    sequence: tuple[Literal['built'], Literal['qualified'], Literal['released']]


class QualificationConfiguration(c.StrictModel):
    version: Literal['m5-configuration-v1']
    policy: c.ArtifactRef
    grading_revision: c.Revision
    runtime_revision: c.Revision
    builder_revision: c.Revision | None


def profile_versions(profile):
    q = profile.qualification
    return (profile.revision, q.revision, q.grader.revision, q.grader.runtime.revision,
            None if q.builder is None else q.builder.revision)


class ReleasedTaskResolver:
    """One controller store, bounded trusted version profiles, varying task policies.

    Profiles supply actual services and current external trust, never artifact-
    chosen code, callbacks or enrollment. A resolver supports only the supplied
    exact implementation/runtime/builder revisions. Historical audit reads do
    not need this class and cannot grant current admission.
    """
    def __init__(self, *, profiles: tuple[TaskLifecycle, ...], revision: str):
        if type(profiles) is not tuple or not 1 <= len(profiles) <= 32 or any(type(p) is not TaskLifecycle for p in profiles):
            raise TypeError('one to 32 actual TaskLifecycle profiles required')
        if type(revision) is not str or len(revision) not in (40, 64) or any(ch not in '0123456789abcdef' for ch in revision):
            raise ValueError('exact resolver implementation revision required')
        self.store, self.registry = profiles[0].store, profiles[0].registry
        if any(p.store is not self.store or p.registry is not self.registry for p in profiles):
            raise TypeError('all trusted profiles must share the actual store and Registry')
        if len({profile_versions(p) for p in profiles}) != len(profiles):
            raise ValueError('duplicate version profiles would make current trust ambiguous')
        self.profiles, self.revision = profiles, revision
        refs = tuple(p.configuration for p in profiles)
        for ref in refs: self.registry.assert_usable(ref)
        payload = canonical_json({'version': 'm6-released-resolver-v1', 'revision': revision,
            'profiles': [document(ref) for ref in refs]})
        self.configuration = self.store.put_bytes(payload, 'm6-resolver-configuration', c.Visibility.PRIVATE)
        self.registry.register(self.configuration, dependencies=refs)

    def _record(self, reference, model, kind):
        if reference.visibility != c.Visibility.PRIVATE:
            raise AdmissionRejected('admission configuration must retain private visibility')
        self.registry.assert_usable(reference)
        return read_record(self.store, reference, model, kind)

    def _selected_profile(self, task_ref, report_ref):
        # Q is an input to both transitions. Use the existing dependency graph;
        # tracing an output alone need not include its producer job.
        matches = []
        for job_id in self.registry.trace(report_ref).jobs:
            job = self.registry.job(job_id)
            if (job.spec.operation != 'release' or job.spec.invocation != 'm6-transition-released'
                    or job.state != 'completed' or job.result is None
                    or job.result.disposition != c.Disposition.SUCCESS
                    or job.result.artifacts[:1] != (task_ref,)):
                continue
            config = self._record(job.spec.configuration, LifecyclePolicy, 'm6-lifecycle-policy')
            qconfig = self._record(config.qualification_configuration,
                QualificationConfiguration, 'm5-qualification-configuration')
            if config.revision != job.spec.implementation or qconfig.policy != config.qualification_policy:
                raise AdmissionRejected('selected lifecycle configuration changed its frozen M5 policy')
            versions = (config.revision, config.qualification_revision, qconfig.grading_revision,
                        qconfig.runtime_revision, qconfig.builder_revision)
            for profile in self.profiles:
                if profile_versions(profile) == versions:
                    matches.append((profile, config, job.spec.configuration))
        if len(matches) != 1:
            raise AdmissionRejected('released task lacks one selected chain under supported exact service versions')
        return matches[0]

    def resolve_released(self, task_ref: c.ArtifactRef) -> c.TaskBundle:
        """Validate current Tn→Q→T0 and selected transitions; no job is dispatched.

        The concrete service constructors only reassert already selected CAS
        and Registry configurations. No new policy/configuration is admitted by
        this read: all exact references must already be registered and usable.
        M5 execution evidence and quarantine are checked on every call.
        """
        try:
            task_ref = checked(c.ArtifactRef, task_ref)
            self.registry.assert_usable(self.configuration)
            self.registry.assert_usable(task_ref)
            task = typed(self.store, task_ref, c.TaskBundle)
            if task.state != c.TaskState.RELEASED or task.qualification is None:
                raise AdmissionRejected('exact RELEASED manifest and accepted Q required')
            self.registry.assert_usable(task.qualification)
            profile, config, expected_configuration = self._selected_profile(task_ref, task.qualification)
            policy = self._record(config.qualification_policy, QualificationPolicy, 'm5-qualification-policy')
            template = profile.qualification
            qualification = QualificationService(store=self.store, registry=self.registry,
                grader=template.grader, builder=template.builder, revision=template.revision,
                policy=policy)
            if (qualification.policy_ref != config.qualification_policy
                    or qualification.configuration != config.qualification_configuration):
                raise AdmissionRejected('resolved service differs from selected M5 configuration')
            lifecycle = TaskLifecycle(store=self.store, registry=self.registry,
                qualification=qualification, revision=profile.revision)
            if lifecycle.configuration != expected_configuration:
                raise AdmissionRejected('resolved lifecycle differs from selected release configuration')
            return lifecycle.resolve_released(task_ref)
        except (ArtifactError, RegistryError, QualificationRejected, OSError, ValueError) as exc:
            raise AdmissionRejected(str(exc)) from exc

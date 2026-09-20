"""Strict local composition of actual services; no artifact-selected code."""
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore
from feature_rl.environments import DockerEngine, EnvironmentRuntime, SandboxPolicy
from feature_rl.environments.images import ImageDigest, ImageRepository
from feature_rl.grading import GradingService
from feature_rl.qualification import QualificationPolicy, QualificationService
from feature_rl.audits.attestation import SSHHumanVerifier
from feature_rl.registry import Registry, RegistryLimits
from .build import TaskBuilder
from .factory import Factory
from .lifecycle import TaskLifecycle
from .resolver import ReleasedTaskResolver
from .authoring_models import AuthoringSettings
from .workflow_models import FeatureWorkflowSettings
from feature_rl.training.native import NativeSettings


class ConfigurationRequired(ValueError):
    """An actual service prerequisite has not been configured."""


class LocalPaths(c.StrictModel):
    @field_validator('*', mode='after', check_fields=False)
    @classmethod
    def absolute_paths(cls, value, info):
        if info.field_name.endswith(('_root', '_path')):
            if type(value) is not str or not Path(value).is_absolute() or '..' in Path(value).parts:
                raise ValueError('absolute local paths without parent traversal are required')
        return value


class RuntimeConfiguration(LocalPaths):
    state_root: str
    socket_path: str
    revision: c.Revision
    grading_revision: c.Revision
    policy: SandboxPolicy
    image_repository: ImageRepository | None = Field(default=None, exclude_if=lambda value: value is None)
    qualification_image: ImageDigest | None = Field(default=None, exclude_if=lambda value: value is None)
    image_seconds: Annotated[float, Field(gt=0, le=3600)] = Field(default=600.0, exclude_if=lambda value: value == 600.0)
    grade_wall_seconds: Annotated[float, Field(gt=0, le=3600)] = 600.0


class HumanTrustConfiguration(LocalPaths):
    enrollment_path: str
    enrollment_sha256: c.Digest


class QualificationSettings(c.StrictModel):
    revision: c.Revision
    policy: QualificationPolicy = Field(default_factory=QualificationPolicy)


class NativeConfiguration(c.StrictModel):
    revision: c.Revision
    settings: NativeSettings
    bootstrap: c.TrainingConfig | None = None


class EvaluationSettings(c.StrictModel):
    revision: c.Revision


class AuditSettings(c.StrictModel):
    revision: c.Revision
    selection_manifest: c.ArtifactRef
    attestations: Annotated[dict[c.Identifier,c.ArtifactRef],Field(max_length=4096)]
    human: HumanTrustConfiguration


class CLIConfiguration(LocalPaths):
    version: Literal['m6-cli-v1'] = 'm6-cli-v1'
    store_root: str
    registry_root: str
    revision: c.Revision
    builder_revision: c.Revision | None = None
    registry_limits: RegistryLimits = Field(default_factory=RegistryLimits)
    runtime: RuntimeConfiguration | None = None
    qualification: QualificationSettings | None = None
    authoring: AuthoringSettings | None = None
    workflow: FeatureWorkflowSettings | None = None
    native: NativeConfiguration | None = None
    evaluation: EvaluationSettings | None = None
    audit: AuditSettings | None = None


@dataclass(frozen=True)
class Application:
    factory: Factory
    runtime: EnvironmentRuntime | None
    grader: GradingService | None
    lifecycle: TaskLifecycle | None
    resolver: ReleasedTaskResolver | None

    def close(self):
        self.factory.close()


def compose(config: CLIConfiguration, *, runtime=False, qualification=False, authoring=False,
            native_operation: Literal['run','train','evaluate'] | None=None, audit=False, workflow=False) -> Application:
    """Create real services, qualifying the M3 boundary only when requested.

    Source and complete-artifact construction require neither a daemon nor model.
    Runtime composition runs the actual trusted M3 boundary probe; a JSON flag or
    cached caller assertion cannot substitute for it. Human enrollment is read-only.
    """
    if type(config) is not CLIConfiguration:
        raise TypeError('validated CLIConfiguration required')
    if native_operation not in (None,'run','train','evaluate'):raise ValueError('unknown native operation')
    if authoring and config.authoring is None:raise ConfigurationRequired('actual M2/M4 authoring settings and frozen batch budget are required')
    if workflow:
        if config.workflow is None:raise ConfigurationRequired('automatic construction requires pinned intake, runtime and bounded authoring settings')
        runtime=True
    if native_operation is not None:
        if config.native is None:raise ConfigurationRequired('actual inert M7 native settings are required')
        if native_operation in ('run','evaluate') and config.native.bootstrap is None:
            raise ConfigurationRequired('run/evaluate requires the frozen native bootstrap TrainingConfig')
        if native_operation=='evaluate' and config.evaluation is None:
            raise ConfigurationRequired('actual M8 evaluation revision is required')
        qualification=True
    if audit and config.audit is None:raise ConfigurationRequired('actual frozen M8 audit selection and external human trust are required')
    if (runtime or qualification) and config.runtime is None:
        raise ConfigurationRequired('actual M3 runtime configuration is required')
    if qualification and config.qualification is None:
        raise ConfigurationRequired('actual M5 qualification configuration is required')
    store=ArtifactStore(Path(config.store_root),c.ActorRole.CONTROLLER)
    registry=Registry(Path(config.registry_root),store,limits=config.registry_limits)
    builder=TaskBuilder(store=store,registry=registry,revision=config.builder_revision or config.revision)
    actual_runtime=grader=q=lifecycle=resolver=None
    if runtime or qualification:
        settings=config.runtime
        engine=DockerEngine(state_root=Path(settings.state_root),socket_path=Path(settings.socket_path),
            policy=settings.policy,image_repository=settings.image_repository,image_seconds=settings.image_seconds)
        if settings.policy.image is not None:
            engine.qualify_boundary(image=settings.qualification_image)
        elif settings.policy.profile is not None:
            raise ConfigurationRequired('an explicit profile requires its pinned image; automatic profiles are resolved per repository')
        actual_runtime=EnvironmentRuntime(store=store,engine=engine,revision=settings.revision)
        grader=GradingService(store=store,runtime=actual_runtime,revision=settings.grading_revision,
            max_wall_seconds=settings.grade_wall_seconds)
    if qualification:
        settings=config.qualification
        q=QualificationService(store=store,registry=registry,grader=grader,builder=builder,
            revision=settings.revision,policy=settings.policy)
        lifecycle=TaskLifecycle(store=store,registry=registry,qualification=q,revision=config.revision)
        resolver=ReleasedTaskResolver(profiles=(lifecycle,),revision=config.revision)
    factory=Factory(store=store,registry=registry,revision=config.revision,builder=builder,qualification=q,
        authoring=config.authoring,grading=grader)
    if workflow:
        from .workflow import FeatureWorkflow
        factory.feature_workflow=FeatureWorkflow(factory=factory,runtime=actual_runtime,settings=config.workflow)
    if native_operation is not None:
        settings=config.native
        shared=dict(store=store,registry=registry,lifecycle=resolver,builder=builder,runtime=actual_runtime,grader=grader)
        if native_operation=='train':
            from feature_rl.training.service import TrainingService
            factory.training=TrainingService(**shared,settings=settings.settings,revision=settings.revision)
        else:
            from feature_rl.training.factory import NativeSessionFactory
            inert=NativeSessionFactory(store=store,registry=registry,settings=settings.settings,
                configuration=settings.bootstrap,revision=settings.revision)
            if native_operation=='run':
                from feature_rl.agents.native_service import NativeRunService
                factory.native_run=NativeRunService(**shared,native_factory=inert,revision=settings.revision)
            else:
                from feature_rl.evaluation.service import EvaluationService
                factory.evaluation=EvaluationService(**shared,native_factory=inert,revision=config.evaluation.revision)
    if audit:
        from feature_rl.audits.service import AuditService
        settings=config.audit
        human=SSHHumanVerifier(enrollment_path=settings.human.enrollment_path,
            expected_enrollment_sha256=settings.human.enrollment_sha256)
        factory.audit_service=AuditService(store=store,registry=registry,human_verifier=human,
            selection_manifest=settings.selection_manifest,attestations=settings.attestations,revision=settings.revision)
    return Application(factory,actual_runtime,grader,lifecycle,resolver)

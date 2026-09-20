"""Strict local composition of actual services; no artifact-selected code."""
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore
from feature_rl.environments import DockerEngine, EnvironmentRuntime, SandboxPolicy
from feature_rl.grading import GradingService
from feature_rl.qualification import QualificationPolicy, QualificationService, SSHHumanVerifier
from feature_rl.registry import Registry, RegistryLimits
from .build import TaskBuilder
from .factory import Factory
from .lifecycle import TaskLifecycle
from .resolver import ReleasedTaskResolver


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
    policy: SandboxPolicy = Field(default_factory=SandboxPolicy)
    grade_wall_seconds: Annotated[float, Field(gt=0, le=3600)] = 600.0


class HumanTrustConfiguration(LocalPaths):
    enrollment_path: str
    enrollment_sha256: c.Digest


class QualificationSettings(c.StrictModel):
    revision: c.Revision
    policy: QualificationPolicy = Field(default_factory=QualificationPolicy)
    human: HumanTrustConfiguration | None = None


class CLIConfiguration(LocalPaths):
    version: Literal['m6-cli-v1'] = 'm6-cli-v1'
    store_root: str
    registry_root: str
    revision: c.Revision
    builder_revision: c.Revision | None = None
    registry_limits: RegistryLimits = Field(default_factory=RegistryLimits)
    runtime: RuntimeConfiguration | None = None
    qualification: QualificationSettings | None = None


@dataclass(frozen=True)
class Application:
    factory: Factory
    runtime: EnvironmentRuntime | None
    grader: GradingService | None
    lifecycle: TaskLifecycle | None
    resolver: ReleasedTaskResolver | None


def compose(config: CLIConfiguration, *, runtime=False, qualification=False) -> Application:
    """Create real services, qualifying the M3 boundary only when requested.

    Source and complete-artifact construction require neither a daemon nor model.
    Runtime composition runs the actual trusted M3 boundary probe; a JSON flag or
    cached caller assertion cannot substitute for it. Human enrollment is read-only.
    """
    if type(config) is not CLIConfiguration:
        raise TypeError('validated CLIConfiguration required')
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
            policy=settings.policy)
        engine.qualify_boundary()
        actual_runtime=EnvironmentRuntime(store=store,engine=engine,revision=settings.revision)
        grader=GradingService(store=store,runtime=actual_runtime,revision=settings.grading_revision,
            max_wall_seconds=settings.grade_wall_seconds)
    if qualification:
        settings=config.qualification
        human=None if settings.human is None else SSHHumanVerifier(
            enrollment_path=settings.human.enrollment_path,
            expected_enrollment_sha256=settings.human.enrollment_sha256)
        q=QualificationService(store=store,registry=registry,grader=grader,builder=builder,
            revision=settings.revision,policy=settings.policy,attestation_verifier=human)
        lifecycle=TaskLifecycle(store=store,registry=registry,qualification=q,revision=config.revision)
        resolver=ReleasedTaskResolver(profiles=(lifecycle,),revision=config.revision)
    factory=Factory(store=store,registry=registry,revision=config.revision,builder=builder,qualification=q)
    return Application(factory,actual_runtime,grader,lifecycle,resolver)

"""Frozen inputs for automatic intake-to-BUILT construction; no admission claims."""
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from feature_rl import contracts as c
from feature_rl.generation import GenerationLimits
from feature_rl.generation.backend import BackendConfig
from feature_rl.intake import PullRequestIntakeSpec
from feature_rl.splits import PartitionManifest
from feature_rl.requirements import GroundedSource
from feature_rl.environments import PreparedEnvironment
from feature_rl.registry import Claim
from .authoring_models import AuthoringCaps
from .models import Name


class FeatureWorkflowSettings(c.StrictModel):
    version: Literal['m6-feature-settings-v1']='m6-feature-settings-v1'
    cache_root: str
    cache_manifest_path: str
    cache_manifest_sha256: c.Digest
    git_directory: str
    source_max_bytes: Annotated[int,Field(gt=0,le=64*1024*1024)]
    git_timeout_seconds: Annotated[float,Field(gt=0,le=120)]=30.0
    intake_revision: c.Revision
    backend: BackendConfig
    m2_revision: c.Revision
    m4_revision: c.Revision
    generation_limits: GenerationLimits
    authoring_caps: AuthoringCaps
    calibration_evidence: Annotated[tuple[c.ArtifactRef,...],Field(min_length=1,max_length=32)]
    context_files: Annotated[int,Field(ge=1,le=32)]=8
    context_lines: Annotated[int,Field(ge=8,le=512)]=80
    context_bytes: Annotated[int,Field(ge=1024,le=524288)]=65536
    max_controls: Annotated[int,Field(ge=1,le=32)]=32
    evidence_scope: Literal['real_integration','unit_diagnostic']='real_integration'

    @field_validator('cache_root','cache_manifest_path','git_directory')
    @classmethod
    def absolute_paths(cls,value):
        if not Path(value).is_absolute() or '..' in Path(value).parts:
            raise ValueError('workflow requires explicit absolute local input paths')
        return value


class FeatureWorkflowRequest(c.StrictModel):
    version: Literal['m6-feature-request-v1']='m6-feature-request-v1'
    intake: PullRequestIntakeSpec
    partitions: PartitionManifest
    invocation: Name
    episode_limits: c.ResourceLimits
    seed_policy: c.SeedPolicy
    public_checks: Annotated[tuple[c.ArtifactRef,...],Field(max_length=32)]=()
    allowed_requirement_ids: Annotated[tuple[c.Identifier,...],Field(min_length=1,max_length=16)]=(
        'F1','F2','F3','F4','F5','F6','F7','F8','C1','C2','C3','C4')

    @model_validator(mode='after')
    def closed_inputs(self):
        if len(set(self.allowed_requirement_ids))!=len(self.allowed_requirement_ids):
            raise ValueError('duplicate workflow requirement namespace')
        if not self.seed_policy.same_cases_within_group or any(seed>=2**63 for seed in self.seed_policy.seeds):
            raise ValueError('workflow requires explicit same-case seeds in the grading domain')
        if any(ref.visibility!=c.Visibility.PUBLIC for ref in self.public_checks):
            raise ValueError('workflow public checks must be public artifacts')
        return self


class IntakeSelection(c.StrictModel):
    candidate: c.ArtifactRef
    source_pair: c.ArtifactRef
    request: c.ArtifactRef
    baseline: c.ArtifactRef
    reference: c.ArtifactRef
    license_text: c.ArtifactRef
    provenance_label: Literal['historical_request','reconstructed_specification']
    mixed_paths_for_qualification: tuple[str,...]


class PreparationSelection(c.StrictModel):
    environment: PreparedEnvironment
    context: GroundedSource
    entry_points: tuple[str,...]
    supported_observables: tuple[str,...]
    private_evidence: tuple[c.ArtifactRef,...]
    costs: tuple[c.CostRecord,...]


class FeatureStep(c.StrictModel):
    version: Literal['m6-feature-step-v1']='m6-feature-step-v1'
    claim: Claim
    request: c.ArtifactRef
    key: Name
    inputs: c.ArtifactRef
    output: IntakeSelection | PreparationSelection | c.OperationResult
    costs: tuple[c.CostRecord,...]


class FeatureOutcome(c.StrictModel):
    version: Literal['m6-feature-outcome-v1']='m6-feature-outcome-v1'
    claim: Claim
    request: c.ArtifactRef
    selected: c.OperationResult
    steps: tuple[c.ArtifactRef,...]
    recorded_at: c.UTCDateTime
    limitations: tuple[str,...]

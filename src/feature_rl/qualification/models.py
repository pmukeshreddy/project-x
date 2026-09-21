"""Controller-local records for the bounded qualification check."""
from typing import Annotated, Literal
from pydantic import Field
from feature_rl.contracts import ArtifactRef, Digest, StrictModel, UTCDateTime
from feature_rl.verifiers.models import Name

ReasonCode = Literal['accepted','provisional','ambiguous_requirement','unsupported_semantics',
    'environment_failure','oracle_disagreement','false_acceptance','false_rejection',
    'flaky_task','budget_exhausted','invalid_evidence']

class QualificationRejected(ValueError):
    def __init__(self, code: ReasonCode, detail: str):
        self.code=code;self.detail=detail
        super().__init__(code+': '+detail)

class ProjectionPath(StrictModel):
    path: Annotated[str,Field(min_length=1,max_length=1024)]
    action: Literal['included_implementation','excluded_unrelated']
    rationale: Annotated[str,Field(min_length=1,max_length=4096)]
    before_sha256: Digest | None
    after_sha256: Digest | None
    before_executable: bool | None
    after_executable: bool | None

class ReferenceProjection(StrictModel):
    version: Literal['m5-reference-projection-v1']='m5-reference-projection-v1'
    task: ArtifactRef
    source_pair: ArtifactRef
    baseline: ArtifactRef
    reference: ArtifactRef
    projected_source: ArtifactRef
    submission: ArtifactRef
    paths: Annotated[tuple[ProjectionPath,...],Field(min_length=1,max_length=2000)]

Mode = Literal['positive','baseline_health','baseline_absence','negative']

class GateOutcome(StrictModel):
    passed: bool
    code: ReasonCode
    detail: str

class QualificationPolicy(StrictModel):
    version: Literal['m5-pilot-policy-v1']='m5-pilot-policy-v1'
    policy_id: Name='pilot-v1'
    seed: Annotated[int,Field(ge=0,lt=2**63)]=11
    max_wall_seconds: Annotated[float,Field(gt=0,le=21600)]=3600.0

class RunBinding(StrictModel):
    version: Literal['m5-run-binding-v1']='m5-run-binding-v1'
    name: Name
    task: ArtifactRef
    projection: ArtifactRef
    submission: ArtifactRef
    seed: Annotated[int,Field(ge=0,lt=2**63)]
    grade: ArtifactRef
    mode: Mode
    targets: tuple[Name,...]
    operation_ids: tuple[Name,...]
    grade_job: Digest
    reset: ArtifactRef | None=None

class ResetReceipt(StrictModel):
    version: Literal['m5-reset-v1']='m5-reset-v1'
    task: ArtifactRef
    projection: ArtifactRef
    workspace_id: Annotated[str,Field(pattern=r'^[0-9a-f]{32}$')]
    mutation: ArtifactRef
    mutation_operation: Name
    initial_source: ArtifactRef
    reset_source: ArtifactRef
    generation_before: Annotated[int,Field(ge=0)]
    generation_after: Annotated[int,Field(ge=1)]
    cleanup_verified: bool
    recorded_at: UTCDateTime

class QualificationSummary(StrictModel):
    version: Literal['m5-qualification-summary-v1']='m5-qualification-summary-v1'
    task: ArtifactRef
    policy: ArtifactRef
    projection: ArtifactRef | None
    bindings: Annotated[tuple[ArtifactRef,...],Field(max_length=7)]
    issues: Annotated[tuple[str,...],Field(max_length=32)]
    qualification_job: Digest
    wall_seconds: Annotated[float,Field(ge=0)]

class QualificationPublicationFailed(Exception):
    """Publication was interrupted; the selected attempt must not execute again."""
    def __init__(self, message, *, claim=None):
        super().__init__(message);self.claim=claim

class QualificationUnavailable(Exception):
    """An unfinished attempt has an unknown outcome and cannot be redispatched."""
    def __init__(self, message, claim=None):
        super().__init__(message);self.claim=claim

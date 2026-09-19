"""M5 controller-local contracts; opaque CAS bytes, no shared schema changes."""
from typing import Annotated, Literal
from pydantic import Field
from feature_rl.contracts import ArtifactRef, Digest, StrictModel

ReasonCode = Literal['accepted','provisional','ambiguous_requirement','unsupported_semantics',
    'unrecoverable_history','environment_failure','oracle_disagreement','false_acceptance',
    'false_rejection','flaky_task','budget_exhausted','unverified_human_review','invalid_evidence']

class QualificationRejected(ValueError):
    def __init__(self, code: ReasonCode, detail: str):
        self.code=code;self.detail=detail
        super().__init__(code+': '+detail)

class ProjectionPath(StrictModel):
    path: Annotated[str,Field(min_length=1,max_length=1024)]
    action: Literal['included_implementation','excluded_documentation','excluded_tests']
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

from feature_rl.contracts import CostRecord, EvidenceRecord, UTCDateTime, Revision
from feature_rl.verifiers.models import Name, unique

Refs = Annotated[tuple[ArtifactRef,...],Field(max_length=256)]
Evidence = Annotated[tuple[EvidenceRecord,...],Field(min_length=1,max_length=64)]
Costs = Annotated[tuple[CostRecord,...],Field(min_length=1,max_length=64)]
Stage = Literal['source','environment','authoring','scenarios','verifier','qualification']
Mode = Literal['positive','baseline_health','semantic_negative','source_rejection','protocol_failure','resource_failure']
Attack = Literal['forged_verdict','evaluator_detection','hardcoded_inputs','skipped_execution',
    'protocol_manipulation','excessive_output','dependency_shadowing','path_link','retained_state']

class GateOutcome(StrictModel):
    passed: bool
    code: ReasonCode
    detail: str

class ControlDiagnosis(StrictModel):
    control_id: Name
    validity: Literal['valid','invalid','equivalent','unresolved']
    mode: Mode
    targets: tuple[Name,...]
    attack: Attack | None
    evidence: Evidence
    independence_evidence: tuple[EvidenceRecord,...] = ()
    note: Annotated[str,Field(min_length=1,max_length=4096)]

class RepairAttempt(StrictModel):
    stage: Stage
    before: ArtifactRef
    after: ArtifactRef
    diagnosis: Annotated[str,Field(min_length=1,max_length=4096)]
    change: Annotated[str,Field(min_length=1,max_length=4096)]
    evidence: Evidence
    costs: Costs

class RepairHistory(StrictModel):
    version: Literal['m5-repair-history-v1']='m5-repair-history-v1'
    candidate: ArtifactRef
    complete: bool
    initial_evidence: Evidence
    attempts: Annotated[tuple[RepairAttempt,...],Field(max_length=128)]
    journal_refs: Annotated[Refs,Field(min_length=1)]

class QualificationPolicy(StrictModel):
    version: Literal['m5-pilot-policy-v1']='m5-pilot-policy-v1'
    policy_id: Name='pilot-v1'
    baseline_missing_requirements: tuple[Name,...]=()
    controls: Annotated[tuple[ControlDiagnosis,...],Field(max_length=128)]=()
    fresh_seeds: Annotated[tuple[Annotated[int,Field(ge=0,lt=2**63)],...],Field(min_length=3,max_length=10)]=(11,11,11)
    reset_seeds: Annotated[tuple[Annotated[int,Field(ge=0,lt=2**63)],...],Field(min_length=3,max_length=10)]=(11,11,11)
    max_grade_calls: Annotated[int,Field(ge=7,le=256)]=64
    max_wall_seconds: Annotated[float,Field(gt=0,le=21600)]=3600.0
    review_seconds: Annotated[int,Field(ge=60,le=604800)]=86400
    repair_history: ArtifactRef | None=None
    repair_history_job: Annotated[str,Field(pattern=r'^[0-9a-f]{64}$')] | None=None
    factory_revision: Revision | None=None

class ReviewRequest(StrictModel):
    version: Literal['m5-review-request-v1']='m5-review-request-v1'
    task: ArtifactRef
    report: ArtifactRef
    policy: ArtifactRef
    challenge: Digest
    issued_at: UTCDateTime
    expires_at: UTCDateTime
    qualification_job: Digest

class ReviewPayload(StrictModel):
    version: Literal['m5-human-review-v1']='m5-human-review-v1'
    actor_type: Literal['human']
    human_identity: Annotated[str,Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}$')]
    decision: Literal['approved','rejected','unresolved']
    request: ArtifactRef
    task: ArtifactRef
    report: ArtifactRef
    policy: ArtifactRef
    challenge: Digest
    human_minutes: Annotated[float,Field(ge=0,le=100000)]
    statement: Literal['I reviewed the frozen contract, complete execution evidence, control validity and alternative independence.']

class DetachedAttestation(StrictModel):
    version: Literal['m5-sshsig-attestation-v1']='m5-sshsig-attestation-v1'
    payload: ArtifactRef
    signature: ArtifactRef

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
    interruption: ArtifactRef
    interruption_operation: Name
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
    bindings: Annotated[tuple[ArtifactRef,...],Field(max_length=256)]
    issues: Annotated[tuple[str,...],Field(max_length=1024)]
    repair_count: Annotated[int,Field(ge=0,le=4)] | None
    qualification_job: Digest
    wall_seconds: Annotated[float,Field(ge=0)] | None=None

class VerifiedAttestation(StrictModel):
    version: Literal['m5-verified-attestation-v1']='m5-verified-attestation-v1'
    request: ArtifactRef
    attestation: ArtifactRef
    payload: ArtifactRef
    verification: ArtifactRef
    consumed_at: UTCDateTime
    verification_job: Digest
    admission_job: Digest

from dataclasses import dataclass

@dataclass(frozen=True)
class CompletionPending:
    claim: object
    result: object

@dataclass(frozen=True)
class GradePending:
    parent_claim: object
    grade_claim: object
    pending: object
    reset_ref: ArtifactRef | None
    reset_costs: tuple

@dataclass(frozen=True)
class RunCompletionPending:
    parent_claim: object
    completion: object

@dataclass(frozen=True)
class FrozenPublication:
    """Retained bytes/costs after work, before CAS or Registry publication."""
    claim: object
    payload: object
    purpose: Literal['qualification','human_verification','admission']

class QualificationPublicationFailed(Exception):
    """Exact operation payload retained; no execution retry is authorized."""
    def __init__(self, message, *, pending, claim=None):
        super().__init__(message);self.pending=pending;self.claim=claim

class QualificationRecoveryRequired(Exception):
    def __init__(self, message, claim=None):
        super().__init__(message);self.claim=claim

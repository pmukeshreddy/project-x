"""Strict controller-side registry values. These do not authorize task admission."""
from __future__ import annotations

from typing import Annotated, Any, Literal
from pydantic import Field, model_validator

from feature_rl.contracts import ArtifactRef, CostRecord, OperationResult, StrictModel
from feature_rl.contracts.models import Digest, Revision, UTCDateTime

Name = Annotated[str, Field(min_length=1, max_length=200, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')]
Reason = Annotated[str, Field(min_length=1, max_length=4096, pattern=r'\S')]
Refs = Annotated[tuple[ArtifactRef, ...], Field(max_length=10000)]
TraceRefs = Annotated[tuple[ArtifactRef, ...], Field(max_length=100000)]
Operation = Literal['construct', 'qualify', 'release', 'run', 'grade', 'audit', 'train', 'evaluate']


class RegistryError(Exception):
    """Registry operation failed; no success or external execution is implied."""


class RegistryIntegrityError(RegistryError):
    """Preserved state, identity or projection is unsafe/inconsistent."""


class RegistryIOError(RegistryError):
    """Storage/locking failed. A commit may have happened: recover and query."""


class RegistryConflict(RegistryError):
    """A semantic identity was reused with different data or prerequisites."""


class RegistryLimit(RegistryError):
    """Configured capacity exceeded before accepting more work."""


class Backpressure(RegistryLimit):
    pass


class AttemptLimit(RegistryLimit):
    pass


class ClaimConflict(RegistryConflict):
    pass


class StaleClaim(RegistryConflict):
    pass


class QuarantinedError(RegistryConflict):
    pass


class UnknownIdentity(RegistryError):
    pass


class RegistryLimits(StrictModel):
    max_active_jobs: Annotated[int, Field(ge=1, le=1024)] = 32
    max_jobs: Annotated[int, Field(ge=1, le=100000)] = 1000
    max_attempts_per_job: Annotated[int, Field(ge=1, le=100)] = 3
    max_artifacts: Annotated[int, Field(ge=1, le=100000)] = 10000
    max_closure_artifacts: Annotated[int, Field(ge=1, le=10000)] = 256
    max_graph_edges: Annotated[int, Field(ge=1, le=100000)] = 20000
    max_event_bytes: Annotated[int, Field(ge=512, le=16777216)] = 262144
    max_events: Annotated[int, Field(ge=1, le=100000)] = 10000
    max_journal_bytes: Annotated[int, Field(ge=1024, le=1073741824)] = 67108864
    max_database_bytes: Annotated[int, Field(ge=65536, le=1073741824)] = 134217728
    max_artifact_envelope_bytes: Annotated[int, Field(ge=128, le=67108864)] = 8388608
    max_artifact_payload_bytes: Annotated[int, Field(ge=0, le=67108864)] = 4194304
    max_closure_bytes: Annotated[int, Field(ge=128, le=1073741824)] = 33554432
    lock_timeout_seconds: Annotated[float, Field(gt=0, le=30)] = 1.0


class LimitExpansion(StrictModel):
    previous_limits: RegistryLimits
    limits: RegistryLimits
    reason: Reason

    @model_validator(mode='after')
    def capacity_only(self):
        fixed = {'max_active_jobs', 'max_attempts_per_job', 'lock_timeout_seconds'}
        before, after = self.previous_limits.model_dump(), self.limits.model_dump()
        if any(after[name] != before[name] if name in fixed else after[name] < before[name] for name in before):
            raise ValueError('capacity expansion cannot shrink limits or change execution/lock policy')
        if before == after:
            raise ValueError('capacity expansion must increase a storage/count limit')
        return self


class ArtifactRecord(StrictModel):
    ref: ArtifactRef
    dependencies: Refs


class JobSpec(StrictModel):
    operation: Operation
    inputs: Annotated[Refs, Field(min_length=1)]
    configuration: ArtifactRef
    implementation: Revision
    invocation: Name
    attempt_limit: Annotated[int, Field(ge=1, le=100)]

    @model_validator(mode='after')
    def distinct_inputs(self):
        if len(set(self.inputs)) != len(self.inputs):
            raise ValueError('duplicate job inputs')
        return self


class Claim(StrictModel):
    job_id: Digest
    attempt_id: Digest
    owner: Name
    claim_key: Name
    token: Digest


class AttemptRecord(StrictModel):
    claim: Claim
    state: Literal['running', 'abandoned', 'completed']
    reason: Reason | None
    evidence: Refs


class JobRecord(StrictModel):
    job_id: Digest
    spec: JobSpec
    state: Literal['queued', 'running', 'paused', 'exhausted', 'completed']
    attempts: tuple[Digest, ...]
    result: OperationResult | None
    result_observations: tuple[Digest, ...]


class CostObservation(StrictModel):
    """Cumulative, monotonically informative snapshot for one upstream attempt."""
    source: Name
    upstream_attempt_id: Name
    revision: Annotated[int, Field(ge=1, le=10000)]
    receipts: Annotated[Refs, Field(min_length=1)]
    costs: Annotated[tuple[CostRecord, ...], Field(min_length=1, max_length=64)]

    @model_validator(mode='after')
    def unique_channels(self):
        if len({c.category for c in self.costs}) != len(self.costs):
            raise ValueError('one cumulative cost record per category is required')
        if len(set(self.receipts)) != len(self.receipts):
            raise ValueError('duplicate receipt references')
        return self


class ObservationRecord(StrictModel):
    observation_id: Digest
    attempt_id: Digest
    observation: CostObservation


class AccountingReport(StrictModel):
    observations: tuple[ObservationRecord, ...]
    unobserved_attempts: tuple[Digest, ...]


class QuarantineNotice(StrictModel):
    notice_id: Name
    root: ArtifactRef
    reason: Reason
    evidence: Annotated[Refs, Field(min_length=1)]
    active: bool
    resolution_reason: Reason | None
    resolution_evidence: Refs


class TraceReport(StrictModel):
    artifacts: TraceRefs
    jobs: tuple[Digest, ...]
    runs: TraceRefs
    checkpoints: TraceRefs
    notices: tuple[QuarantineNotice, ...]


class RegistryEvent(StrictModel):
    sequence: Annotated[int, Field(ge=1)]
    event_id: Digest
    previous: Digest
    semantic_key: Annotated[str, Field(min_length=1, max_length=500)]
    recorded_at: UTCDateTime
    action: Literal['register', 'enqueue', 'claim', 'abandon', 'retry', 'reconcile', 'complete', 'quarantine', 'lift', 'expand_limits']
    data: dict[str, Any]


class RecoveryReport(StrictModel):
    event_count: int
    appended_bytes: int
    completed_tail_bytes: int

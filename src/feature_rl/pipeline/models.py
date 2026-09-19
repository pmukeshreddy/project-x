"""Closed records for the M6 BUILT-root boundary; no admission claims."""
from typing import Annotated, Literal
import hashlib
from pydantic import Field
from feature_rl.contracts import ArtifactRef, CostRecord, Disposition, SolverView, StrictModel
from feature_rl.contracts.models import Digest, Revision, UTCDateTime
from feature_rl.environments import PreparedEnvironment
from feature_rl.registry import Claim

Name = Annotated[str, Field(min_length=1, max_length=200, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')]
Refs = Annotated[tuple[ArtifactRef, ...], Field(max_length=256)]
Costs = Annotated[tuple[CostRecord, ...], Field(min_length=1, max_length=64)]


class BuildInputs(StrictModel):
    version: Literal['m6-build-v1'] = 'm6-build-v1'
    source_pair: ArtifactRef
    contract: ArtifactRef
    scenario_plan: ArtifactRef
    verifier: ArtifactRef
    environment: PreparedEnvironment
    baseline_files: Annotated[tuple[Annotated[str, Field(min_length=1, max_length=1024)], ...], Field(min_length=1, max_length=2000)]
    invocation: Name


class BuildIntent(StrictModel):
    version: Literal['m6-build-intent-v1'] = 'm6-build-intent-v1'
    inputs: ArtifactRef
    claim: Claim
    started_at: UTCDateTime
    nonce: Digest


class InventoryEntry(StrictModel):
    path: str
    sha256: Digest
    size: Annotated[int, Field(ge=0)]
    executable: bool


class SolverInventory(StrictModel):
    version: Literal['m6-solver-inventory-v1'] = 'm6-solver-inventory-v1'
    archive: ArtifactRef
    archive_sha256: Digest
    components: Refs
    files: Annotated[tuple[InventoryEntry, ...], Field(min_length=1, max_length=2100)]


class FrozenBuild(StrictModel):
    version: Literal['m6-frozen-build-v1'] = 'm6-frozen-build-v1'
    inputs: ArtifactRef
    intent: ArtifactRef
    claim: Claim
    recorded_at: UTCDateTime
    revision: Revision
    solver_view: SolverView
    package: ArtifactRef
    dependencies: Refs
    costs: Costs


class FailedBuild(StrictModel):
    version: Literal['m6-failed-build-v1'] = 'm6-failed-build-v1'
    inputs: ArtifactRef
    intent: ArtifactRef
    claim: Claim
    recorded_at: UTCDateTime
    revision: Revision
    disposition: Disposition
    reason: Annotated[str, Field(min_length=1, max_length=2048)]
    costs: Costs


class BuildRejected(ValueError):
    """An input, join or solver package failed the construction policy."""


class BuildRecoveryRequired(Exception):
    """No assembly redispatch: inspect/reconcile the retained registry claim."""
    def __init__(self, message, claim):
        super().__init__(message)
        self.claim = claim


class BuildPublicationFailed(BuildRecoveryRequired):
    """Exact bounded receipt bytes can be retried without reassembling a task."""
    def __init__(self, message, claim, *, payload, kind):
        super().__init__(message, claim)
        self.payload = payload
        self.kind = kind
        self.sha256 = hashlib.sha256(payload).hexdigest()

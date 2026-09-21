"""Frozen inputs and resource budgets for concrete M2/M4 authoring."""
from typing import Annotated, Literal
from pydantic import Field, model_validator
from feature_rl import contracts as c
from feature_rl.generation import CodexConfig
from feature_rl.requirements import (GenerationCandidate, GroundedSource,
    ContractFinalizationInputs, RetrievalPolicy)
from feature_rl.verifiers import CheckerFinalizationInputs, ControlFinalizationInputs
from feature_rl.environments import PreparedEnvironment
from feature_rl.registry import Claim


class AuthoringBudgetExceeded(ValueError):
    """Frozen resource budget or local attempt limit exhausted before provider dispatch."""


class AuthoringBudgetUnverified(ValueError):
    """A finite monetary cap cannot be verified by the Codex subscription."""


class AuthoringCaps(c.StrictModel):
    """Cumulative admission reservations; Codex token overruns are charged after completion."""
    input_tokens: Annotated[int,Field(gt=0)]
    output_tokens: Annotated[int,Field(gt=0)]
    wall_seconds: Annotated[float,Field(gt=0)]
    cpu_seconds: Annotated[float,Field(gt=0)]
    commands: Annotated[int,Field(gt=0)]
    memory_bytes: Annotated[int,Field(gt=0)]
    spend_usd: Annotated[float,Field(ge=0)] | None


class AuthoringBatch(c.StrictModel):
    version: Literal['m6-authoring-batch-v1']='m6-authoring-batch-v1'
    candidates: Annotated[tuple[c.ArtifactRef,...],Field(min_length=1,max_length=128)]
    candidate_caps: AuthoringCaps
    batch_caps: AuthoringCaps

    @model_validator(mode='after')
    def unique_candidates(self):
        if len(set(self.candidates))!=len(self.candidates):raise ValueError('duplicate batch candidate')
        if any(ref.kind!='CandidateRecord' for ref in self.candidates):raise ValueError('batch requires exact M0 candidate refs')
        return self


class AuthoringSettings(c.StrictModel):
    codex: CodexConfig
    m2_revision: c.Revision
    m4_revision: c.Revision
    batch: AuthoringBatch
    evidence_scope: Literal['real_integration','unit_diagnostic']='real_integration'


class ResolverInputs(c.StrictModel):
    request: c.ArtifactRef
    baseline: c.ArtifactRef
    runtime_discovery: c.ArtifactRef
    public_checks: tuple[c.ArtifactRef,...]
    retrieval_policy: RetrievalPolicy
    request_provenance: Literal['historical_request','reconstructed_specification']='reconstructed_specification'


class AuthoringCall(c.StrictModel):
    version: Literal['m6-authoring-call-v1']='m6-authoring-call-v1'
    source_pair: c.ArtifactRef
    environment: PreparedEnvironment
    resolver: ResolverInputs
    generation: GenerationCandidate
    inputs: ContractFinalizationInputs | CheckerFinalizationInputs | ControlFinalizationInputs
    sources: Annotated[tuple[GroundedSource,...],Field(min_length=1,max_length=128)]

    @model_validator(mode='after')
    def actual_stage(self):
        allowed={ContractFinalizationInputs:'initial_authoring',
            CheckerFinalizationInputs:'checker_generation',ControlFinalizationInputs:'control_authoring'}
        if allowed[type(self.inputs)]!=self.generation.request.stage.value:
            raise ValueError('actual M2 stage and finalization inputs differ')
        return self


class AuthoringFrontier(c.StrictModel):
    version: Literal['m6-authoring-frontier-v1']='m6-authoring-frontier-v1'
    candidate: c.ArtifactRef
    source: c.ArtifactRef
    source_pair: c.ArtifactRef
    environment: PreparedEnvironment
    batch: c.ArtifactRef
    revision: c.Revision


class AuthoringRequest(c.StrictModel):
    version: Literal['m6-authoring-request-v1']='m6-authoring-request-v1'
    candidate: c.ArtifactRef
    frontier: c.ArtifactRef
    call: AuthoringCall
    previous: c.ArtifactRef | None
    stage: Literal['authoring','verifier']
    lane: c.Identifier


class AuthoringReceipt(c.StrictModel):
    version: Literal['m6-authoring-receipt-v1']='m6-authoring-receipt-v1'
    claim: Claim
    request: c.ArtifactRef
    outputs: tuple[c.ArtifactRef,...]
    journal_refs: tuple[c.ArtifactRef,...]
    disposition: c.Disposition
    reason: Annotated[str,Field(min_length=1,max_length=4096)]
    stage: Literal['authoring','verifier']
    lane: c.Identifier
    costs: tuple[c.CostRecord,...]
    revision: c.Revision
    recorded_at: c.UTCDateTime

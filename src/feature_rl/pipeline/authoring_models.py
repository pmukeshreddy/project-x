"""Frozen inputs for concrete M2/M4 authoring and shared repair accounting."""
from typing import Annotated, Literal
from pydantic import Field, model_validator
from feature_rl import contracts as c
from feature_rl.generation.backend import BackendConfig
from feature_rl.requirements import (GenerationCandidate, GroundedSource,
    ContractFinalizationInputs, RetrievalPolicy)
from feature_rl.scenarios import ScenarioFinalizationInputs
from feature_rl.verifiers import CheckerFinalizationInputs, ControlFinalizationInputs
from feature_rl.environments import PreparedEnvironment
from feature_rl.registry import Claim
from feature_rl.qualification.models import Attack


class AuthoringBudgetExceeded(ValueError):
    """Frozen resource/repair budget exhausted before another provider dispatch."""


class AuthoringBudgetUnverified(ValueError):
    """A finite monetary cap cannot be verified by the actual local backend."""


class AuthoringCaps(c.StrictModel):
    """Cumulative provider reservations; memory is the serialized peak ceiling."""
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
    calibration_evidence: Annotated[tuple[c.ArtifactRef,...],Field(min_length=1,max_length=32)]

    @model_validator(mode='after')
    def unique_candidates(self):
        if len(set(self.candidates))!=len(self.candidates):raise ValueError('duplicate batch candidate')
        if any(ref.kind!='CandidateRecord' for ref in self.candidates):raise ValueError('batch requires exact M0 candidate refs')
        return self


class AuthoringSettings(c.StrictModel):
    backend: BackendConfig
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


class ControlSlot(c.StrictModel):
    category: Literal['omission','plausible_wrong','hardcoded','regression','adversarial','alternative_positive']
    requirement_ids: tuple[c.Identifier,...]
    attack: Attack | None=None

    @model_validator(mode='after')
    def bounded_role(self):
        if tuple(sorted(set(self.requirement_ids)))!=self.requirement_ids:
            raise ValueError('slot requirement IDs must be distinct and sorted')
        if (self.category=='adversarial')!=(self.attack is not None):
            raise ValueError('only an adversarial slot has one named attack')
        if self.category=='alternative_positive' and self.requirement_ids:
            raise ValueError('alternative slot has no targeted omissions')
        if self.category not in ('alternative_positive','adversarial') and not self.requirement_ids:
            raise ValueError('semantic negative slot requires exact targets')
        return self


class ControlPlan(c.StrictModel):
    version: Literal['m6-control-plan-v1']='m6-control-plan-v1'
    contract: c.ArtifactRef
    slots: Annotated[tuple[ControlSlot,...],Field(min_length=1,max_length=32)]

    @model_validator(mode='after')
    def unique_slots(self):
        from feature_rl.artifacts import canonical_json
        if len({canonical_json(s.model_dump(mode='json')) for s in self.slots})!=len(self.slots):
            raise ValueError('duplicate control slot')
        return self


class AuthoringCall(c.StrictModel):
    version: Literal['m6-authoring-call-v1']='m6-authoring-call-v1'
    source_pair: c.ArtifactRef
    environment: PreparedEnvironment
    resolver: ResolverInputs
    generation: GenerationCandidate
    inputs: ContractFinalizationInputs | ScenarioFinalizationInputs | CheckerFinalizationInputs | ControlFinalizationInputs
    sources: Annotated[tuple[GroundedSource,...],Field(min_length=1,max_length=128)]
    control_plan: ControlPlan | None=None
    attack: Attack | None=None

    @model_validator(mode='after')
    def actual_stage(self):
        allowed={ContractFinalizationInputs:'initial_authoring',ScenarioFinalizationInputs:'scenario_planning',
            CheckerFinalizationInputs:'checker_generation',ControlFinalizationInputs:
                'alternative_authoring' if getattr(self.inputs,'category',None)=='alternative_positive' else 'control_authoring'}
        if allowed[type(self.inputs)]!=self.generation.request.stage.value:
            raise ValueError('actual M2 stage and finalization inputs differ')
        if isinstance(self.inputs,ControlFinalizationInputs):
            if self.control_plan is None or self.control_plan.contract!=self.inputs.contract:
                raise ValueError('control call requires its exact frozen contract plan')
            slot=ControlSlot(category=self.inputs.category,requirement_ids=tuple(sorted(self.inputs.requirement_ids)),attack=self.attack)
            if slot not in self.control_plan.slots:raise ValueError('control is outside the frozen role plan')
        elif self.control_plan is not None or self.attack is not None:
            raise ValueError('only control authoring consumes a control plan/attack')
        return self


class AuthoringFrontier(c.StrictModel):
    version: Literal['m6-authoring-frontier-v1']='m6-authoring-frontier-v1'
    candidate: c.ArtifactRef
    source: c.ArtifactRef
    source_pair: c.ArtifactRef
    environment: PreparedEnvironment
    batch: c.ArtifactRef
    scope: Literal['factory-controlled-after-source-disposition','retained-history-import']='factory-controlled-after-source-disposition'
    external_history: Literal['not-asserted']='not-asserted'
    revision: c.Revision


class AuthoringRequest(c.StrictModel):
    version: Literal['m6-authoring-request-v1']='m6-authoring-request-v1'
    candidate: c.ArtifactRef
    frontier: c.ArtifactRef
    call: AuthoringCall
    previous: c.ArtifactRef | None
    repair: bool
    stage: Literal['authoring','scenarios','verifier']
    lane: c.Identifier
    origin: Literal['factory_dispatch','retained_journal']='factory_dispatch'
    imported_journals: tuple[c.ArtifactRef,...]=()


class AuthoringReceipt(c.StrictModel):
    version: Literal['m6-authoring-receipt-v1']='m6-authoring-receipt-v1'
    claim: Claim
    request: c.ArtifactRef
    outputs: tuple[c.ArtifactRef,...]
    journal_refs: tuple[c.ArtifactRef,...]
    disposition: c.Disposition
    reason: Annotated[str,Field(min_length=1,max_length=4096)]
    repair: bool
    stage: Literal['authoring','scenarios','verifier']
    lane: c.Identifier
    costs: tuple[c.CostRecord,...]
    revision: c.Revision
    recorded_at: c.UTCDateTime

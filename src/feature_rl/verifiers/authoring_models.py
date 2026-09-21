"""Bounded generated content; frozen identity and permissions are controller inputs."""
from typing import Annotated, Literal
from pydantic import Field, model_validator
from feature_rl.contracts import (ArtifactRef, CaseDefinition, ControlPatch, CostRecord,
    Provenance, StrictModel, Visibility, WorkerAdapter)
from feature_rl.requirements.models import derived_model
from .models import InputPlan, CaseComparison, unique

MAX_CHECKER_OUTPUT_BYTES = 2 * 1024 * 1024

_CaseFields = derived_model('_CaseFields', CaseDefinition,
    ('case_id', 'requirement_ids', 'mandatory'), module=__name__)

class CaseProposal(_CaseFields):
    inputs: InputPlan
    comparison: CaseComparison

_WorkerFields = derived_model('_WorkerFields', WorkerAdapter,
    ('limitations',), module=__name__)

class WorkerProposal(_WorkerFields):
    source: Annotated[str, Field(min_length=1, max_length=65536)]
    supported_observables: Annotated[tuple[Literal['json', 'process'], ...], Field(min_length=1, max_length=2)]

    @model_validator(mode='after')
    def bounded_source(self):
        if len(self.source.encode('utf-8')) > 65536 or '\x00' in self.source:
            raise ValueError('worker source must be bounded UTF-8 without NUL')
        unique(self.supported_observables, 'worker observables')
        return self

class CheckerProposal(StrictModel):
    worker_adapter: WorkerProposal
    cases: Annotated[tuple[CaseProposal, ...], Field(min_length=1, max_length=256)]

    @model_validator(mode='after')
    def case_ids(self):
        unique([case.case_id for case in self.cases], 'case IDs')
        return self

class CheckerFinalizationInputs(StrictModel):
    contract: ArtifactRef
    scenario_plan: ArtifactRef
    baseline: ArtifactRef
    environment: ArtifactRef
    output_limit_bytes: Annotated[int, Field(gt=0, le=MAX_CHECKER_OUTPUT_BYTES)]
    public_examples: Annotated[tuple[ArtifactRef, ...], Field(max_length=128)] = ()
    controls: Annotated[tuple[ControlPatch, ...], Field(max_length=128)] = ()
    visibility: Literal[Visibility.PRIVATE, Visibility.EVALUATION]
    provenance: Provenance
    costs: Annotated[tuple[CostRecord, ...], Field(min_length=1, max_length=256)]

    @model_validator(mode='after')
    def frozen_refs(self):
        for ref, kind, visibility in (
            (self.contract, 'RequirementContract', {Visibility.AUTHORING}),
            (self.scenario_plan, 'ScenarioPlan', {Visibility.PRIVATE, Visibility.EVALUATION}),
            (self.baseline, 'source-archive', {Visibility.PUBLIC, Visibility.AUTHORING}),
            (self.environment, 'EnvironmentRecipe', {Visibility.PRIVATE, Visibility.AUTHORING, Visibility.EVALUATION}),
        ):
            if ref.kind != kind or ref.visibility not in visibility or ref.encoding != ('bytes' if kind == 'source-archive' else 'json'):
                raise ValueError('invalid frozen checker '+kind+' reference')
            if ref not in self.provenance.inputs:
                raise ValueError('checker provenance is missing '+kind)
        unique([control.control_id for control in self.controls], 'control IDs')
        unique(self.public_examples, 'public examples')
        if any(ref.visibility is not Visibility.PUBLIC for ref in self.public_examples):
            raise ValueError('public examples must be public artifacts')
        return self

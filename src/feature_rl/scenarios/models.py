"""Derived proposal and controller inputs for scenario planning."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from feature_rl.contracts import (
    ArtifactRef,
    CostRecord,
    Provenance,
    ScenarioPlan,
    SeedPolicy,
    StrictModel,
    Text,
    Visibility,
)
from feature_rl.requirements.models import derived_model


ScenarioPlanProposal = derived_model(
    "ScenarioPlanProposal", ScenarioPlan, ("scenarios",), module=__name__
)


class ScenarioFinalizationInputs(StrictModel):
    contract: ArtifactRef
    supported_observables: Annotated[tuple[Text, ...], Field(min_length=1)]
    seed_policy: SeedPolicy
    visibility: Literal[Visibility.PRIVATE, Visibility.EVALUATION]
    provenance: Provenance
    costs: Annotated[tuple[CostRecord, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def fixed_controller_inputs(self):
        if (
            self.contract.kind != "RequirementContract"
            or self.contract.encoding != "json"
            or self.contract.visibility is not Visibility.AUTHORING
        ):
            raise ValueError("scenario input requires an authoring RequirementContract")
        if len(self.supported_observables) != len(set(self.supported_observables)):
            raise ValueError("duplicate supported observables")
        if not self.seed_policy.same_cases_within_group:
            raise ValueError("scenario planning requires same cases within group")
        if self.contract not in self.provenance.inputs:
            raise ValueError("scenario provenance must include the frozen contract")
        return self


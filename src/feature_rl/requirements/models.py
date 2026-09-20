"""Derived proposal and grounded-input models for requirement authoring."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, create_model, model_validator
from pydantic_core import PydanticUndefined

from feature_rl.contracts import (
    AllowedChanges,
    ArtifactRef,
    CostRecord,
    Identifier,
    Provenance,
    RequirementContract,
    ResourceLimits,
    StrictModel,
    Text,
    Visibility,
)


def derived_model(
    name: str, source: type[StrictModel], field_names: tuple[str, ...], *, module: str
) -> type[StrictModel]:
    """Build a strict proposal from authoritative M0 FieldInfo objects."""
    definitions = {}
    for field_name in field_names:
        field = source.model_fields[field_name]
        details = field.asdict()
        attributes = {
            key: value
            for key, value in details["attributes"].items()
            if key not in {"default", "default_factory"} and value is not None
        }
        annotation = Annotated[
            details["annotation"], *details["metadata"], Field(**attributes)
        ]
        if field.default_factory is not None:
            default = Field(default_factory=field.default_factory)
        elif field.default is not PydanticUndefined:
            default = field.default
        else:
            default = ...
        definitions[field_name] = (annotation, default)
    return create_model(name, __base__=StrictModel, __module__=module, **definitions)


RequirementContractProposal = derived_model(
    "RequirementContractProposal",
    RequirementContract,
    (
        "capability",
        "entry_points",
        "requirements",
        "compatibility_obligations",
        "feature_files",
        "ambiguities",
        "allowed_changes",
    ),
    module=__name__,
)


class GroundedSource(StrictModel):
    context_id: Identifier
    role: Literal["request", "baseline", "public_check"]
    source: ArtifactRef
    locator: Text
    text: Text
    provenance_label: Literal[
        "historical_request", "reconstructed_specification", "existing_obligation"
    ]

    @model_validator(mode="after")
    def author_visible(self):
        if self.source.visibility not in {Visibility.PUBLIC, Visibility.AUTHORING}:
            raise ValueError("grounded authoring source must be public or authoring")
        if self.source.kind in {"SourcePair", "reference", "reference-tree"}:
            raise ValueError("reference implementation source is forbidden")
        return self


class ContractFinalizationInputs(StrictModel):
    visible_request: Text
    allowed_requirement_ids: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    entry_points: Annotated[tuple[Text, ...], Field(min_length=1)]
    supported_observables: Annotated[tuple[Text, ...], Field(min_length=1)]
    runtime_discovery: ArtifactRef
    allowed_changes: AllowedChanges
    public_checks: tuple[ArtifactRef, ...]
    episode_limits: ResourceLimits
    provenance_label: Literal["historical_request", "reconstructed_specification"]
    visibility: Literal[Visibility.AUTHORING]
    provenance: Provenance
    costs: Annotated[tuple[CostRecord, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_fixed_inputs(self):
        for values, label in (
            (self.allowed_requirement_ids, "allowed requirement IDs"),
            (self.entry_points, "entry points"),
            (self.supported_observables, "supported observables"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {label}")
        if any(ref.visibility is not Visibility.PUBLIC for ref in self.public_checks):
            raise ValueError("public checks must be public artifacts")
        if (
            self.runtime_discovery.kind not in {"runtime-discovery"}
            or self.runtime_discovery.visibility is not Visibility.AUTHORING
            or self.runtime_discovery.encoding != "bytes"
        ):
            raise ValueError("validated runtime discovery artifact is required")
        if self.runtime_discovery not in self.provenance.inputs:
            raise ValueError("contract provenance must include runtime discovery")
        return self

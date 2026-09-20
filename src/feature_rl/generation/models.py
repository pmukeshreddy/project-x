"""Strict proposal contracts for the configured generation boundary."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from feature_rl.contracts import (
    ArtifactRef,
    CostRecord,
    Digest,
    Identifier,
    Revision,
    StrictModel,
    Text,
    UTCDateTime,
    Visibility,
)

GENERATION_IDENTIFIER_MAX_LENGTH = 128
GenerationIdentifier = Annotated[
    Identifier, Field(max_length=GENERATION_IDENTIFIER_MAX_LENGTH)
]


class GenerationStage(str, Enum):
    DISCOVERY = "discovery"
    INITIAL_AUTHORING = "initial_authoring"
    SCENARIO_PLANNING = "scenario_planning"
    CONTROL_AUTHORING = "control_authoring"
    ALTERNATIVE_AUTHORING = "alternative_authoring"
    CHECKER_GENERATION = "checker_generation"


class AuthoringContext(StrictModel):
    context_id: Identifier
    role: Literal[
        "request", "baseline", "public_check", "contract", "scenario",
        "reference", "solver_safe",
    ]
    source: ArtifactRef
    locator: Text
    text: Text
    provenance_label: Literal[
        "historical_request", "reconstructed_specification", "existing_obligation"
    ]


class GenerationLimits(StrictModel):
    wall_seconds: Annotated[float, Field(gt=0, le=120)]
    cpu_seconds: Annotated[int, Field(gt=0, le=120)]
    stdin_bytes: Annotated[int, Field(gt=0, le=1_048_576)]
    output_bytes: Annotated[int, Field(gt=0, le=1_048_576)]
    file_size_bytes: Annotated[int, Field(gt=0, le=1_048_576)]
    input_tokens: Annotated[int, Field(gt=0, le=262_144)]
    output_tokens: Annotated[int, Field(gt=0, le=262_144)]
    physical_footprint_kill_bytes: Annotated[int, Field(gt=0)]
    physical_footprint_poll_seconds: Annotated[float, Field(gt=0, le=1)]
    cuda_memory_bytes: Annotated[int, Field(gt=0)] | None = None
    declared_memory_ceiling_bytes: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def token_envelope(self):
        if self.input_tokens + self.output_tokens > 262_144:
            raise ValueError("input and emitted output must fit the model context envelope")
        if self.declared_memory_ceiling_bytes <= self.physical_footprint_kill_bytes + (self.cuda_memory_bytes or 0):
            raise ValueError("declared memory ceiling must leave a guard band above process and CUDA limits")
        return self


def _fixed_identifier(value: str) -> bool:
    return not any(marker in value for marker in ("{{", "}}", "${", "<TBD", "TODO"))


class GenerationRequest(StrictModel):
    request_id: GenerationIdentifier
    response_id: GenerationIdentifier
    prompt_id: GenerationIdentifier
    stage: GenerationStage
    system_prompt: Text
    instruction: Text
    contexts: Annotated[tuple[AuthoringContext, ...], Field(min_length=1)]
    allowed_requirement_ids: tuple[Identifier, ...]
    limits: GenerationLimits
    seed: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def stage_boundary(self):
        identifiers = (
            self.request_id,
            self.response_id,
            self.prompt_id,
            *(item.context_id for item in self.contexts),
            *self.allowed_requirement_ids,
        )
        if any(not _fixed_identifier(value) for value in identifiers):
            raise ValueError("all request, response, prompt, context and requirement IDs must be fixed")
        context_ids = [item.context_id for item in self.contexts]
        if len(context_ids) != len(set(context_ids)):
            raise ValueError("context IDs must be unique")
        if len(self.allowed_requirement_ids) != len(set(self.allowed_requirement_ids)):
            raise ValueError("allowed requirement IDs must be unique")

        if self.stage in {GenerationStage.DISCOVERY, GenerationStage.INITIAL_AUTHORING}:
            for item in self.contexts:
                if item.role not in {"request", "baseline", "public_check"}:
                    raise ValueError("initial authoring accepts only request, baseline and public checks")
                if item.source.visibility not in {Visibility.PUBLIC, Visibility.AUTHORING}:
                    raise ValueError("initial authoring cannot receive private or evaluation artifacts")
                if item.source.kind in {"SourcePair", "reference", "reference-tree"}:
                    raise ValueError("reference implementation context is forbidden")
        elif self.stage is GenerationStage.SCENARIO_PLANNING:
            contracts = 0
            for item in self.contexts:
                if item.role == "contract":
                    contracts += 1
                    if (
                        item.source.kind != "RequirementContract"
                        or item.source.encoding != "json"
                        or item.source.visibility is not Visibility.AUTHORING
                    ):
                        raise ValueError(
                            "scenario planning requires an authoring RequirementContract artifact"
                        )
                    continue
                if item.role not in {"request", "baseline", "public_check"}:
                    raise ValueError("scenario planning context role is not allowlisted")
                if item.source.visibility not in {Visibility.PUBLIC, Visibility.AUTHORING}:
                    raise ValueError("scenario planning evidence must be public or authoring")
                if item.source.kind in {
                    "SourcePair",
                    "reference",
                    "reference-tree",
                    "ScenarioPlan",
                }:
                    raise ValueError("scenario planning cannot receive reference or plan data")
            if contracts != 1:
                raise ValueError("scenario planning requires exactly one frozen contract")
            if not self.allowed_requirement_ids:
                raise ValueError("scenario planning requires fixed requirement IDs")
        elif self.stage is GenerationStage.CONTROL_AUTHORING:
            contracts = 0
            baselines = 0
            scenarios = 0
            for item in self.contexts:
                if item.role == "contract":
                    contracts += 1
                    valid = (
                        item.source.kind == "RequirementContract"
                        and item.source.encoding == "json"
                        and item.source.visibility is Visibility.AUTHORING
                    )
                elif item.role == "baseline":
                    baselines += 1
                    valid = (
                        item.source.kind in {"source-archive", "runtime-discovery"}
                        and item.source.encoding == "bytes"
                        and item.source.visibility in {Visibility.PUBLIC, Visibility.AUTHORING}
                    )
                elif item.role == "request":
                    valid = (
                        item.source.kind == "authoring-request"
                        and item.source.encoding == "bytes"
                        and item.source.visibility in {Visibility.PUBLIC, Visibility.AUTHORING}
                    )
                elif item.role == "public_check":
                    valid = (
                        item.source.kind == "public-check"
                        and item.source.encoding == "bytes"
                        and item.source.visibility in {Visibility.PUBLIC, Visibility.AUTHORING}
                    )
                elif item.role == "scenario":
                    scenarios += 1
                    valid = (
                        item.source.kind == "ScenarioPlan"
                        and item.source.encoding == "json"
                        and item.source.visibility in {Visibility.PRIVATE, Visibility.EVALUATION}
                    )
                elif item.role == "reference":
                    valid = (
                        item.source.kind == "source-archive"
                        and item.source.encoding == "bytes"
                        and item.source.visibility in {Visibility.PRIVATE, Visibility.EVALUATION}
                    )
                else:
                    valid = False
                if not valid:
                    raise ValueError("control authoring context kind, encoding, or visibility is invalid")
            if contracts != 1 or baselines < 1 or scenarios > 1:
                raise ValueError("control authoring requires one frozen contract and baseline context")
        elif self.stage is GenerationStage.ALTERNATIVE_AUTHORING:
            contracts = 0
            baselines = 0
            for item in self.contexts:
                if item.role == "contract":
                    contracts += 1
                    valid = (
                        item.source.kind == "RequirementContract"
                        and item.source.encoding == "json"
                        and item.source.visibility is Visibility.AUTHORING
                    )
                elif item.role == "baseline":
                    baselines += 1
                    valid = (
                        item.source.kind in {"source-archive", "runtime-discovery"}
                        and item.source.encoding == "bytes"
                        and item.source.visibility in {Visibility.PUBLIC, Visibility.AUTHORING}
                    )
                elif item.role == "request":
                    valid = (
                        item.source.kind == "authoring-request"
                        and item.source.encoding == "bytes"
                        and item.source.visibility in {Visibility.PUBLIC, Visibility.AUTHORING}
                    )
                elif item.role == "public_check":
                    valid = (
                        item.source.kind == "public-check"
                        and item.source.encoding == "bytes"
                        and item.source.visibility in {Visibility.PUBLIC, Visibility.AUTHORING}
                    )
                elif item.role == "solver_safe":
                    valid = (
                        item.source.kind == "solver-safe-context"
                        and item.source.encoding == "bytes"
                        and item.source.visibility is Visibility.PUBLIC
                    )
                else:
                    valid = False
                if not valid:
                    raise ValueError(
                        "alternative authoring context kind, encoding, or visibility is invalid"
                    )
            if contracts != 1 or baselines < 1:
                raise ValueError(
                    "alternative authoring requires one visible frozen contract and baseline context"
                )
        else:
            for item in self.contexts:
                expected_kind = {"contract": "RequirementContract", "scenario": "ScenarioPlan"}.get(
                    item.role
                )
                if expected_kind is not None and item.source.kind != expected_kind:
                    raise ValueError("checker contract/scenario kind does not match its explicit role")
                if expected_kind is None and item.role not in {"request", "baseline", "public_check"}:
                    raise ValueError("checker context role is not allowlisted")
                if expected_kind is None and item.source.kind in {"SourcePair", "reference", "reference-tree"}:
                    raise ValueError("checker baseline context cannot contain reference implementation data")
                allowed = ({Visibility.AUTHORING, Visibility.PRIVATE, Visibility.EVALUATION}
                           if item.role == "contract" else
                           {Visibility.PRIVATE, Visibility.EVALUATION}
                           if item.role == "scenario" else
                           {Visibility.PUBLIC, Visibility.AUTHORING})
                if item.source.visibility not in allowed:
                    raise ValueError("checker context visibility does not match its role")
            roles = {item.role for item in self.contexts}
            if not {"contract", "scenario"}.issubset(roles):
                raise ValueError("checker generation requires explicit frozen contract and scenario data")
        return self


class GenerationUsage(StrictModel):
    input_tokens: Annotated[int, Field(ge=0)]
    input_token_ids: tuple[Annotated[int, Field(ge=0)], ...]
    output_tokens: Annotated[int, Field(ge=0)]
    token_ids: tuple[Annotated[int, Field(ge=0)], ...]
    selected_model_logprobs: tuple[
        Annotated[float, Field(le=0, allow_inf_nan=False)], ...
    ]
    sampling_policy: Literal["greedy_argmax"]
    behavior_logprobs: Literal[None] = None
    finish_reason: Literal["stop"]
    truncated: Literal[False]

    @model_validator(mode="after")
    def token_alignment(self):
        if self.input_tokens != len(self.input_token_ids):
            raise ValueError("actual input token IDs must align with input count")
        if self.output_tokens != len(self.token_ids) or len(self.token_ids) != len(
            self.selected_model_logprobs
        ):
            raise ValueError("token IDs and selected model logprobs must align with output count")
        return self


class GenerationCallRecord(StrictModel):
    attempt_id: Identifier
    recorded_at: UTCDateTime
    request_id: GenerationIdentifier
    response_id: GenerationIdentifier
    success: bool
    generation_succeeded: bool
    publication_complete: bool
    error_code: str | None
    archives: dict[str, ArtifactRef]


class GenerationAttemptMetadata(StrictModel):
    attempt_id: Identifier
    recorded_at: UTCDateTime
    producer: Literal["feature_rl.generation.LocalGenerationProvider"]
    protocol_version: Literal[3, 4]
    request_id: GenerationIdentifier
    response_id: GenerationIdentifier
    prompt_id: GenerationIdentifier
    request_sha256: Digest
    output_schema_sha256: Digest
    source_sha256: dict[str, Digest]
    configured_model_id: Text
    configured_model_revision: Revision
    model_config_sha256: Digest | None = None
    model_manifest_sha256: Digest
    dependency_manifest_sha256: Digest
    dependency_versions: dict[str, str] = Field(default_factory=dict)
    device: str | None = None
    dtype: str | None = None


class GenerationResult(StrictModel):
    content: object
    usage: GenerationUsage
    cost: CostRecord
    record: GenerationCallRecord

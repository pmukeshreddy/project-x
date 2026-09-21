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
    CONTROL_AUTHORING = "control_authoring"
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
    wall_seconds: Annotated[float, Field(gt=0, le=3600)]
    cpu_seconds: Annotated[int, Field(gt=0, le=120)]
    stdin_bytes: Annotated[int, Field(gt=0, le=1_048_576)]
    output_bytes: Annotated[int, Field(gt=0, le=1_048_576)]
    input_tokens: Annotated[int, Field(gt=0, le=262_144)]
    output_tokens: Annotated[int, Field(gt=0, le=262_144)]
    physical_footprint_kill_bytes: Annotated[int, Field(gt=0)]
    physical_footprint_poll_seconds: Annotated[float, Field(gt=0, le=1)]
    declared_memory_ceiling_bytes: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def token_envelope(self):
        if self.input_tokens + self.output_tokens > 262_144:
            raise ValueError("input and emitted output must fit the authoring context envelope")
        if self.declared_memory_ceiling_bytes <= self.physical_footprint_kill_bytes:
            raise ValueError("declared memory ceiling must leave a guard band above Codex process limit")
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
                else:
                    valid = False
                if not valid:
                    raise ValueError("control authoring context kind, encoding, or visibility is invalid")
            if contracts != 1 or baselines < 1 or scenarios > 1:
                raise ValueError("control authoring requires one frozen contract and baseline context")

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
    """Counts reported by Codex, not invented local token IDs or log probabilities."""
    input_tokens: Annotated[int, Field(ge=0)]
    cached_input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    cache_write_input_tokens: Annotated[int, Field(ge=0)] | None = Field(default=None, exclude_if=lambda value: value is None)
    reasoning_output_tokens: Annotated[int, Field(ge=0)] | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def cached_subset(self):
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached input tokens exceed total input tokens")
        if self.cache_write_input_tokens is not None and self.cache_write_input_tokens > self.input_tokens:
            raise ValueError("cache write tokens exceed total input tokens")
        if self.reasoning_output_tokens is not None and self.reasoning_output_tokens > self.output_tokens:
            raise ValueError("reasoning tokens exceed total output tokens")
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
    producer: Literal["feature_rl.generation.CodexGenerationProvider"]
    protocol_version: Literal["codex-exec-v1"]
    request_id: GenerationIdentifier
    response_id: GenerationIdentifier
    prompt_id: GenerationIdentifier
    request_sha256: Digest
    output_schema_sha256: Digest
    configured_model_id: Literal["gpt-6-astra"]
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"]


class GenerationResult(StrictModel):
    content: object
    usage: GenerationUsage
    cost: CostRecord
    record: GenerationCallRecord

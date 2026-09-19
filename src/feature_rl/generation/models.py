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


class GenerationStage(str, Enum):
    DISCOVERY = "discovery"
    INITIAL_AUTHORING = "initial_authoring"
    CHECKER_GENERATION = "checker_generation"


class AuthoringContext(StrictModel):
    context_id: Identifier
    role: Literal["request", "baseline", "public_check", "contract", "scenario"]
    source: ArtifactRef
    locator: Text
    text: Text
    provenance_label: Literal[
        "historical_request", "reconstructed_specification", "existing_obligation"
    ]


class GenerationLimits(StrictModel):
    measurement_profile: Literal["tiny_smoke_2048x128", "larger_unqualified"]
    wall_seconds: Annotated[float, Field(gt=0, le=120)]
    cpu_seconds: Annotated[int, Field(gt=0, le=120)]
    stdin_bytes: Annotated[int, Field(gt=0, le=1_048_576)]
    output_bytes: Annotated[int, Field(gt=0, le=1_048_576)]
    file_size_bytes: Annotated[int, Field(gt=0, le=1_048_576)]
    input_tokens: Annotated[int, Field(gt=0, le=262_144)]
    output_tokens: Annotated[int, Field(gt=0, le=262_144)]
    mlx_memory_guideline_bytes: Literal[3_758_096_384]
    mlx_wired_limit_bytes: Literal[3_758_096_384]
    mlx_cache_limit_bytes: Literal[0]
    physical_footprint_kill_bytes: Literal[4_294_967_296]
    physical_footprint_poll_seconds: Literal[0.02]
    declared_memory_ceiling_bytes: Literal[5_368_709_120]

    @model_validator(mode="after")
    def token_envelope(self):
        if self.input_tokens + self.output_tokens > 262_144:
            raise ValueError("input and emitted output must fit the model context envelope")
        if self.measurement_profile == "tiny_smoke_2048x128" and (
            self.input_tokens > 2048 or self.output_tokens > 128
        ):
            raise ValueError("tiny smoke profile is qualified only through 2048 input/128 output")
        return self


def _fixed_identifier(value: str) -> bool:
    return not any(marker in value for marker in ("{{", "}}", "${", "<TBD", "TODO"))


class GenerationRequest(StrictModel):
    request_id: Identifier
    response_id: Identifier
    prompt_id: Identifier
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
    request_id: Identifier
    response_id: Identifier
    success: bool
    generation_succeeded: bool
    publication_complete: bool
    error_code: str | None
    archives: dict[str, ArtifactRef]


class GenerationAttemptMetadata(StrictModel):
    attempt_id: Identifier
    recorded_at: UTCDateTime
    producer: Literal["feature_rl.generation.LocalGenerationProvider"]
    protocol_version: Literal[3]
    request_id: Identifier
    response_id: Identifier
    prompt_id: Identifier
    request_sha256: Digest
    output_schema_sha256: Digest
    source_sha256: dict[str, Digest]
    configured_model_id: Text
    configured_model_revision: Revision
    model_config_sha256: Digest
    model_manifest_sha256: Digest
    dependency_manifest_sha256: Digest
    dependency_versions: dict[str, str]


class GenerationResult(StrictModel):
    content: object
    usage: GenerationUsage
    cost: CostRecord
    record: GenerationCallRecord

"""Exact JSONL event protocol emitted by the isolated Transformers worker."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from feature_rl.contracts import Digest, NonnegativeFloat, NonnegativeInt, StrictModel

from .models import GenerationIdentifier

PROTOCOL_VERSION = 4


def _exact_literal(expected):
    def validate(value):
        if type(value) is not type(expected) or value != expected:
            raise ValueError(
                f"expected exact JSON {type(expected).__name__} literal {expected!r}"
            )
        return value

    return validate


ExactProtocolVersion = Annotated[Literal[4], BeforeValidator(_exact_literal(4))]
ExactTrue = Annotated[Literal[True], BeforeValidator(_exact_literal(True))]
ExactFalse = Annotated[Literal[False], BeforeValidator(_exact_literal(False))]


class EventIdentity(StrictModel):
    protocol_version: ExactProtocolVersion
    request_id: GenerationIdentifier
    response_id: GenerationIdentifier
    prompt_id: GenerationIdentifier


class IdentityValidated(EventIdentity):
    event: Literal["identity_validated"]
    model_id: str
    revision: str
    config_sha256: Digest
    tokenizer_sha256: Digest
    weights_sha256: Digest
    model_manifest_sha256: Digest
    dependency_manifest_sha256: Digest
    dependency_versions: dict[str, str]
    seed: NonnegativeInt
    prompt_sha256: Digest
    worker_source_sha256: Digest
    offline_environment: dict[str, str]
    local_files_only: ExactTrue
    remote_code: ExactFalse
    device: str
    dtype: str


class InputAccepted(EventIdentity):
    event: Literal["input_accepted"]
    actual_input_tokens: NonnegativeInt
    input_token_ids: tuple[NonnegativeInt, ...]
    max_input_tokens: NonnegativeInt


class InputRejected(EventIdentity):
    event: Literal["input_rejected"]
    actual_input_tokens: NonnegativeInt
    max_input_tokens: NonnegativeInt
    model_load_started: ExactFalse
    inference_started: ExactFalse


class MemoryControlsSet(EventIdentity):
    event: Literal["memory_controls_set"]
    device: str
    cuda_memory_bytes: NonnegativeInt | None


class ModelLoaded(EventIdentity):
    event: Literal["model_loaded"]
    model_id: str
    revision: str
    fresh_process: ExactTrue
    fresh_prompt_cache: ExactTrue
    active_memory_bytes: NonnegativeInt | None
    peak_memory_bytes: NonnegativeInt | None
    cache_memory_bytes: NonnegativeInt | None


class TokenEmitted(EventIdentity):
    event: Literal["token"]
    position: Annotated[int, Field(gt=0)]
    token_id: NonnegativeInt
    selected_model_logprob: Annotated[float, Field(le=0, allow_inf_nan=False)]
    text_fragment: str


class Completed(EventIdentity):
    event: Literal["completed"]
    model_id: str
    revision: str
    input_tokens: NonnegativeInt
    output_tokens: NonnegativeInt
    max_output_tokens: NonnegativeInt
    finish_reason: Literal["stop", "length"]
    sampling_policy: Literal["greedy_argmax"]
    truncated: bool
    output_text: str
    inference_seconds: NonnegativeFloat
    total_seconds: NonnegativeFloat
    active_memory_bytes: NonnegativeInt | None
    peak_memory_bytes: NonnegativeInt | None
    cache_memory_bytes: NonnegativeInt | None
    fresh_process: ExactTrue
    fresh_prompt_cache: ExactTrue


EVENT_MODELS = {
    "identity_validated": IdentityValidated,
    "input_accepted": InputAccepted,
    "input_rejected": InputRejected,
    "memory_controls_set": MemoryControlsSet,
    "model_loaded": ModelLoaded,
    "token": TokenEmitted,
    "completed": Completed,
}

SuccessfulEvent = (
    IdentityValidated | InputAccepted | MemoryControlsSet | ModelLoaded | TokenEmitted | Completed
)

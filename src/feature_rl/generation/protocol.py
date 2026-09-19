"""Exact JSONL event protocol emitted by the isolated MLX worker."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from feature_rl.contracts import Digest, Identifier, NonnegativeFloat, NonnegativeInt, StrictModel

PROTOCOL_VERSION = 3


class EventIdentity(StrictModel):
    protocol_version: Literal[3]
    request_id: Identifier
    response_id: Identifier
    prompt_id: Identifier


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
    local_files_only: Literal[True]
    remote_code: Literal[False]


class InputAccepted(EventIdentity):
    event: Literal["input_accepted"]
    actual_input_tokens: NonnegativeInt
    input_token_ids: tuple[NonnegativeInt, ...]
    max_input_tokens: NonnegativeInt


class InputRejected(EventIdentity):
    event: Literal["input_rejected"]
    actual_input_tokens: NonnegativeInt
    max_input_tokens: NonnegativeInt
    model_load_started: Literal[False]
    inference_started: Literal[False]


class MemoryControlsSet(EventIdentity):
    event: Literal["memory_controls_set"]
    mlx_memory_guideline_bytes: NonnegativeInt
    mlx_cache_limit_bytes: NonnegativeInt
    mlx_wired_limit_bytes: NonnegativeInt
    previous_memory_limit_bytes: NonnegativeInt
    previous_cache_limit_bytes: NonnegativeInt
    previous_wired_limit_bytes: NonnegativeInt


class ModelLoaded(EventIdentity):
    event: Literal["model_loaded"]
    model_id: str
    revision: str
    fresh_process: Literal[True]
    fresh_prompt_cache: Literal[True]
    active_memory_bytes: NonnegativeInt
    peak_memory_bytes: NonnegativeInt
    cache_memory_bytes: NonnegativeInt


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
    active_memory_bytes: NonnegativeInt
    peak_memory_bytes: NonnegativeInt
    cache_memory_bytes: NonnegativeInt
    fresh_process: Literal[True]
    fresh_prompt_cache: Literal[True]


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

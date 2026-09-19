"""Strict provider boundary for one local, offline MLX generation call."""

from __future__ import annotations

import base64
import json
import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from feature_rl.artifacts import canonical_json
from feature_rl.contracts import ArtifactRef, CostRecord, StrictModel, Visibility

from .backend import BackendConfig, MODEL_ID, MODEL_REVISION, VerifiedBackend
from .models import (
    GenerationCallRecord,
    GenerationRequest,
    GenerationResult,
    GenerationStage,
    GenerationUsage,
)
from .runner import BoundedProcessRunner, ProcessOutcome

ArchiveWriter = Callable[[bytes, str, Visibility], ArtifactRef]
ARCHIVE_KINDS = (
    "request", "response", "retrieval", "schema", "options", "provenance",
    "usage", "cost", "events", "status",
)


class GenerationProviderError(RuntimeError):
    def __init__(self, message: str, record: GenerationCallRecord):
        super().__init__(message)
        self.record = record


def _json_no_duplicates(data: str) -> object:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(
        data,
        object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"nonfinite JSON: {value}")),
    )


def _prompt(request: GenerationRequest, schema: type[StrictModel]) -> str:
    sources = [
        {
            "context_id": item.context_id,
            "role": item.role,
            "source": item.source.model_dump(mode="json"),
            "locator": item.locator,
            "provenance_label": item.provenance_label,
            "text": item.text,
        }
        for item in request.contexts
    ]
    envelope = {
        "response_id": request.response_id,
        "source_ids": [item.context_id for item in request.contexts],
        "requirement_ids": list(request.allowed_requirement_ids),
        "content": schema.model_json_schema(),
    }
    return "\n\n".join(
        (
            request.system_prompt,
            f"PROMPT_ID: {request.prompt_id}\nREQUEST_ID: {request.request_id}\nRESPONSE_ID: {request.response_id}",
            "INSTRUCTION:\n" + request.instruction,
            "ATTRIBUTABLE_CONTEXT_JSON:\n" + json.dumps(sources, sort_keys=True, separators=(",", ":")),
            "Return exactly one JSON object, without markdown or commentary. The object must have "
            "exactly response_id, source_ids, requirement_ids, and content. Copy response_id exactly; "
            "source_ids must contain every supplied context ID once in supplied order; requirement_ids "
            "may contain only the declared IDs. CONTENT_AND_ENVELOPE_SCHEMA:\n"
            + json.dumps(envelope, sort_keys=True, separators=(",", ":")),
        )
    )


def _parse_events(raw: bytes, request: GenerationRequest, verified: VerifiedBackend):
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("worker events are not UTF-8") from error
    lines = text.splitlines()
    if not lines:
        raise ValueError("worker emitted no events")
    events = []
    for line in lines:
        try:
            event = _json_no_duplicates(line)
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError("worker event is malformed JSON") from error
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            raise ValueError("worker event is malformed")
        if event["event"] not in {
            "identity_validated", "input_accepted", "memory_controls_set", "model_loaded",
            "token", "completed",
        }:
            raise ValueError(f"unknown or forbidden worker event {event['event']!r}")
        events.append(event)
    names = [item["event"] for item in events]
    if names[:4] != ["identity_validated", "input_accepted", "memory_controls_set", "model_loaded"]:
        raise ValueError("worker event prefix is incomplete or out of order")
    if names[-1] != "completed" or any(name != "token" for name in names[4:-1]):
        raise ValueError("worker token/completion event order is invalid")
    identity, accepted, memory, loaded, completed = events[0], events[1], events[2], events[3], events[-1]
    for event in (identity, completed):
        if event.get("model_id") != verified.model.model_id or event.get("revision") != verified.model.revision:
            raise ValueError("worker model identity mismatch")
    if accepted.get("actual_input_tokens") != completed.get("input_tokens"):
        raise ValueError("input usage is inconsistent")
    input_tokens = completed.get("input_tokens")
    if type(input_tokens) is not int or not 0 < input_tokens <= request.limits.input_tokens:
        raise ValueError("actual input token count violates the limit")
    if accepted.get("max_input_tokens") != request.limits.input_tokens:
        raise ValueError("worker input limit mismatch")
    input_token_ids = accepted.get("input_token_ids")
    if (
        not isinstance(input_token_ids, list)
        or len(input_token_ids) != input_tokens
        or any(type(token_id) is not int or token_id < 0 for token_id in input_token_ids)
    ):
        raise ValueError("actual templated input token IDs are missing or inconsistent")
    expected_memory = {
        "mlx_memory_guideline_bytes": request.limits.mlx_memory_guideline_bytes,
        "mlx_cache_limit_bytes": request.limits.mlx_cache_limit_bytes,
        "mlx_wired_limit_bytes": request.limits.mlx_wired_limit_bytes,
    }
    if any(memory.get(key) != value for key, value in expected_memory.items()):
        raise ValueError("worker memory policy mismatch")
    if loaded.get("fresh_process") is not True or loaded.get("fresh_prompt_cache") is not True:
        raise ValueError("worker did not attest a fresh process and prompt cache")
    token_events = events[4:-1]
    token_ids: list[int] = []
    model_logprobs: list[float] = []
    for position, event in enumerate(token_events, start=1):
        token_id, logprob = event.get("token_id"), event.get("selected_model_logprob")
        if event.get("position") != position or type(token_id) is not int or token_id < 0:
            raise ValueError("token event position or ID is invalid")
        if type(logprob) not in {int, float} or not math.isfinite(logprob):
            raise ValueError("token event selected model logprob is missing or nonfinite")
        token_ids.append(token_id)
        model_logprobs.append(float(logprob))
    output_tokens = completed.get("output_tokens")
    if output_tokens != len(token_ids) or not 0 <= output_tokens <= request.limits.output_tokens:
        raise ValueError("output token usage is inconsistent or exceeds the cap")
    if completed.get("max_output_tokens") != request.limits.output_tokens:
        raise ValueError("worker output limit mismatch")
    if completed.get("truncated") is not False:
        raise ValueError("worker output is truncated at the emitted-token cap")
    if completed.get("finish_reason") != "stop":
        raise ValueError("worker did not finish at a stop token")
    if completed.get("sampling_policy") != "greedy_argmax":
        raise ValueError("worker sampling policy mismatch")
    if completed.get("fresh_process") is not True or completed.get("fresh_prompt_cache") is not True:
        raise ValueError("completion lacks fresh-context attestation")
    if not isinstance(completed.get("output_text"), str):
        raise ValueError("worker completion output is missing")
    usage = GenerationUsage(
        input_tokens=input_tokens,
        input_token_ids=tuple(input_token_ids),
        output_tokens=output_tokens,
        token_ids=tuple(token_ids),
        selected_model_logprobs=tuple(model_logprobs),
        sampling_policy="greedy_argmax",
        behavior_logprobs=None,
        finish_reason="stop",
        truncated=False,
    )
    return events, completed["output_text"], usage


def _observed_usage(raw: bytes) -> dict:
    """Retain internally consistent token facts without accepting a response."""
    observed = {
        "event_stream_complete": False,
        "input_tokens": None,
        "input_token_ids": None,
        "output_tokens_observed": 0,
        "token_ids": [],
        "selected_model_logprobs": [],
        "sampling_policy": "greedy_argmax",
        "behavior_logprobs": None,
        "observation_error": None,
    }
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        observed["observation_error"] = "non-UTF-8 event stream"
        return observed
    expected_position = 1
    for line in lines:
        try:
            event = _json_no_duplicates(line)
        except (json.JSONDecodeError, ValueError):
            observed["observation_error"] = "malformed event"
            break
        if not isinstance(event, dict):
            observed["observation_error"] = "non-object event"
            break
        name = event.get("event")
        if name == "input_rejected":
            count = event.get("actual_input_tokens")
            if type(count) is int and count >= 0:
                observed["input_tokens"] = count
        elif name == "input_accepted":
            count, ids = event.get("actual_input_tokens"), event.get("input_token_ids")
            if (
                type(count) is int
                and count >= 0
                and isinstance(ids, list)
                and len(ids) == count
                and all(type(token_id) is int and token_id >= 0 for token_id in ids)
            ):
                observed["input_tokens"] = count
                observed["input_token_ids"] = ids
            else:
                observed["observation_error"] = "invalid accepted-input usage"
                break
        elif name == "token":
            token_id, logprob = event.get("token_id"), event.get("selected_model_logprob")
            if (
                event.get("position") != expected_position
                or type(token_id) is not int
                or token_id < 0
                or type(logprob) not in {int, float}
                or not math.isfinite(logprob)
            ):
                observed["observation_error"] = "invalid emitted-token usage"
                break
            observed["token_ids"].append(token_id)
            observed["selected_model_logprobs"].append(float(logprob))
            expected_position += 1
        elif name == "completed":
            observed["event_stream_complete"] = (
                observed["input_tokens"] == event.get("input_tokens")
                and len(observed["token_ids"]) == event.get("output_tokens")
            )
        elif name not in {"identity_validated", "memory_controls_set", "model_loaded"}:
            observed["observation_error"] = f"unknown event {name!r}"
            break
    observed["output_tokens_observed"] = len(observed["token_ids"])
    return observed


def _parse_envelope(
    output_text: str, request: GenerationRequest, schema: type[StrictModel]
) -> StrictModel:
    try:
        envelope = _json_no_duplicates(output_text)
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError("model response is malformed JSON") from error
    if not isinstance(envelope, dict) or set(envelope) != {
        "response_id", "source_ids", "requirement_ids", "content"
    }:
        raise ValueError("model response envelope has missing or extra fields")
    if envelope["response_id"] != request.response_id:
        raise ValueError("model response ID mismatch")
    expected_sources = [item.context_id for item in request.contexts]
    if envelope["source_ids"] != expected_sources:
        raise ValueError("model response has missing, unknown, duplicate, or reordered source provenance")
    requirement_ids = envelope["requirement_ids"]
    if not isinstance(requirement_ids, list) or len(requirement_ids) != len(set(requirement_ids)):
        raise ValueError("model response requirement provenance is malformed")
    if not set(requirement_ids).issubset(request.allowed_requirement_ids):
        raise ValueError("model response cites an unknown requirement ID")
    try:
        return schema.model_validate(envelope["content"])
    except ValidationError as error:
        raise ValueError("model response content violates its strict schema") from error


class LocalGenerationProvider:
    def __init__(
        self,
        *,
        backend: BackendConfig,
        archive: ArchiveWriter,
        runner: BoundedProcessRunner | None = None,
    ):
        self._backend = backend
        self._archive = archive
        self._runner = runner or BoundedProcessRunner()

    def generate(
        self, request: GenerationRequest, output_schema: type[StrictModel]
    ) -> GenerationResult:
        if not isinstance(request, GenerationRequest):
            raise TypeError("request must be a validated GenerationRequest")
        if not isinstance(output_schema, type) or not issubclass(output_schema, StrictModel):
            raise TypeError("output schema must be a StrictModel subclass")
        visibility = (
            Visibility.AUTHORING
            if request.stage in {GenerationStage.DISCOVERY, GenerationStage.INITIAL_AUTHORING}
            else Visibility.PRIVATE
        )
        prompt = _prompt(request, output_schema)
        schema_payload = output_schema.model_json_schema()
        outcome: ProcessOutcome | None = None
        verified: VerifiedBackend | None = None
        events: list[dict] = []
        usage: GenerationUsage | None = None
        observed_usage = _observed_usage(b"")
        content: StrictModel | None = None
        error: Exception | None = None
        try:
            verified = self._backend.verify()
            worker_input = canonical_json(
                {
                    "protocol_version": 2,
                    "model_directory": str(self._backend.model_directory.resolve()),
                    "model_id": MODEL_ID,
                    "revision": MODEL_REVISION,
                    "response_id": request.response_id,
                    "prompt": prompt,
                    "max_input_tokens": request.limits.input_tokens,
                    "max_output_tokens": request.limits.output_tokens,
                    "seed": request.seed,
                    "memory": {
                        "guideline_bytes": request.limits.mlx_memory_guideline_bytes,
                        "wired_limit_bytes": request.limits.mlx_wired_limit_bytes,
                        "cache_limit_bytes": request.limits.mlx_cache_limit_bytes,
                    },
                }
            )
            if len(worker_input) > request.limits.stdin_bytes:
                raise ValueError("serialized worker request exceeds stdin cap")
            with tempfile.TemporaryDirectory(prefix="feature-rl-generation-") as directory:
                root = Path(directory)
                worker = root / "worker.py"
                shutil.copyfile(Path(__file__).with_name("_worker.py"), worker)
                cache = root / "cache"
                cache.mkdir()
                environment = {
                    "PATH": "/usr/bin:/bin",
                    "TMPDIR": str(root),
                    "HF_HOME": str(cache),
                    "HF_HUB_CACHE": str(cache / "hub"),
                    "HF_XET_CACHE": str(cache / "xet"),
                    "TRANSFORMERS_CACHE": str(cache / "transformers"),
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "HF_DATASETS_OFFLINE": "1",
                    "TOKENIZERS_PARALLELISM": "false",
                    "LC_ALL": "C",
                }
                if "HOME" in os.environ:
                    environment["HOME"] = os.environ["HOME"]
                outcome = self._runner.run(
                    command=(str(self._backend.python_executable), "-I", "-X", "utf8", str(worker)),
                    stdin=worker_input,
                    cwd=root,
                    environment=environment,
                    limits=request.limits,
                )
            observed_usage = _observed_usage(outcome.stdout)
            if outcome.termination != "process_exit":
                raise ValueError(f"worker terminated by {outcome.termination}")
            if outcome.exit_status != 0:
                raise ValueError(f"worker exited with status {outcome.exit_status}")
            if not outcome.process_group_cleanup_verified:
                raise ValueError("worker process group cleanup was not verified")
            if outcome.memory_samples <= 0:
                raise ValueError("worker physical footprint was never sampled")
            events, output_text, usage = _parse_events(outcome.stdout, request, verified)
            content = _parse_envelope(output_text, request, output_schema)
        except Exception as caught:  # Every failure is archived below before it escapes.
            error = caught

        cost = CostRecord(
            category="authoring",
            wall_seconds=outcome.wall_seconds if outcome else None,
            cpu_seconds=outcome.cpu_seconds if outcome else None,
            gpu_seconds=None,
            input_tokens=(usage.input_tokens if usage else observed_usage["input_tokens"]),
            output_tokens=(
                usage.output_tokens
                if usage
                else observed_usage["output_tokens_observed"]
                if outcome
                else None
            ),
            human_minutes=None,
            usd=None,
            measurement="partial" if outcome or usage else "unknown",
            note=(
                "Local execution: wall/CPU and accepted token counts are measured when present; "
                "GPU time, energy, human time and currency cost are unknown. No remote API was used."
                if outcome or usage
                else "Generation did not start; all execution and monetary costs are unknown."
            ),
        )
        response_payload = {
            "stdout_base64": base64.b64encode(outcome.stdout).decode() if outcome else "",
            "stderr_base64": base64.b64encode(outcome.stderr).decode() if outcome else "",
            "termination": outcome.termination if outcome else None,
            "exit_status": outcome.exit_status if outcome else None,
            "wall_seconds": outcome.wall_seconds if outcome else None,
            "cpu_seconds": outcome.cpu_seconds if outcome else None,
            "memory_samples": outcome.memory_samples if outcome else None,
            "max_sampled_physical_footprint_bytes": (
                outcome.max_sampled_physical_footprint_bytes if outcome else None
            ),
            "max_reported_lifetime_physical_footprint_bytes": (
                outcome.max_reported_lifetime_physical_footprint_bytes if outcome else None
            ),
            "breach_sample": (
                outcome.breach_sample.__dict__ if outcome and outcome.breach_sample else None
            ),
            "process_group_cleanup_verified": (
                outcome.process_group_cleanup_verified if outcome else None
            ),
        }
        provenance_payload = {
            "expected_source_ids": [item.context_id for item in request.contexts],
            "allowed_requirement_ids": list(request.allowed_requirement_ids),
            "observed": content is not None,
        }
        payloads = {
            "request": request.model_dump(mode="json"),
            "response": response_payload,
            "retrieval": {"contexts": [item.model_dump(mode="json") for item in request.contexts]},
            "schema": schema_payload,
            "options": {
                "seed": request.seed,
                "limits": request.limits.model_dump(mode="json"),
                "model": verified.model.model_dump(mode="json") if verified else None,
                "resource_enforcement": {
                    "wall": "external_monotonic_deadline",
                    "cpu": "kernel_rlimit_cpu",
                    "file_size": "kernel_rlimit_fsize",
                    "memory": "sampled_proc_pid_rusage_20ms_with_1GiB_guard_band",
                },
            },
            "provenance": provenance_payload,
            "usage": (
                usage.model_dump(mode="json")
                if usage
                else {"accepted_response_usage": False, "observed": observed_usage}
            ),
            "cost": cost.model_dump(mode="json"),
            "events": events,
        }
        refs: dict[str, ArtifactRef] = {}
        for name in ARCHIVE_KINDS[:-1]:
            data = outcome.stdout if name == "events" and outcome else canonical_json(payloads[name])
            refs[name] = self._archive(data, f"generation-{name}", visibility)
        status = {
            "request_id": request.request_id,
            "response_id": request.response_id,
            "success": error is None,
            "error_type": type(error).__name__ if error else None,
            "error": str(error) if error else None,
            "archive_refs": {key: value.model_dump(mode="json") for key, value in refs.items()},
        }
        refs["status"] = self._archive(canonical_json(status), "generation-status", visibility)
        record = GenerationCallRecord(
            request_id=request.request_id,
            response_id=request.response_id,
            success=error is None,
            error_code=type(error).__name__ if error else None,
            archives=refs,
        )
        if error is not None:
            raise GenerationProviderError(str(error), record) from error
        assert content is not None and usage is not None
        return GenerationResult(content=content, usage=usage, cost=cost, record=record)

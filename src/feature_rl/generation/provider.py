"""Strict provider boundary for one local, offline MLX generation call."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from feature_rl.artifacts import canonical_json
from feature_rl.contracts import ArtifactRef, CostRecord, StrictModel, Visibility

from .backend import (
    DEPENDENCY_MANIFEST_SHA256,
    EXPECTED_DEPENDENCIES,
    MODEL_CONFIG_SHA256,
    MODEL_ID,
    MODEL_MANIFEST_SHA256,
    MODEL_REVISION,
    BackendConfig,
    VerifiedBackend,
)
from .models import (
    GenerationAttemptMetadata,
    GenerationCallRecord,
    GenerationRequest,
    GenerationResult,
    GenerationStage,
    GenerationUsage,
)
from .runner import BoundedProcessRunner, ProcessOutcome
from .protocol import (
    EVENT_MODELS,
    PROTOCOL_VERSION,
    Completed,
    IdentityValidated,
    InputAccepted,
    InputRejected,
    MemoryControlsSet,
    ModelLoaded,
    TokenEmitted,
)

ArchiveWriter = Callable[[bytes, str, Visibility], ArtifactRef]
ARCHIVE_KINDS = (
    "attempt", "request", "response", "retrieval", "schema", "options", "provenance",
    "usage", "cost", "events", "status",
)


class GenerationProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        record: GenerationCallRecord,
        *,
        cost: CostRecord | None = None,
        response: dict | None = None,
        usage_observation: dict | None = None,
        recovery: "GenerationPublicationRecovery | None" = None,
    ):
        super().__init__(message)
        self.record = record
        self.cost = cost
        self.response = response
        self.usage_observation = usage_observation
        self.recovery = recovery

    def replay_publication(self, archive: ArchiveWriter) -> GenerationCallRecord:
        if self.recovery is None:
            raise RuntimeError("this provider failure has no pending archive publication")
        return self.recovery.replay(archive)


@dataclass(frozen=True)
class _ArchivePayload:
    name: str
    data: bytes
    kind: str
    visibility: Visibility


@dataclass(frozen=True)
class GenerationPublicationRecovery:
    """Bounded retained payloads that can be published without rerunning generation."""

    attempt_id: str
    recorded_at: datetime
    request_id: str
    response_id: str
    payloads: tuple[_ArchivePayload, ...]
    published_refs: tuple[tuple[str, ArtifactRef], ...]
    generation_succeeded: bool
    underlying_error_code: str | None
    underlying_error: str | None
    publication_error: str
    cost: CostRecord | None
    response: dict | None
    usage_observation: dict | None

    def replay(self, archive: ArchiveWriter) -> GenerationCallRecord:
        refs = dict(self.published_refs)
        try:
            for payload in self.payloads:
                if payload.name in refs:
                    continue
                refs[payload.name] = ArtifactRef.model_validate(
                    archive(payload.data, payload.kind, payload.visibility)
                )
            status = {
                "attempt_id": self.attempt_id,
                "recorded_at": self.recorded_at.isoformat(),
                "request_id": self.request_id,
                "response_id": self.response_id,
                "success": self.generation_succeeded,
                "generation_succeeded": self.generation_succeeded,
                "publication_complete": True,
                "publication_recovered": True,
                "publication_failure": self.publication_error,
                "error_type": self.underlying_error_code,
                "error": self.underlying_error,
                "archive_refs": {key: value.model_dump(mode="json") for key, value in refs.items()},
            }
            visibility = self.payloads[0].visibility
            refs["status"] = ArtifactRef.model_validate(
                archive(canonical_json(status), "generation-status", visibility)
            )
        except Exception as caught:
            retry = GenerationPublicationRecovery(
                **{
                    **self.__dict__,
                    "published_refs": tuple(refs.items()),
                    "publication_error": f"{type(caught).__name__}: {caught}",
                }
            )
            record = GenerationCallRecord(
                attempt_id=self.attempt_id,
                recorded_at=self.recorded_at,
                request_id=self.request_id,
                response_id=self.response_id,
                success=False,
                generation_succeeded=self.generation_succeeded,
                publication_complete=False,
                error_code="ArchivePublicationError",
                archives=refs,
            )
            raise GenerationProviderError(
                f"archive publication replay failed: {caught}",
                record,
                cost=self.cost,
                response=self.response,
                usage_observation=self.usage_observation,
                recovery=retry,
            ) from caught
        return GenerationCallRecord(
            attempt_id=self.attempt_id,
            recorded_at=self.recorded_at,
            request_id=self.request_id,
            response_id=self.response_id,
            success=self.generation_succeeded,
            generation_succeeded=self.generation_succeeded,
            publication_complete=True,
            error_code=self.underlying_error_code,
            archives=refs,
        )


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


def _decode_event(line: str):
    try:
        plain = _json_no_duplicates(line)
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError("worker event is malformed JSON") from error
    if not isinstance(plain, dict) or not isinstance(plain.get("event"), str):
        raise ValueError("worker event is malformed")
    model = EVENT_MODELS.get(plain["event"])
    if model is None:
        raise ValueError(f"unknown or forbidden worker event {plain['event']!r}")
    try:
        return model.model_validate_json(line)
    except ValidationError as error:
        raise ValueError(
            f"worker {plain['event']!r} event violates protocol v{PROTOCOL_VERSION}: {error}"
        ) from error


def _check_event_request_identity(event, request: GenerationRequest) -> None:
    if (
        event.protocol_version != PROTOCOL_VERSION
        or event.request_id != request.request_id
        or event.response_id != request.response_id
        or event.prompt_id != request.prompt_id
    ):
        raise ValueError("worker event request/protocol identity mismatch")


def _parse_events(
    raw: bytes,
    request: GenerationRequest,
    verified: VerifiedBackend,
    *,
    prompt_sha256: str,
    worker_source_sha256: str,
):
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("worker events are not UTF-8") from error
    lines = text.splitlines()
    if not lines:
        raise ValueError("worker emitted no events")
    events = []
    for line in lines:
        event = _decode_event(line)
        _check_event_request_identity(event, request)
        events.append(event)
    names = [item.event for item in events]
    if names[:4] != ["identity_validated", "input_accepted", "memory_controls_set", "model_loaded"]:
        raise ValueError("worker event prefix is incomplete or out of order")
    if names[-1] != "completed" or any(name != "token" for name in names[4:-1]):
        raise ValueError("worker token/completion event order is invalid")
    identity, accepted, memory, loaded, completed = events[0], events[1], events[2], events[3], events[-1]
    if not isinstance(identity, IdentityValidated) or not isinstance(accepted, InputAccepted):
        raise ValueError("worker identity/input events have wrong protocol types")
    if not isinstance(memory, MemoryControlsSet) or not isinstance(loaded, ModelLoaded):
        raise ValueError("worker memory/load events have wrong protocol types")
    if not isinstance(completed, Completed):
        raise ValueError("worker completion event has wrong protocol type")
    expected_offline = {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
    }
    if (
        identity.model_id != verified.model.model_id
        or identity.revision != verified.model.revision
        or identity.config_sha256 != verified.model.config_sha256
        or identity.tokenizer_sha256 != verified.model.tokenizer_sha256
        or identity.weights_sha256 != verified.model.weights_sha256
        or identity.model_manifest_sha256 != verified.model_manifest_sha256
        or identity.dependency_manifest_sha256 != verified.dependency_manifest_sha256
        or identity.dependency_versions != verified.dependencies.versions
        or identity.seed != request.seed
        or identity.prompt_sha256 != prompt_sha256
        or identity.worker_source_sha256 != worker_source_sha256
        or identity.offline_environment != expected_offline
        or identity.local_files_only is not True
        or identity.remote_code is not False
    ):
        raise ValueError("worker backend/offline/configuration identity mismatch")
    for event in (loaded, completed):
        if event.model_id != verified.model.model_id or event.revision != verified.model.revision:
            raise ValueError("worker model identity mismatch")
    if accepted.actual_input_tokens != completed.input_tokens:
        raise ValueError("input usage is inconsistent")
    input_tokens = completed.input_tokens
    if type(input_tokens) is not int or not 0 < input_tokens <= request.limits.input_tokens:
        raise ValueError("actual input token count violates the limit")
    if accepted.max_input_tokens != request.limits.input_tokens:
        raise ValueError("worker input limit mismatch")
    input_token_ids = accepted.input_token_ids
    if (
        len(input_token_ids) != input_tokens
        or any(type(token_id) is not int or token_id < 0 for token_id in input_token_ids)
    ):
        raise ValueError("actual templated input token IDs are missing or inconsistent")
    expected_memory = {
        "mlx_memory_guideline_bytes": request.limits.mlx_memory_guideline_bytes,
        "mlx_cache_limit_bytes": request.limits.mlx_cache_limit_bytes,
        "mlx_wired_limit_bytes": request.limits.mlx_wired_limit_bytes,
    }
    if any(getattr(memory, key) != value for key, value in expected_memory.items()):
        raise ValueError("worker memory policy mismatch")
    if loaded.fresh_process is not True or loaded.fresh_prompt_cache is not True:
        raise ValueError("worker did not attest a fresh process and prompt cache")
    if loaded.peak_memory_bytes < loaded.active_memory_bytes:
        raise ValueError("worker load memory measurements are inconsistent")
    token_events = events[4:-1]
    token_ids: list[int] = []
    model_logprobs: list[float] = []
    for position, event in enumerate(token_events, start=1):
        if not isinstance(event, TokenEmitted):
            raise ValueError("worker token event has wrong protocol type")
        token_id, logprob = event.token_id, event.selected_model_logprob
        if event.position != position:
            raise ValueError("token event position or ID is invalid")
        token_ids.append(token_id)
        model_logprobs.append(float(logprob))
    output_tokens = completed.output_tokens
    if output_tokens != len(token_ids) or not 0 <= output_tokens <= request.limits.output_tokens:
        raise ValueError("output token usage is inconsistent or exceeds the cap")
    if completed.max_output_tokens != request.limits.output_tokens:
        raise ValueError("worker output limit mismatch")
    if completed.truncated is not False:
        raise ValueError("worker output is truncated at the emitted-token cap")
    if completed.finish_reason != "stop":
        raise ValueError("worker did not finish at a stop token")
    if completed.fresh_process is not True or completed.fresh_prompt_cache is not True:
        raise ValueError("completion lacks fresh-context attestation")
    if completed.total_seconds < completed.inference_seconds:
        raise ValueError("worker timing measurements are inconsistent")
    if completed.peak_memory_bytes < completed.active_memory_bytes:
        raise ValueError("worker completion memory measurements are inconsistent")
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
    return [item.model_dump(mode="json") for item in events], completed.output_text, usage


def _observed_usage(raw: bytes, request: GenerationRequest | None = None) -> dict:
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
        "observation_errors": [],
    }
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        observed["observation_errors"].append("non-UTF-8 event stream")
        return observed
    expected_position = 1
    for line in lines:
        try:
            event = _decode_event(line)
            if request is not None:
                _check_event_request_identity(event, request)
        except ValueError as error:
            observed["observation_errors"].append(str(error))
            continue
        if isinstance(event, InputRejected):
            if (
                event.actual_input_tokens > event.max_input_tokens
                and (request is None or event.max_input_tokens == request.limits.input_tokens)
            ):
                observed["input_tokens"] = event.actual_input_tokens
            else:
                observed["observation_errors"].append("invalid rejected-input usage")
        elif isinstance(event, InputAccepted):
            if (
                0 < event.actual_input_tokens <= event.max_input_tokens
                and len(event.input_token_ids) == event.actual_input_tokens
                and (request is None or event.max_input_tokens == request.limits.input_tokens)
            ):
                observed["input_tokens"] = event.actual_input_tokens
                observed["input_token_ids"] = list(event.input_token_ids)
            else:
                observed["observation_errors"].append("invalid accepted-input usage")
        elif isinstance(event, TokenEmitted):
            if event.position != expected_position:
                observed["observation_errors"].append("invalid emitted-token sequence")
                continue
            observed["token_ids"].append(event.token_id)
            observed["selected_model_logprobs"].append(float(event.selected_model_logprob))
            expected_position += 1
        elif isinstance(event, Completed):
            observed["event_stream_complete"] = (
                observed["input_tokens"] == event.input_tokens
                and len(observed["token_ids"]) == event.output_tokens
            )
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
        return schema.model_validate_json(canonical_json(envelope["content"]))
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
        prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        worker_source = Path(__file__).with_name("_worker.py").read_bytes()
        worker_source_sha256 = hashlib.sha256(worker_source).hexdigest()
        schema_payload = output_schema.model_json_schema()
        request_payload = request.model_dump(mode="json")
        if any(
            len(data) > request.limits.stdin_bytes
            for data in (
                canonical_json(request_payload),
                canonical_json(schema_payload),
                prompt.encode("utf-8"),
            )
        ):
            raise ValueError("request, schema, or templated prompt exceeds the configured input byte cap")
        attempt_id = "gen-" + uuid.uuid4().hex
        recorded_at = datetime.now(timezone.utc)
        source_paths = {
            name: Path(__file__).with_name(name)
            for name in (
                "__init__.py", "_worker.py", "backend.py", "models.py",
                "protocol.py", "provider.py", "runner.py",
            )
        }
        attempt = GenerationAttemptMetadata(
            attempt_id=attempt_id,
            recorded_at=recorded_at,
            producer="feature_rl.generation.LocalGenerationProvider",
            protocol_version=PROTOCOL_VERSION,
            request_id=request.request_id,
            response_id=request.response_id,
            prompt_id=request.prompt_id,
            request_sha256=hashlib.sha256(canonical_json(request_payload)).hexdigest(),
            output_schema_sha256=hashlib.sha256(canonical_json(schema_payload)).hexdigest(),
            source_sha256={
                name: hashlib.sha256(path.read_bytes()).hexdigest()
                for name, path in source_paths.items()
            },
            configured_model_id=MODEL_ID,
            configured_model_revision=MODEL_REVISION,
            model_config_sha256=MODEL_CONFIG_SHA256,
            model_manifest_sha256=MODEL_MANIFEST_SHA256,
            dependency_manifest_sha256=DEPENDENCY_MANIFEST_SHA256,
            dependency_versions=EXPECTED_DEPENDENCIES,
        )
        attempt_archive = _ArchivePayload(
            name="attempt",
            data=canonical_json(attempt.model_dump(mode="json")),
            kind="generation-attempt",
            visibility=visibility,
        )
        refs: dict[str, ArtifactRef] = {}
        try:
            refs["attempt"] = ArtifactRef.model_validate(
                self._archive(attempt_archive.data, attempt_archive.kind, visibility)
            )
        except Exception as caught:
            cost = CostRecord(
                category="authoring",
                wall_seconds=None,
                cpu_seconds=None,
                gpu_seconds=None,
                input_tokens=None,
                output_tokens=None,
                human_minutes=None,
                usd=None,
                measurement="unknown",
                note="Execution did not start because attempt registration failed; costs are unknown.",
            )
            recovery = GenerationPublicationRecovery(
                attempt_id=attempt_id,
                recorded_at=recorded_at,
                request_id=request.request_id,
                response_id=request.response_id,
                payloads=(attempt_archive,),
                published_refs=(),
                generation_succeeded=False,
                underlying_error_code="ArchivePublicationError",
                underlying_error="attempt registration failed before execution",
                publication_error=f"{type(caught).__name__}: {caught}",
                cost=cost,
                response=None,
                usage_observation=None,
            )
            record = GenerationCallRecord(
                attempt_id=attempt_id,
                recorded_at=recorded_at,
                request_id=request.request_id,
                response_id=request.response_id,
                success=False,
                generation_succeeded=False,
                publication_complete=False,
                error_code="ArchivePublicationError",
                archives={},
            )
            raise GenerationProviderError(
                f"attempt registration failed before execution: {caught}",
                record,
                cost=cost,
                recovery=recovery,
            ) from caught
        outcome: ProcessOutcome | None = None
        verified: VerifiedBackend | None = None
        events: list[dict] = []
        usage: GenerationUsage | None = None
        observed_usage = _observed_usage(b"", request)
        content: StrictModel | None = None
        error: Exception | None = None
        try:
            verified = self._backend.verify()
            worker_input = canonical_json(
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "model_directory": str(self._backend.model_directory.resolve()),
                    "model_id": MODEL_ID,
                    "revision": MODEL_REVISION,
                    "response_id": request.response_id,
                    "request_id": request.request_id,
                    "prompt_id": request.prompt_id,
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
                worker.write_bytes(worker_source)
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
            observed_usage = _observed_usage(outcome.stdout, request)
            if outcome.termination != "process_exit":
                raise ValueError(f"worker terminated by {outcome.termination}")
            if outcome.monitoring_failures:
                raise ValueError("worker watchdog observation failed")
            if outcome.exit_status != 0:
                raise ValueError(f"worker exited with status {outcome.exit_status}")
            if not outcome.process_group_cleanup_verified:
                raise ValueError("worker process group cleanup was not verified")
            if outcome.memory_samples <= 0:
                raise ValueError("worker physical footprint was never sampled")
            events, output_text, usage = _parse_events(
                outcome.stdout,
                request,
                verified,
                prompt_sha256=prompt_sha256,
                worker_source_sha256=worker_source_sha256,
            )
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
                "Local execution: wall/CPU and validated observed token counts are measured when present; "
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
            "monitoring_failures": outcome.monitoring_failures if outcome else None,
            "monitor_error_type": outcome.monitor_error_type if outcome else None,
            "monitor_error": outcome.monitor_error if outcome else None,
            "cleanup_error": outcome.cleanup_error if outcome else None,
        }
        provenance_payload = {
            "expected_source_ids": [item.context_id for item in request.contexts],
            "allowed_requirement_ids": list(request.allowed_requirement_ids),
            "observed": content is not None,
        }
        payloads = {
            "request": request_payload,
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
            "usage": {
                "accepted_response_usage": error is None and usage is not None,
                "accepted": usage.model_dump(mode="json") if error is None and usage else None,
                "observed": observed_usage,
            },
            "cost": cost.model_dump(mode="json"),
            "events": events,
        }
        archive_payloads = (attempt_archive,) + tuple(
            _ArchivePayload(
                name=name,
                data=(
                    outcome.stdout
                    if name == "events" and outcome
                    else canonical_json(payloads[name])
                ),
                kind=f"generation-{name}",
                visibility=visibility,
            )
            for name in ARCHIVE_KINDS[1:-1]
        )
        generation_succeeded = error is None
        try:
            for payload in archive_payloads:
                if payload.name in refs:
                    continue
                refs[payload.name] = ArtifactRef.model_validate(
                    self._archive(payload.data, payload.kind, visibility)
                )
            status = {
                "attempt_id": attempt_id,
                "recorded_at": recorded_at.isoformat(),
                "request_id": request.request_id,
                "response_id": request.response_id,
                "success": generation_succeeded,
                "generation_succeeded": generation_succeeded,
                "publication_complete": True,
                "publication_recovered": False,
                "error_type": type(error).__name__ if error else None,
                "error": str(error) if error else None,
                "archive_refs": {key: value.model_dump(mode="json") for key, value in refs.items()},
            }
            refs["status"] = ArtifactRef.model_validate(
                self._archive(canonical_json(status), "generation-status", visibility)
            )
        except Exception as publication_error:
            recovery = GenerationPublicationRecovery(
                attempt_id=attempt_id,
                recorded_at=recorded_at,
                request_id=request.request_id,
                response_id=request.response_id,
                payloads=archive_payloads,
                published_refs=tuple(refs.items()),
                generation_succeeded=generation_succeeded,
                underlying_error_code=type(error).__name__ if error else None,
                underlying_error=str(error) if error else None,
                publication_error=f"{type(publication_error).__name__}: {publication_error}",
                cost=cost,
                response=response_payload,
                usage_observation=observed_usage,
            )
            record = GenerationCallRecord(
                attempt_id=attempt_id,
                recorded_at=recorded_at,
                request_id=request.request_id,
                response_id=request.response_id,
                success=False,
                generation_succeeded=generation_succeeded,
                publication_complete=False,
                error_code="ArchivePublicationError",
                archives=refs,
            )
            raise GenerationProviderError(
                f"archive publication failed: {publication_error}",
                record,
                cost=cost,
                response=response_payload,
                usage_observation=observed_usage,
                recovery=recovery,
            ) from publication_error
        record = GenerationCallRecord(
            attempt_id=attempt_id,
            recorded_at=recorded_at,
            request_id=request.request_id,
            response_id=request.response_id,
            success=generation_succeeded,
            generation_succeeded=generation_succeeded,
            publication_complete=True,
            error_code=type(error).__name__ if error else None,
            archives=refs,
        )
        if error is not None:
            raise GenerationProviderError(
                str(error),
                record,
                cost=cost,
                response=response_payload,
                usage_observation=observed_usage,
            ) from error
        assert content is not None and usage is not None
        return GenerationResult(content=content, usage=usage, cost=cost, record=record)

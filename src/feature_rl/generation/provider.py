"""One Codex/Astra call, strict output validation, and durable publication recovery."""

from __future__ import annotations

import base64
import hashlib
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from feature_rl.artifacts import canonical_json
from feature_rl.contracts import ArtifactRef, CostRecord, StrictModel, Visibility
from .codex import CodexConfig, CodexUnavailable
from .models import (
    GenerationAttemptMetadata,
    GenerationCallRecord,
    GenerationRequest,
    GenerationResult,
    GenerationStage,
    GenerationUsage,
)
from .runner import BoundedProcessRunner
from .schema import _parse_envelope, codex_request_schema, prompt
from .events import observed_usage, parse_events

ArchiveWriter = Callable[[bytes, str, Visibility], ArtifactRef]
ARCHIVE_KINDS = (
    "attempt",
    "request",
    "response",
    "retrieval",
    "schema",
    "options",
    "provenance",
    "usage",
    "cost",
    "events",
    "status",
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
            raise RuntimeError(
                "this provider failure has no pending archive publication"
            )
        return self.recovery.replay(archive)

    def replay_result(self, archive: ArchiveWriter) -> GenerationResult:
        if self.recovery is None:
            raise RuntimeError(
                "this provider failure has no pending archive publication"
            )
        return self.recovery.replay_result(archive)

    def replay_error(self, archive: ArchiveWriter) -> "GenerationProviderError":
        """Finish a failed call's archive and return the same failure for controller replay."""
        if self.recovery is None:
            raise RuntimeError(
                "this provider failure has no pending archive publication"
            )
        if self.recovery.generation_succeeded:
            raise RuntimeError(
                "successful generation publication must use replay_result"
            )
        record = self.recovery.replay(archive)
        return GenerationProviderError(
            self.recovery.underlying_error or str(self),
            record,
            cost=self.recovery.cost,
            response=self.recovery.response,
            usage_observation=self.recovery.usage_observation,
        )


class GenerationInputLimitError(ValueError):
    """A validated request cannot fit its declared serialized-input boundary."""


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
    content: StrictModel | None
    usage: GenerationUsage | None

    def replay(
        self, archive: ArchiveWriter, *, recovered: bool = True
    ) -> GenerationCallRecord:
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
                "publication_recovered": recovered,
                "publication_failure": self.publication_error,
                "error_type": self.underlying_error_code,
                "error": self.underlying_error,
                "archive_refs": {
                    key: value.model_dump(mode="json") for key, value in refs.items()
                },
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

    def replay_result(self, archive: ArchiveWriter) -> GenerationResult:
        if (
            not self.generation_succeeded
            or self.content is None
            or self.usage is None
            or self.cost is None
        ):
            raise RuntimeError(
                "pending publication does not contain a successful generation result"
            )
        record = self.replay(archive)
        return GenerationResult(
            content=self.content,
            usage=self.usage,
            cost=self.cost,
            record=record,
        )


class CodexGenerationProvider:
    def __init__(
        self,
        *,
        config: CodexConfig,
        archive: ArchiveWriter,
        runner: BoundedProcessRunner | None = None,
    ):
        self._config = CodexConfig.model_validate(config)
        self._archive = archive
        self._runner = runner or BoundedProcessRunner()

    def generate(
        self, request: GenerationRequest, output_schema: type[StrictModel]
    ) -> GenerationResult:
        if not isinstance(request, GenerationRequest):
            raise TypeError("request must be a validated GenerationRequest")
        request = GenerationRequest.model_validate(request)
        if not isinstance(output_schema, type) or not issubclass(
            output_schema, StrictModel
        ):
            raise TypeError("output schema must be a StrictModel subclass")
        visibility = (
            Visibility.AUTHORING
            if request.stage
            in {GenerationStage.DISCOVERY, GenerationStage.INITIAL_AUTHORING}
            else Visibility.PRIVATE
        )
        request_payload = request.model_dump(mode="json")
        schema_payload = output_schema.model_json_schema()
        attempt = GenerationAttemptMetadata(
            attempt_id="gen-" + uuid.uuid4().hex,
            recorded_at=datetime.now(timezone.utc),
            producer="feature_rl.generation.CodexGenerationProvider",
            protocol_version="codex-exec-v1",
            request_id=request.request_id,
            response_id=request.response_id,
            prompt_id=request.prompt_id,
            request_sha256=hashlib.sha256(canonical_json(request_payload)).hexdigest(),
            output_schema_sha256=hashlib.sha256(
                canonical_json(schema_payload)
            ).hexdigest(),
            configured_model_id=self._config.model,
            reasoning_effort=self._config.reasoning_effort,
        )

        def payload(name, value):
            return _ArchivePayload(
                name,
                value if isinstance(value, bytes) else canonical_json(value),
                "generation-" + name,
                visibility,
            )

        attempt_payload = payload("attempt", attempt.model_dump(mode="json"))
        refs = {}
        try:
            refs["attempt"] = self._archive(
                attempt_payload.data, attempt_payload.kind, visibility
            )
        except Exception as error:
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
                note="Execution did not start; execution and monetary costs are unknown.",
            )
            recovery = GenerationPublicationRecovery(
                attempt_id=attempt.attempt_id,
                recorded_at=attempt.recorded_at,
                request_id=request.request_id,
                response_id=request.response_id,
                payloads=(
                    attempt_payload,
                    payload("cost", cost.model_dump(mode="json")),
                ),
                published_refs=(),
                generation_succeeded=False,
                underlying_error_code="ArchivePublicationError",
                underlying_error="attempt registration failed before execution",
                publication_error=str(error),
                cost=cost,
                response=None,
                usage_observation=None,
                content=None,
                usage=None,
            )
            record = GenerationCallRecord(
                attempt_id=attempt.attempt_id,
                recorded_at=attempt.recorded_at,
                request_id=request.request_id,
                response_id=request.response_id,
                success=False,
                generation_succeeded=False,
                publication_complete=False,
                error_code="ArchivePublicationError",
                archives={},
            )
            raise GenerationProviderError(
                "attempt registration failed before execution",
                record,
                cost=cost,
                recovery=recovery,
            ) from error

        outcome = None
        transport_schema = None
        content = usage = error = None
        output_text = None
        version = None
        try:

            prompt_bytes = prompt(request)
            transport_schema = codex_request_schema(request, output_schema)
            if (
                max(
                    len(prompt_bytes),
                    len(canonical_json(transport_schema)),
                    len(canonical_json(request_payload)),
                )
                > request.limits.stdin_bytes
            ):
                raise GenerationInputLimitError(
                    "request, schema, or prompt exceeds the configured input byte cap"
                )
            with tempfile.TemporaryDirectory(prefix="feature-rl-codex-") as directory:
                root = Path(directory)
                executable, version = self._config.verify(root)
                (root / "schema.json").write_bytes(canonical_json(transport_schema))
                outcome = self._runner.run(
                    command=self._config.command(executable, root),
                    stdin=prompt_bytes,
                    cwd=root,
                    environment=self._config.environment(),
                    limits=request.limits,
                    response_path=root / "response.json",
                )
                usage = observed_usage(outcome.stdout)
                if outcome.termination != "process_exit":
                    raise ValueError(f"Codex terminated by {outcome.termination}")
                if outcome.exit_status != 0:
                    diagnostic = outcome.stderr.decode("utf-8", errors="replace")[
                        -4096:
                    ]
                    try:
                        parse_events(outcome.stdout)
                    except (ValueError, CodexUnavailable) as event_error:
                        diagnostic += "\n" + str(event_error)[-4096:]
                    raise CodexUnavailable(
                        f"Codex/Astra exited with status {outcome.exit_status}: {diagnostic}"
                    )
                if (
                    outcome.monitoring_failures
                    or not outcome.process_group_cleanup_verified
                ):
                    raise ValueError("Codex process monitoring or cleanup failed")
                try:
                    output_text, usage = parse_events(outcome.stdout)
                except ValueError as error:
                    # A CLI protocol incompatibility cannot be repaired by
                    # asking the model to rewrite the artifact.
                    raise CodexUnavailable(f"Codex event protocol is invalid: {error}") from error
                response_path = root / "response.json"
                if (
                    response_path.is_symlink()
                    or not response_path.is_file()
                    or response_path.stat().st_size > request.limits.output_bytes
                ):
                    raise ValueError(
                        "Codex final response file is missing or exceeds the output byte cap"
                    )
                if (
                    response_path.read_text(encoding="utf-8").strip()
                    != output_text.strip()
                ):
                    raise ValueError(
                        "Codex final response file differs from its completed message"
                    )
            if (
                usage.input_tokens > request.limits.input_tokens
                or usage.output_tokens > request.limits.output_tokens
            ):
                raise ValueError(
                    "Codex reported token usage exceeds the authoring acceptance limits"
                )
            content = _parse_envelope(output_text, request, output_schema)
        except Exception as caught:
            error = caught

        observed = usage.model_dump(mode="json") if usage is not None else None
        cost = CostRecord(
            category="authoring",
            wall_seconds=outcome.wall_seconds if outcome else None,
            cpu_seconds=outcome.cpu_seconds if outcome else None,
            gpu_seconds=None,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            human_minutes=None,
            usd=None,
            measurement="partial" if outcome else "unknown",
            note=(
                "Wall/CPU describe the local Codex process; token counts are Codex-reported. "
                "Remote compute and subscription/USD costs are unknown. Token ceilings are checked "
                "after completion; Codex exec does not expose hard per-call token limits."
                if outcome
                else "Execution did not start; execution and monetary costs are unknown."
            ),
        )
        response = {
            "output_text": output_text,
            "stdout_base64": base64.b64encode(outcome.stdout).decode()
            if outcome
            else "",
            "stderr_base64": base64.b64encode(outcome.stderr).decode()
            if outcome
            else "",
            "termination": outcome.termination if outcome else None,
            "exit_status": outcome.exit_status if outcome else None,
            "transport_schema": transport_schema,
            "process_boundary": {
                key: value for key, value in asdict(outcome).items()
                if key not in {"stdout", "stderr", "termination", "exit_status",
                               "wall_seconds", "cpu_seconds"}
            } if outcome else None,
        }
        values = {
            "request": request_payload,
            "response": response,
            "retrieval": {
                "contexts": [item.model_dump(mode="json") for item in request.contexts]
            },
            "schema": schema_payload,
            "options": {
                "model": self._config.model,
                "reasoning_effort": self._config.reasoning_effort,
                "limits": request.limits.model_dump(mode="json"),
            },
            "provenance": {
                "codex_version": version,
                "authentication": "chatgpt",
                "expected_source_ids": [item.context_id for item in request.contexts],
                "allowed_requirement_ids": list(request.allowed_requirement_ids),
            },
            "usage": {
                "accepted_response_usage": error is None,
                "accepted": observed if error is None else None,
                "observed": observed,
            },
            "cost": cost.model_dump(mode="json"),
            "events": outcome.stdout if outcome else b"",
        }
        limited = isinstance(error, GenerationInputLimitError)
        if limited:
            response = None
        archived_payloads = (
            (attempt_payload, payload("cost", cost.model_dump(mode="json")))
            if limited
            else (attempt_payload,)
            + tuple(payload(name, values[name]) for name in ARCHIVE_KINDS[1:-1])
        )
        recovery = GenerationPublicationRecovery(
            attempt_id=attempt.attempt_id,
            recorded_at=attempt.recorded_at,
            request_id=request.request_id,
            response_id=request.response_id,
            payloads=archived_payloads,
            published_refs=tuple(refs.items()),
            generation_succeeded=error is None,
            underlying_error_code=type(error).__name__ if error else None,
            underlying_error=str(error) if error else None,
            publication_error="",
            cost=cost,
            response=response,
            usage_observation=observed,
            content=content,
            usage=usage,
        )
        record = recovery.replay(self._archive, recovered=False)
        if error is not None:
            raise GenerationProviderError(
                str(error),
                record,
                cost=cost,
                response=response,
                usage_observation=observed,
            ) from error
        return GenerationResult(content=content, usage=usage, cost=cost, record=record)

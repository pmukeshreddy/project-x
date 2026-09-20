"""Store-backed reconstruction of every author-visible model context."""

from __future__ import annotations

from typing import Literal

from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole, ArtifactRef

from .runtime_discovery import parse_discovery, discovery_locator
from .models import GroundedSource
from .retrieval import BaselineRetriever, RetrievalPolicy, RetrievalRequest


class EvidenceResolutionError(ValueError):
    """Supplied context differs from bytes resolved through the admitted store boundary."""


def _ranges(locator: str) -> tuple[str, tuple[tuple[int, int], ...]]:
    path, separator, encoded = locator.rpartition(":")
    if not separator or not path or not encoded:
        raise EvidenceResolutionError("baseline locator must contain path and line ranges")
    try:
        ranges = tuple(
            tuple(int(value) for value in item.split("-", 1)) for item in encoded.split(",")
        )
    except (TypeError, ValueError) as error:
        raise EvidenceResolutionError("baseline locator contains invalid line ranges") from error
    if any(len(item) != 2 for item in ranges):
        raise EvidenceResolutionError("baseline locator contains invalid line ranges")
    return path, ranges


class AuthoringEvidenceResolver:
    def __init__(
        self,
        *,
        store: ArtifactStore,
        request: ArtifactRef,
        baseline: ArtifactRef,
        runtime_discovery: ArtifactRef,
        public_checks: tuple[ArtifactRef, ...],
        retrieval_policy: RetrievalPolicy,
        request_provenance: Literal[
            "historical_request", "reconstructed_specification"
        ] = "reconstructed_specification",
    ):
        if not isinstance(store, ArtifactStore) or store.role not in {
            ActorRole.AUTHOR,
            ActorRole.CONTROLLER,
        }:
            raise TypeError("authoring evidence resolver requires an author-capable store")
        self.store = store
        self.request = ArtifactRef.model_validate(request)
        self.baseline = ArtifactRef.model_validate(baseline)
        self.runtime_discovery = ArtifactRef.model_validate(runtime_discovery)
        self.public_checks = tuple(ArtifactRef.model_validate(ref) for ref in public_checks)
        self.retrieval_policy = RetrievalPolicy.model_validate(retrieval_policy)
        self.request_provenance = request_provenance
        if self.request.kind != "authoring-request" or self.request.encoding != "bytes":
            raise EvidenceResolutionError("exact authoring request byte artifact is required")
        if self.baseline.kind != "source-archive" or self.baseline.encoding != "bytes":
            raise EvidenceResolutionError("exact baseline source archive is required")
        if (
            self.runtime_discovery.kind not in {"runtime-discovery"}
            or self.runtime_discovery.encoding != "bytes"
        ):
            raise EvidenceResolutionError("exact validated runtime discovery is required")

    def _read(self, ref: ArtifactRef, cap: int) -> bytes:
        return self.store.get_bytes(
            ref,
            max_envelope_bytes=4 * ((cap + 2) // 3) + 4096,
            max_payload_bytes=cap,
        )

    def resolve(self, supplied: tuple[GroundedSource, ...]) -> tuple[GroundedSource, ...]:
        supplied = tuple(GroundedSource.model_validate(source) for source in supplied)
        if not supplied:
            raise EvidenceResolutionError("authoring evidence cannot be empty")
        context_ids = [source.context_id for source in supplied]
        if len(context_ids) != len(set(context_ids)):
            raise EvidenceResolutionError("duplicate authoring context ID")

        resolved: dict[str, GroundedSource] = {}
        baseline_supplied = [source for source in supplied if source.source == self.baseline]
        if baseline_supplied:
            archive = self._read(self.baseline, self.retrieval_policy.max_archive_bytes)
            requests = []
            for source in baseline_supplied:
                path, line_ranges = _ranges(source.locator)
                requests.append(
                    RetrievalRequest(
                        context_id=source.context_id,
                        path=path,
                        line_ranges=line_ranges,
                    )
                )
            retrieved = BaselineRetriever(
                baseline=self.baseline,
                archive=archive,
                policy=self.retrieval_policy,
            ).retrieve(tuple(requests))
            resolved.update({source.context_id: source for source in retrieved.sources})

        for source in supplied:
            if source.source == self.baseline:
                continue
            if source.source == self.request:
                expected = GroundedSource(
                    context_id=source.context_id,
                    role="request",
                    source=self.request,
                    locator="authoring-request:whole",
                    text=self._read(self.request, 1024 * 1024).decode(),
                    provenance_label=self.request_provenance,
                )
            elif source.source == self.runtime_discovery:
                text = self._read(self.runtime_discovery, 128 * 1024).decode()
                try:
                    discovery = parse_discovery(self.runtime_discovery, text)
                except ValueError as error:
                    raise EvidenceResolutionError("stored runtime discovery is invalid") from error
                if discovery.baseline != self.baseline:
                    raise EvidenceResolutionError("runtime discovery baseline binding mismatch")
                expected = GroundedSource(
                    context_id=source.context_id,
                    role="baseline",
                    source=self.runtime_discovery,
                    locator=discovery_locator(self.runtime_discovery),
                    text=text,
                    provenance_label="existing_obligation",
                )
            elif source.source in self.public_checks:
                expected = GroundedSource(
                    context_id=source.context_id,
                    role="public_check",
                    source=source.source,
                    locator="artifact:whole",
                    text=self._read(source.source, 1024 * 1024).decode(),
                    provenance_label="existing_obligation",
                )
            else:
                raise EvidenceResolutionError("source is outside the exact authoring allowlist")
            resolved[source.context_id] = expected

        canonical = tuple(resolved[source.context_id] for source in supplied)
        if canonical != supplied:
            raise EvidenceResolutionError("supplied source text or metadata does not match resolved bytes")
        return canonical

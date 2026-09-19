"""Bounded inert retrieval from an admitted baseline source archive."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
from pathlib import PurePosixPath
import tarfile
from typing import Annotated

from pydantic import Field, model_validator

from feature_rl.contracts import ArtifactRef, PositiveInt, StrictModel, Text, Visibility

from .models import GroundedSource


class RetrievalRejected(ValueError):
    """A requested source span is outside the declared inert retrieval boundary."""


class RetrievalPolicy(StrictModel):
    allowed_paths: Annotated[tuple[Text, ...], Field(min_length=1)]
    max_archive_bytes: PositiveInt
    max_files: PositiveInt
    max_selected_bytes: PositiveInt
    max_spans: PositiveInt
    max_expanded_bytes: PositiveInt = 16 * 1024 * 1024

    @model_validator(mode="after")
    def unique_paths(self):
        if len(self.allowed_paths) != len(set(self.allowed_paths)):
            raise ValueError("duplicate retrieval allowlist paths")
        for path in self.allowed_paths:
            _safe_path(path)
        return self


class RetrievalRequest(StrictModel):
    context_id: Text
    path: Text
    line_ranges: Annotated[tuple[tuple[int, int], ...], Field(min_length=1)]

    @model_validator(mode="after")
    def valid_ranges(self):
        _safe_path(self.path)
        previous_end = 0
        for start, end in self.line_ranges:
            if type(start) is not int or type(end) is not int or start < 1 or end < start:
                raise ValueError("line ranges require positive inclusive bounds")
            if start <= previous_end:
                raise ValueError("line ranges must be ordered and nonoverlapping")
            previous_end = end
        return self


class RetrievedSpan(StrictModel):
    context_id: Text
    path: Text
    locator: Text
    line_ranges: tuple[tuple[int, int], ...]
    text_bytes: int
    text_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class RetrievalReceipt(StrictModel):
    baseline: ArtifactRef
    archive_bytes: PositiveInt
    archive_file_count: PositiveInt
    archive_expanded_bytes: PositiveInt
    selected_bytes: PositiveInt
    requests: Annotated[tuple[RetrievedSpan, ...], Field(min_length=1)]


@dataclass(frozen=True)
class RetrievalResult:
    sources: tuple[GroundedSource, ...]
    receipt: RetrievalReceipt


def _safe_path(value: str) -> None:
    if "\\" in value or "\x00" in value:
        raise RetrievalRejected("unsafe archive path")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise RetrievalRejected("unsafe archive path")


class BaselineRetriever:
    def __init__(self, *, baseline: ArtifactRef, archive: bytes, policy: RetrievalPolicy):
        self.baseline = ArtifactRef.model_validate(baseline)
        self.policy = RetrievalPolicy.model_validate(policy)
        if (
            self.baseline.kind != "source-archive"
            or self.baseline.visibility not in {Visibility.PUBLIC, Visibility.AUTHORING}
            or self.baseline.encoding != "bytes"
        ):
            raise RetrievalRejected("author-visible source archive is required")
        if type(archive) is not bytes or len(archive) > self.policy.max_archive_bytes:
            raise RetrievalRejected("baseline archive exceeds its byte cap")
        self.archive = archive

    def _files(self) -> tuple[dict[str, bytes], int]:
        files: dict[str, bytes] = {}
        expanded = 0
        try:
            with tarfile.open(fileobj=io.BytesIO(self.archive), mode="r:*") as archive:
                members = archive.getmembers()
                if len(members) > self.policy.max_files:
                    raise RetrievalRejected("baseline archive exceeds its file cap")
                for member in members:
                    _safe_path(member.name)
                    if not member.isfile() or member.name in files:
                        raise RetrievalRejected("baseline archive contains unsupported members")
                    expanded += member.size
                    if expanded > self.policy.max_expanded_bytes:
                        raise RetrievalRejected("baseline archive exceeds its expanded byte cap")
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise RetrievalRejected("baseline archive member is unavailable")
                    files[member.name] = stream.read()
        except (tarfile.TarError, OSError) as error:
            raise RetrievalRejected("invalid baseline source archive") from error
        return files, expanded

    def retrieve(self, requests: tuple[RetrievalRequest, ...]) -> RetrievalResult:
        requests = tuple(RetrievalRequest.model_validate(request) for request in requests)
        if not requests:
            raise RetrievalRejected("at least one retrieval span is required")
        if sum(len(request.line_ranges) for request in requests) > self.policy.max_spans:
            raise RetrievalRejected("retrieval span cap exceeded")
        context_ids = [request.context_id for request in requests]
        if len(context_ids) != len(set(context_ids)):
            raise RetrievalRejected("duplicate retrieval context ID")
        files, expanded = self._files()
        sources = []
        records = []
        selected_bytes = 0
        for request in requests:
            if request.path not in self.policy.allowed_paths or request.path not in files:
                raise RetrievalRejected("requested path is outside the retrieval allowlist")
            try:
                lines = files[request.path].decode().splitlines()
            except UnicodeError as error:
                raise RetrievalRejected("requested source is not UTF-8") from error
            if any(end > len(lines) for _, end in request.line_ranges):
                raise RetrievalRejected("requested line range exceeds the source file")
            text = "".join(
                "\n".join(lines[start - 1 : end]) + "\n"
                for start, end in request.line_ranges
            )
            encoded = text.encode()
            selected_bytes += len(encoded)
            if selected_bytes > self.policy.max_selected_bytes:
                raise RetrievalRejected("retrieval selected-byte cap exceeded")
            locator = request.path + ":" + ",".join(
                f"{start}-{end}" for start, end in request.line_ranges
            )
            sources.append(
                GroundedSource(
                    context_id=request.context_id,
                    role="baseline",
                    source=self.baseline,
                    locator=locator,
                    text=text,
                    provenance_label="existing_obligation",
                )
            )
            records.append(
                RetrievedSpan(
                    context_id=request.context_id,
                    path=request.path,
                    locator=locator,
                    line_ranges=request.line_ranges,
                    text_bytes=len(encoded),
                    text_sha256=hashlib.sha256(encoded).hexdigest(),
                )
            )
        return RetrievalResult(
            sources=tuple(sources),
            receipt=RetrievalReceipt(
                baseline=self.baseline,
                archive_bytes=len(self.archive),
                archive_file_count=len(files),
                archive_expanded_bytes=expanded,
                selected_bytes=selected_bytes,
                requests=tuple(records),
            ),
        )

"""Bounded HTTP and cache intake into immutable M0 source references."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import socket
from typing import Callable, Literal
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import SourceSnapshot, Visibility


EditHistory = Literal["available", "unavailable", "not_applicable"]


class SourceFetchError(RuntimeError):
    """A source could not be fetched under the declared policy."""


class SourceFetchTimeout(SourceFetchError):
    """A source operation exceeded its deadline."""


class SourceTooLarge(SourceFetchError):
    """A source body exceeded its byte limit."""


class SourceIntegrityError(SourceFetchError):
    """A cached source did not match its immutable manifest."""


def _utc(value: datetime | None, field: str) -> None:
    if value is not None and (
        value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value)
    ):
        raise ValueError(f"{field} must explicitly use UTC")


@dataclass(frozen=True)
class FetchedSource:
    url: str
    body: bytes
    retrieved_at: datetime
    published_at: datetime | None
    edited_at: datetime | None
    edit_history: EditHistory
    media_type: str
    status_code: int

    def __post_init__(self):
        parsed = urlsplit(self.url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("source URL must use https")
        if type(self.body) is not bytes:
            raise TypeError("source body must be bytes")
        _utc(self.retrieved_at, "retrieved_at")
        _utc(self.published_at, "published_at")
        _utc(self.edited_at, "edited_at")
        if self.edit_history not in {"available", "unavailable", "not_applicable"}:
            raise ValueError("edit_history must be explicit")
        if not self.media_type.strip():
            raise ValueError("media_type must be nonempty")
        if type(self.status_code) is not int:
            raise TypeError("status_code must be an integer")


class BoundedHttpFetcher:
    """Fetch one HTTPS response with an explicit deadline and byte cap."""

    def __init__(
        self,
        *,
        max_bytes: int,
        timeout_seconds: float,
        opener: Callable[..., object] | None = None,
    ):
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds
        self.opener = opener or urllib.request.urlopen

    def fetch(
        self,
        url: str,
        *,
        edit_history: EditHistory,
        published_at: datetime | None = None,
        edited_at: datetime | None = None,
        media_type: str | None = None,
    ) -> FetchedSource:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username is not None:
            raise ValueError("source URL must be credential-free https")
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "feature-rl-source-intake/1",
            },
        )
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                status = int(getattr(response, "status", 200))
                if not 200 <= status < 300:
                    raise SourceFetchError(f"source returned HTTP {status}")
                length_text = response.headers.get("Content-Length")
                if length_text is not None:
                    try:
                        declared_length = int(length_text)
                    except ValueError as exc:
                        raise SourceFetchError("invalid Content-Length") from exc
                    if declared_length < 0:
                        raise SourceFetchError("invalid Content-Length")
                    if declared_length > self.max_bytes:
                        raise SourceTooLarge(
                            f"source declared {declared_length} bytes; limit is {self.max_bytes}"
                        )
                body = response.read(self.max_bytes + 1)
                if len(body) > self.max_bytes:
                    raise SourceTooLarge(f"source exceeded {self.max_bytes} bytes")
                content_type = media_type or response.headers.get(
                    "Content-Type", "application/octet-stream"
                ).split(";", 1)[0]
        except SourceFetchError:
            raise
        except (TimeoutError, socket.timeout) as exc:
            raise SourceFetchTimeout(f"source fetch timed out after {self.timeout_seconds}s") from exc
        except urllib.error.HTTPError as exc:
            raise SourceFetchError(f"source returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise SourceFetchTimeout(
                    f"source fetch timed out after {self.timeout_seconds}s"
                ) from exc
            raise SourceFetchError(f"source fetch failed: {exc.reason}") from exc
        return FetchedSource(
            url=url,
            body=body,
            retrieved_at=datetime.now(timezone.utc),
            published_at=published_at,
            edited_at=edited_at,
            edit_history=edit_history,
            media_type=content_type,
            status_code=status,
        )


@dataclass(frozen=True)
class _CacheEntry:
    name: str
    status: int
    retrieved_at: datetime
    url: str
    body_path: str
    sha256: str


class CachedSourceCatalog:
    """Verify and load inert HTTP bodies captured by an earlier bounded fetch."""

    _FIELDS = ("name", "http_status", "retrieved_at", "url", "body_path", "sha256")

    def __init__(self, root: Path, manifest: Path, *, max_bytes: int):
        if not isinstance(root, Path) or not isinstance(manifest, Path):
            raise TypeError("root and manifest must be Path values")
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        self.root = root.resolve(strict=True)
        manifest_path = manifest.resolve(strict=True)
        if not manifest_path.is_relative_to(self.root):
            raise SourceIntegrityError("manifest is outside the cache root")
        self.max_bytes = max_bytes
        entries: dict[str, _CacheEntry] = {}
        with manifest_path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            if tuple(reader.fieldnames or ()) != self._FIELDS:
                raise SourceIntegrityError("cache manifest has unexpected columns")
            for row in reader:
                name = row["name"]
                if name in entries:
                    raise SourceIntegrityError("cache manifest has a duplicate source name")
                try:
                    retrieved = datetime.fromisoformat(row["retrieved_at"].replace("Z", "+00:00"))
                    status = int(row["http_status"])
                except (ValueError, TypeError) as exc:
                    raise SourceIntegrityError("cache manifest metadata is invalid") from exc
                _utc(retrieved, "retrieved_at")
                digest = row["sha256"]
                if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    raise SourceIntegrityError("cache manifest digest is invalid")
                entries[name] = _CacheEntry(
                    name, status, retrieved, row["url"], row["body_path"], digest
                )
        self.entries = entries

    def load(
        self,
        name: str,
        *,
        edit_history: EditHistory,
        published_at: datetime | None = None,
        edited_at: datetime | None = None,
        media_type: str = "application/json",
    ) -> FetchedSource:
        try:
            entry = self.entries[name]
        except KeyError as exc:
            raise SourceIntegrityError(f"cache source is missing: {name}") from exc
        if not 200 <= entry.status < 300:
            raise SourceFetchError(f"cached source returned HTTP {entry.status}")
        body_path = (self.root / entry.body_path).resolve(strict=True)
        if not body_path.is_relative_to(self.root):
            raise SourceIntegrityError("cached response body is outside the cache root")
        if not body_path.is_file() or body_path.is_symlink():
            raise SourceIntegrityError("cached response body is not a regular file")
        if body_path.stat().st_size > self.max_bytes:
            raise SourceTooLarge(f"cached response exceeded {self.max_bytes} bytes")
        body = body_path.read_bytes()
        if hashlib.sha256(body).hexdigest() != entry.sha256:
            raise SourceIntegrityError("cached response body digest mismatch")
        return FetchedSource(
            url=entry.url,
            body=body,
            retrieved_at=entry.retrieved_at,
            published_at=published_at,
            edited_at=edited_at,
            edit_history=edit_history,
            media_type=media_type,
            status_code=entry.status,
        )


class SourceArchiver:
    """Publish exact response bodies and return typed source metadata."""

    def __init__(self, store: ArtifactStore):
        if not isinstance(store, ArtifactStore):
            raise TypeError("store must be an ArtifactStore")
        self.store = store

    def archive(self, source: FetchedSource, visibility: Visibility) -> SourceSnapshot:
        if not isinstance(source, FetchedSource) or not isinstance(visibility, Visibility):
            raise TypeError("archive requires FetchedSource and Visibility")
        if not 200 <= source.status_code < 300:
            raise SourceFetchError(f"cannot archive unsuccessful HTTP {source.status_code}")
        content = self.store.put_bytes(source.body, "source-response", visibility)
        return SourceSnapshot(
            url=source.url,
            content=content,
            retrieved_at=source.retrieved_at,
            published_at=source.published_at,
            edited_at=source.edited_at,
            edit_history=source.edit_history,
            media_type=source.media_type,
        )

    def verify(self, snapshot: SourceSnapshot) -> bytes:
        if not isinstance(snapshot, SourceSnapshot):
            raise TypeError("snapshot must be a SourceSnapshot")
        return self.store.get_bytes(snapshot.content)

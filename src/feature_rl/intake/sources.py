"""Bounded HTTP and cache intake into immutable M0 source references."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import ipaddress
import multiprocessing
from pathlib import Path
import socket
import time
from typing import Callable, Literal
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from urllib.parse import urljoin

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
    redirect_chain: tuple[str, ...] | None

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
        if self.redirect_chain is not None:
            if not self.redirect_chain or self.redirect_chain[0] != self.url:
                raise ValueError("known redirect chain must begin with source URL")
            origin = urlsplit(self.url).netloc.lower()
            for hop in self.redirect_chain:
                _validate_url(hop, origin=origin)


def _validate_url(url: str, *, origin: str | None = None) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("source and redirect URLs must be credential-free https")
    hostname = parsed.hostname
    assert hostname is not None
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise ValueError("source URL must not target a local host")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("source URL must not target a nonpublic address")
    if origin is not None and parsed.netloc.lower() != origin:
        raise ValueError("source redirect must remain on the requested HTTPS origin")
    return parsed.netloc.lower()


class _RedirectRecorder(urllib.request.HTTPRedirectHandler):
    def __init__(self, requested_url: str):
        super().__init__()
        self.origin = _validate_url(requested_url)
        self.chain = [requested_url]

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urljoin(req.full_url, newurl)
        _validate_url(target, origin=self.origin)
        self.chain.append(target)
        return super().redirect_request(req, fp, code, msg, headers, target)


def _fetch_worker(connection, opener, url: str, max_bytes: int, timeout: float, media_type,
                  bearer_token=None, accept='application/vnd.github+json'):
    recorder = _RedirectRecorder(url)
    open_call = opener or urllib.request.build_opener(recorder).open
    headers = {"Accept": accept, "User-Agent": "feature-rl-source-intake/1"}
    if bearer_token is not None:
        headers['Authorization'] = 'Bearer '+bearer_token
    request = urllib.request.Request(url, headers=headers)
    try:
        with open_call(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            if not 200 <= status < 300:
                raise SourceFetchError(f"source returned HTTP {status}")
            final_url = getattr(response, "geturl", lambda: url)() or url
            origin = _validate_url(url)
            _validate_url(final_url, origin=origin)
            reported_chain = getattr(response, "redirect_chain", None)
            if reported_chain is not None:
                chain = tuple(reported_chain)
                if not chain or chain[0] != url or chain[-1] != final_url:
                    raise ValueError("reported redirect chain is incomplete")
                for hop in chain:
                    _validate_url(hop, origin=origin)
            else:
                chain_list = recorder.chain
                if chain_list[-1] != final_url:
                    chain_list.append(final_url)
                chain = tuple(chain_list)
            length_text = response.headers.get("Content-Length")
            if length_text is not None:
                try:
                    declared_length = int(length_text)
                except ValueError as exc:
                    raise SourceFetchError("invalid Content-Length") from exc
                if declared_length < 0:
                    raise SourceFetchError("invalid Content-Length")
                if declared_length > max_bytes:
                    raise SourceTooLarge(
                        f"source declared {declared_length} bytes; limit is {max_bytes}"
                    )
            body = bytearray()
            while len(body) <= max_bytes:
                chunk = response.read(min(65_536, max_bytes + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
            if len(body) > max_bytes:
                raise SourceTooLarge(f"source exceeded {max_bytes} bytes")
            content_type = media_type or response.headers.get(
                "Content-Type", "application/octet-stream"
            ).split(";", 1)[0]
        connection.send(
            (
                "ok",
                bytes(body),
                status,
                content_type,
                chain,
                datetime.now(timezone.utc),
            )
        )
    except SourceFetchError as exc:
        connection.send(("error", type(exc).__name__, str(exc)))
    except ValueError as exc:
        connection.send(("error", "ValueError", str(exc)))
    except (TimeoutError, socket.timeout) as exc:
        connection.send(("error", "SourceFetchTimeout", str(exc)))
    except urllib.error.HTTPError as exc:
        connection.send(("error", "SourceFetchError", f"source returned HTTP {exc.code}"))
    except urllib.error.URLError as exc:
        kind = (
            "SourceFetchTimeout"
            if isinstance(exc.reason, (TimeoutError, socket.timeout))
            else "SourceFetchError"
        )
        connection.send(("error", kind, f"source fetch failed: {exc.reason}"))
    except BaseException as exc:
        connection.send(("error", "SourceFetchError", f"source fetch failed: {exc}"))
    finally:
        connection.close()


class BoundedHttpFetcher:
    """Fetch one HTTPS response with an explicit deadline and byte cap."""

    def __init__(
        self,
        *,
        max_bytes: int,
        timeout_seconds: float,
        opener: Callable[..., object] | None = None,
        bearer_token: str | None = None,
    ):
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if bearer_token is not None and (not isinstance(bearer_token, str)
                or not 1 <= len(bearer_token) <= 4096
                or any(not 33 <= ord(char) <= 126 for char in bearer_token)):
            raise ValueError('GitHub token must be a nonempty printable credential')
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds
        self.opener = opener
        self.bearer_token = bearer_token

    def fetch(
        self,
        url: str,
        *,
        edit_history: EditHistory,
        published_at: datetime | None = None,
        edited_at: datetime | None = None,
        media_type: str | None = None,
        accept: Literal['application/vnd.github+json', 'application/vnd.github.raw+json'] = 'application/vnd.github+json',
    ) -> FetchedSource:
        _validate_url(url)
        if self.bearer_token is not None and urlsplit(url).netloc != 'api.github.com':
            raise ValueError('GitHub API credentials cannot be sent to another origin')
        context = multiprocessing.get_context("fork")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(
            target=_fetch_worker,
            args=(
                child,
                self.opener,
                url,
                self.max_bytes,
                self.timeout_seconds,
                media_type,
                self.bearer_token,
                accept,
            ),
            daemon=True,
        )
        started = time.monotonic()
        process.start()
        child.close()
        try:
            remaining = self.timeout_seconds - (time.monotonic() - started)
            if remaining <= 0 or not parent.poll(remaining):
                process.kill()
                process.join()
                raise SourceFetchTimeout(
                    f"source fetch timed out after {self.timeout_seconds}s"
                )
            message = parent.recv()
        except EOFError as exc:
            raise SourceFetchError("source fetch worker exited without a result") from exc
        finally:
            parent.close()
            if process.is_alive():
                process.kill()
            process.join()
        if message[0] == "error":
            error_types = {
                "ValueError": ValueError,
                "SourceFetchError": SourceFetchError,
                "SourceFetchTimeout": SourceFetchTimeout,
                "SourceTooLarge": SourceTooLarge,
            }
            raise error_types.get(message[1], SourceFetchError)(message[2])
        _, body, status, content_type, redirect_chain, retrieved_at = message
        return FetchedSource(
            url=url,
            body=body,
            retrieved_at=retrieved_at,
            published_at=published_at,
            edited_at=edited_at,
            edit_history=edit_history,
            media_type=content_type,
            status_code=status,
            redirect_chain=redirect_chain,
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
            redirect_chain=None,
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
            redirect_chain=source.redirect_chain,
        )

    def verify(self, snapshot: SourceSnapshot) -> bytes:
        if not isinstance(snapshot, SourceSnapshot):
            raise TypeError("snapshot must be a SourceSnapshot")
        return self.store.get_bytes(snapshot.content)

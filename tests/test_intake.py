"""Source intake tests cover bounded fetches and immutable response archives."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
from pathlib import Path
import time

import pytest


class Response:
    def __init__(
        self,
        body: bytes,
        *,
        content_length: int | None = None,
        status: int = 200,
        final_url: str | None = None,
    ):
        self._stream = io.BytesIO(body)
        self.status = status
        self.final_url = final_url
        self.headers = {"Content-Type": "application/json"}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, amount: int = -1) -> bytes:
        return self._stream.read(amount)

    def geturl(self) -> str | None:
        return self.final_url


def test_http_fetch_enforces_declared_and_streamed_size_limits():
    """Catches trusting Content-Length or buffering an oversized body."""
    from feature_rl.intake import BoundedHttpFetcher, SourceTooLarge

    declared = BoundedHttpFetcher(
        max_bytes=4,
        timeout_seconds=1,
        opener=lambda *_args, **_kwargs: Response(b"12345", content_length=5),
    )
    streamed = BoundedHttpFetcher(
        max_bytes=4,
        timeout_seconds=1,
        opener=lambda *_args, **_kwargs: Response(b"12345"),
    )

    with pytest.raises(SourceTooLarge):
        declared.fetch("https://example.invalid/source", edit_history="not_applicable")
    with pytest.raises(SourceTooLarge):
        streamed.fetch("https://example.invalid/source", edit_history="not_applicable")


def test_http_fetch_rejects_unsafe_scheme_and_reports_timeout():
    """Catches local-file intake and timeout failures masquerading as empty sources."""
    from feature_rl.intake import BoundedHttpFetcher, SourceFetchTimeout

    fetcher = BoundedHttpFetcher(
        max_bytes=10,
        timeout_seconds=1,
        opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()),
    )

    with pytest.raises(ValueError, match="https"):
        fetcher.fetch("file:///etc/passwd", edit_history="not_applicable")
    with pytest.raises(SourceFetchTimeout):
        fetcher.fetch("https://example.invalid/slow", edit_history="unavailable")


def test_http_fetch_rejects_disallowed_redirect_and_bounds_whole_operation():
    """Catches redirects escaping source policy and progress extending the deadline."""
    from feature_rl.intake import BoundedHttpFetcher, SourceFetchTimeout

    redirected = BoundedHttpFetcher(
        max_bytes=10,
        timeout_seconds=1,
        opener=lambda *_args, **_kwargs: Response(
            b"ok", final_url="http://127.0.0.1/private"
        ),
    )
    with pytest.raises(ValueError, match="redirect|https"):
        redirected.fetch("https://example.invalid/source", edit_history="unavailable")

    captured = BoundedHttpFetcher(
        max_bytes=10,
        timeout_seconds=1,
        opener=lambda *_args, **_kwargs: Response(
            b"ok", final_url="https://example.invalid/final"
        ),
    ).fetch("https://example.invalid/source", edit_history="unavailable")
    assert captured.redirect_chain == (
        "https://example.invalid/source",
        "https://example.invalid/final",
    )

    with pytest.raises(ValueError, match="nonpublic"):
        BoundedHttpFetcher(max_bytes=10, timeout_seconds=1).fetch(
            "https://127.0.0.1/private", edit_history="unavailable"
        )

    def blocked(*_args, **_kwargs):
        time.sleep(0.5)
        return Response(b"late")

    bounded = BoundedHttpFetcher(
        max_bytes=10,
        timeout_seconds=0.05,
        opener=blocked,
    )
    started = time.monotonic()
    with pytest.raises(SourceFetchTimeout):
        bounded.fetch("https://example.invalid/slow", edit_history="unavailable")
    assert time.monotonic() - started < 0.3


def test_cached_catalog_verifies_digest_and_confines_paths(tmp_path):
    """Catches accepting a changed response body or a manifest path outside the cache root."""
    from feature_rl.intake import CachedSourceCatalog, SourceIntegrityError

    cache = tmp_path / "cache"
    cache.mkdir()
    body = cache / "issue.json"
    body.write_bytes(b'{"number":3107}')
    digest = hashlib.sha256(body.read_bytes()).hexdigest()
    manifest = cache / "manifest.tsv"
    manifest.write_text(
        "name\thttp_status\tretrieved_at\turl\tbody_path\tsha256\n"
        f"issue\t200\t2026-09-19T00:00:00Z\thttps://api.github.com/issue\tcache/issue.json\t{digest}\n"
    )
    catalog = CachedSourceCatalog(tmp_path, manifest, max_bytes=1024)

    source = catalog.load("issue", edit_history="unavailable")
    assert source.body == b'{"number":3107}'
    assert source.retrieved_at == datetime(2026, 9, 19, tzinfo=timezone.utc)

    body.write_bytes(b"changed")
    with pytest.raises(SourceIntegrityError, match="digest"):
        catalog.load("issue", edit_history="unavailable")

    outside = tmp_path.parent / "outside.json"
    outside.write_bytes(b"outside")
    outside_digest = hashlib.sha256(b"outside").hexdigest()
    manifest.write_text(
        "name\thttp_status\tretrieved_at\turl\tbody_path\tsha256\n"
        f"issue\t200\t2026-09-19T00:00:00Z\thttps://api.github.com/issue\t../outside.json\t{outside_digest}\n"
    )
    with pytest.raises(SourceIntegrityError, match="outside"):
        CachedSourceCatalog(tmp_path, manifest, max_bytes=1024).load(
            "issue", edit_history="unavailable"
        )


def test_source_archive_preserves_edit_status_and_detects_store_corruption(tmp_path):
    """Catches dropping edit uncertainty or trusting a corrupted immutable object."""
    from feature_rl.artifacts import ArtifactIntegrityError, ArtifactStore
    from feature_rl.contracts import ActorRole, Visibility
    from feature_rl.intake import FetchedSource, SourceArchiver

    store_root = tmp_path / "objects"
    store = ArtifactStore(store_root, ActorRole.CONTROLLER)
    archiver = SourceArchiver(store)
    fetched = FetchedSource(
        url="https://github.com/pallets/click/issues/3107",
        body=b"current issue body",
        retrieved_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
        published_at=datetime(2025, 10, 13, tzinfo=timezone.utc),
        edited_at=None,
        edit_history="unavailable",
        media_type="application/json",
        status_code=200,
        redirect_chain=None,
    )

    snapshot = archiver.archive(fetched, Visibility.PRIVATE)
    assert snapshot.edit_history == "unavailable"
    assert snapshot.redirect_chain is None
    assert archiver.verify(snapshot) == b"current issue body"

    (store_root / f"{snapshot.content.sha256}.json").write_bytes(b"corrupted")
    with pytest.raises(ArtifactIntegrityError):
        archiver.verify(snapshot)

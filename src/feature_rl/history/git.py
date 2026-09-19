"""Read Git object data without checking out or executing repository content."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import selectors
import subprocess
import tempfile
import time
from typing import Literal
from pathlib import PurePosixPath


_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


class HistoryError(RuntimeError):
    """Base error for source graph inspection."""


class UnrecoverableHistory(HistoryError):
    """A required commit or relationship cannot be recovered."""


class HistoryOutputLimit(HistoryError):
    """A read-only Git command exceeded its declared output cap."""


@dataclass(frozen=True)
class CommitObject:
    revision: str
    tree: str
    parents: tuple[str, ...]


@dataclass(frozen=True)
class Reconstruction:
    integration: Literal["merge", "squash", "rebase", "linear"]
    baseline_commit: str
    reference_commit: str
    implementation_commits: tuple[str, ...]
    parents: tuple[str, ...]
    baseline_tree: str
    reference_tree: str
    source_tree: str | None
    patch_sha256: str


class GitHistory:
    """Bounded, read-only access to an existing Git object database."""

    def __init__(self, git_dir: Path, *, timeout_seconds: float = 30.0):
        if not isinstance(git_dir, Path) or git_dir.is_symlink() or not git_dir.is_dir():
            raise ValueError("git_dir must be an existing non-symlink directory")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.git_dir = git_dir.absolute()
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _revision(value: str) -> str:
        if not isinstance(value, str) or not _REVISION.fullmatch(value):
            raise ValueError("revision must be a full hexadecimal object id")
        return value

    def _argv(self, *args: str) -> tuple[str, ...]:
        return (
            "git",
            "--no-replace-objects",
            "--literal-pathspecs",
            f"--git-dir={self.git_dir}",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        )

    @staticmethod
    def _environment() -> dict[str, str]:
        return {
            "PATH": os.environ["PATH"],
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "LC_ALL": "C",
        }

    def _run_bytes(self, *args: str, max_bytes: int = 16_000_000) -> bytes:
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        argv = self._argv(*args)
        with tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=stderr,
                env=self._environment(),
            )
            assert process.stdout is not None
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + self.timeout_seconds
            output = bytearray()
            try:
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        process.kill()
                        process.wait()
                        raise HistoryError(f"Git command timed out: {args[0]}")
                    events = selector.select(remaining)
                    if not events:
                        continue
                    chunk = os.read(process.stdout.fileno(), 65_536)
                    if not chunk:
                        selector.unregister(process.stdout)
                        break
                    output.extend(chunk)
                    if len(output) > max_bytes:
                        process.kill()
                        process.wait()
                        raise HistoryOutputLimit(f"Git command exceeded {max_bytes} bytes")
                return_code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
            finally:
                selector.close()
                process.stdout.close()
                if process.poll() is None:
                    process.kill()
                    process.wait()
            if return_code != 0:
                stderr.seek(0)
                detail = stderr.read(4096).decode("utf-8", "replace").strip()
                raise UnrecoverableHistory(
                    f"Git {args[0]} failed with exit {return_code}: {detail}"
                )
            return bytes(output)

    def _run_text(self, *args: str, max_bytes: int = 1_000_000) -> str:
        try:
            return self._run_bytes(*args, max_bytes=max_bytes).decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise UnrecoverableHistory("Git metadata was not UTF-8") from exc

    def commit(self, revision: str) -> CommitObject:
        revision = self._revision(revision)
        try:
            object_type = self._run_text("cat-file", "-t", revision)
        except UnrecoverableHistory as exc:
            raise UnrecoverableHistory(f"commit object is unavailable: {revision}") from exc
        if object_type != "commit":
            raise UnrecoverableHistory(f"object is not a commit: {revision}")
        raw = self._run_text("cat-file", "-p", revision)
        tree = ""
        parents: list[str] = []
        for line in raw.splitlines():
            if not line:
                break
            if line.startswith("tree "):
                tree = line[5:]
            elif line.startswith("parent "):
                parents.append(line[7:])
        if not _REVISION.fullmatch(tree) or any(not _REVISION.fullmatch(p) for p in parents):
            raise UnrecoverableHistory(f"malformed commit object: {revision}")
        return CommitObject(revision=revision, tree=tree, parents=tuple(parents))

    def _commits_not_in(self, head: str, baseline: str) -> tuple[str, ...]:
        text = self._run_text("rev-list", "--reverse", head, "--not", baseline)
        commits = tuple(line for line in text.splitlines() if line)
        if not commits:
            raise UnrecoverableHistory("implementation commit set is empty")
        return commits

    def _patch_digest(self, baseline: str, reference: str) -> str:
        patch = self._run_bytes(
            "diff", "--binary", "--full-index", baseline, reference, "--",
            max_bytes=64_000_000,
        )
        return hashlib.sha256(patch).hexdigest()

    def reconstruct(
        self,
        integrated_after: str,
        *,
        integration: Literal["merge", "squash", "rebase", "linear", "unknown"],
        source_head: str | None = None,
        implementation_commits: tuple[str, ...] = (),
    ) -> Reconstruction:
        reference = self.commit(integrated_after)
        if integration == "unknown":
            raise ValueError("integration method must be proven, not guessed")

        source_tree: str | None = None
        if integration == "merge":
            if len(reference.parents) != 2:
                raise ValueError("merge integration requires exactly two parents")
            baseline = self.commit(reference.parents[0])
            actual_source_head = reference.parents[1]
            if source_head is not None and self._revision(source_head) != actual_source_head:
                raise ValueError("merge source head does not match second parent")
            source = self.commit(actual_source_head)
            source_tree = source.tree
            implementation = self._commits_not_in(actual_source_head, baseline.revision)
        elif integration == "squash":
            if len(reference.parents) != 1:
                raise ValueError("squash integration requires one target parent")
            if source_head is None:
                raise ValueError("squash integration requires a source head")
            source = self.commit(source_head)
            baseline = self.commit(reference.parents[0])
            if source.tree != reference.tree:
                raise ValueError("squash source tree does not equal integrated tree")
            source_tree = source.tree
            implementation = self._commits_not_in(source.revision, baseline.revision)
        else:
            if not implementation_commits:
                raise ValueError(f"{integration} integration requires a declared contiguous span")
            implementation = tuple(self._revision(item) for item in implementation_commits)
            if implementation[-1] != reference.revision:
                raise ValueError("contiguous span must end at the integrated commit")
            objects = tuple(self.commit(item) for item in implementation)
            if len(objects[0].parents) != 1:
                raise ValueError("contiguous span must start after one baseline parent")
            baseline = self.commit(objects[0].parents[0])
            previous = baseline.revision
            for item in objects:
                if item.parents != (previous,):
                    raise ValueError("declared integration span is not contiguous")
                previous = item.revision

        return Reconstruction(
            integration=integration,
            baseline_commit=baseline.revision,
            reference_commit=reference.revision,
            implementation_commits=implementation,
            parents=reference.parents,
            baseline_tree=baseline.tree,
            reference_tree=reference.tree,
            source_tree=source_tree,
            patch_sha256=self._patch_digest(baseline.revision, reference.revision),
        )

    def changed_paths(self, baseline: str, reference: str) -> tuple[str, ...]:
        baseline = self.commit(baseline).revision
        reference = self.commit(reference).revision
        raw = self._run_bytes(
            "diff", "--name-only", "-z", baseline, reference, "--",
            max_bytes=8_000_000,
        )
        try:
            return tuple(item.decode("utf-8") for item in raw.split(b"\0") if item)
        except UnicodeDecodeError as exc:
            raise UnrecoverableHistory("changed path is not UTF-8") from exc

    def path_object(self, revision: str, path: str) -> str:
        revision = self.commit(revision).revision
        parts = PurePosixPath(path).parts
        if not path or path.startswith("/") or ".." in parts or ":" in path:
            raise ValueError("Git path must be repository-relative")
        value = self._run_text("rev-parse", f"{revision}:{path}")
        if not _REVISION.fullmatch(value):
            raise UnrecoverableHistory(f"path object is unavailable: {path}")
        return value

    def archive_tree(self, revision: str, *, max_bytes: int) -> bytes:
        revision = self.commit(revision).revision
        return self._run_bytes("archive", "--format=tar", revision, max_bytes=max_bytes)

"""Read Git object data without checking out or executing repository content."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import difflib
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import signal
import subprocess
import time
from typing import Literal


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
    authored_at: datetime
    committed_at: datetime


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
    source_commits: tuple[str, ...]
    commit_mapping: tuple[tuple[str, str, str], ...]


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
            "--no-pager",
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
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_PAGER": "",
            "LC_ALL": "C",
        }

    @staticmethod
    def _group_members(group_id: int) -> tuple[tuple[int, str], ...]:
        try:
            result = subprocess.run(
                ("/bin/ps", "-axo", "pid=,pgid=,stat="),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=True,
                timeout=0.5,
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise HistoryError("could not verify Git process-group cleanup") from exc
        members = []
        for line in result.stdout.decode("ascii", "strict").splitlines():
            fields = line.split(None, 2)
            if len(fields) == 3 and int(fields[1]) == group_id:
                members.append((int(fields[0]), fields[2]))
        return tuple(members)

    @classmethod
    def _kill(cls, process: subprocess.Popen[bytes]) -> None:
        group_id = process.pid
        deadline = time.monotonic() + 1.0
        while True:
            try:
                os.killpg(group_id, signal.SIGKILL)
            except PermissionError:
                for member, state in cls._group_members(group_id):
                    if not state.startswith("Z"):
                        try:
                            os.kill(member, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
            except ProcessLookupError:
                pass
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=0.05)
            except subprocess.TimeoutExpired:
                pass
            live = tuple(
                member
                for member, state in cls._group_members(group_id)
                if not state.startswith("Z")
            )
            if not live:
                return
            if time.monotonic() >= deadline:
                raise HistoryError(
                    f"Git process group still has live descendants: {live!r}"
                )
            time.sleep(0.01)

    def _run_bytes(self, *args: str, max_bytes: int = 16_000_000) -> bytes:
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        argv = self._argv(*args)
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._environment(),
            start_new_session=True,
        )
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        deadline = time.monotonic() + self.timeout_seconds
        output = bytearray()
        errors = bytearray()
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._kill(process)
                    raise HistoryError(f"Git command timed out: {args[0]}")
                events = selector.select(remaining)
                if not events:
                    continue
                for key, _ in events:
                    chunk = os.read(key.fileobj.fileno(), 65_536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    target = output if key.data == "stdout" else errors
                    target.extend(chunk)
                    limit = max_bytes if key.data == "stdout" else 1_000_000
                    if len(target) > limit:
                        self._kill(process)
                        raise HistoryOutputLimit(
                            f"Git command {key.data} exceeded {limit} bytes"
                        )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._kill(process)
                raise HistoryError(f"Git command timed out: {args[0]}")
            return_code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            self._kill(process)
            raise HistoryError(f"Git command timed out: {args[0]}") from exc
        finally:
            selector.close()
            process.stdout.close()
            process.stderr.close()
            self._kill(process)
        if return_code != 0:
            detail = bytes(errors[:4096]).decode("utf-8", "replace").strip()
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
        authored_at: datetime | None = None
        committed_at: datetime | None = None
        for line in raw.splitlines():
            if not line:
                break
            if line.startswith("tree "):
                tree = line[5:]
            elif line.startswith("parent "):
                parents.append(line[7:])
            elif line.startswith("author "):
                authored_at = self._identity_time(line)
            elif line.startswith("committer "):
                committed_at = self._identity_time(line)
        if (
            not _REVISION.fullmatch(tree)
            or any(not _REVISION.fullmatch(p) for p in parents)
            or authored_at is None
            or committed_at is None
        ):
            raise UnrecoverableHistory(f"malformed commit object: {revision}")
        return CommitObject(
            revision=revision,
            tree=tree,
            parents=tuple(parents),
            authored_at=authored_at,
            committed_at=committed_at,
        )

    @staticmethod
    def _identity_time(line: str) -> datetime:
        match = re.search(r" ([0-9]+) [+-][0-9]{4}\Z", line)
        if match is None:
            raise UnrecoverableHistory("commit identity timestamp is malformed")
        return datetime.fromtimestamp(int(match.group(1)), timezone.utc)

    def _commits_not_in(self, head: str, baseline: str) -> tuple[str, ...]:
        text = self._run_text("rev-list", "--reverse", head, "--not", baseline)
        commits = tuple(line for line in text.splitlines() if line)
        if not commits:
            raise UnrecoverableHistory("implementation commit set is empty")
        return commits

    def _patch_digest(self, baseline: str, reference: str) -> str:
        patch = self._run_bytes(
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--diff-algorithm=myers",
            "--binary",
            "--full-index",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            baseline,
            reference,
            "--",
            max_bytes=64_000_000,
        )
        return hashlib.sha256(patch).hexdigest()

    def _linear_objects(
        self, commits: tuple[str, ...], *, expected_parent: str | None = None
    ) -> tuple[CommitObject, ...]:
        if not commits:
            raise ValueError("declared commit chain must be nonempty")
        objects = tuple(self.commit(self._revision(item)) for item in commits)
        previous = expected_parent
        if previous is None and objects[0].parents:
            previous = objects[0].parents[0]
        if previous is None:
            raise ValueError("declared source chain is not contiguous from its baseline")
        for item in objects:
            if item.parents != (previous,):
                raise ValueError("declared commit chain is not contiguous")
            previous = item.revision
        return objects

    def _first_parent_span(self, head: str, count: int) -> tuple[CommitObject, ...]:
        if count <= 0:
            raise ValueError("rewritten rebase requires source commits")
        reversed_objects: list[CommitObject] = []
        current = self.commit(head)
        for _ in range(count):
            reversed_objects.append(current)
            if len(current.parents) != 1:
                raise ValueError("integrated rebase span is not a one-parent chain")
            current = self.commit(current.parents[0])
        return tuple(reversed(reversed_objects))

    def _tree_entry(self, revision: str, path: str) -> tuple[str, str, str] | None:
        raw = self._run_bytes("ls-tree", "-z", revision, "--", path, max_bytes=1_000_000)
        if not raw:
            return None
        rows = [row for row in raw.split(b"\0") if row]
        if len(rows) != 1 or b"\t" not in rows[0]:
            raise UnrecoverableHistory(f"tree entry is ambiguous: {path}")
        metadata, encoded_path = rows[0].split(b"\t", 1)
        try:
            mode, object_type, object_id = metadata.decode("ascii").split(" ")
            decoded_path = encoded_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise UnrecoverableHistory(f"tree entry is malformed: {path}") from exc
        if decoded_path != path or not _REVISION.fullmatch(object_id):
            raise UnrecoverableHistory(f"tree entry does not match path: {path}")
        return mode, object_type, object_id

    def _blob(self, object_id: str, *, max_bytes: int) -> bytes:
        if self._run_text("cat-file", "-t", object_id) != "blob":
            raise UnrecoverableHistory(f"object is not a blob: {object_id}")
        return self._run_bytes("cat-file", "blob", object_id, max_bytes=max_bytes)

    @staticmethod
    def _line_delta(before: bytes, after: bytes) -> tuple[tuple[str, bytes, bytes], ...]:
        matcher = difflib.SequenceMatcher(
            None,
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            autojunk=False,
        )
        changes = []
        before_lines = before.splitlines(keepends=True)
        after_lines = after.splitlines(keepends=True)
        for tag, left_start, left_end, right_start, right_end in matcher.get_opcodes():
            if tag != "equal":
                changes.append(
                    (
                        tag,
                        b"".join(before_lines[left_start:left_end]),
                        b"".join(after_lines[right_start:right_end]),
                    )
                )
        return tuple(changes)

    def _delta_digest(self, parent: str, commit: str) -> str:
        paths = self.changed_paths(parent, commit)
        if not paths:
            raise ValueError("commit delta is empty")
        digest = hashlib.sha256()
        for path in paths:
            before_entry = self._tree_entry(parent, path)
            after_entry = self._tree_entry(commit, path)
            descriptor = (
                path,
                before_entry[0] if before_entry else None,
                after_entry[0] if after_entry else None,
                before_entry[1] if before_entry else None,
                after_entry[1] if after_entry else None,
            )
            digest.update(repr(descriptor).encode("utf-8"))
            if before_entry and before_entry[1] != "blob":
                digest.update(before_entry[2].encode("ascii"))
                before = b""
            else:
                before = (
                    self._blob(before_entry[2], max_bytes=16_000_000)
                    if before_entry
                    else b""
                )
            if after_entry and after_entry[1] != "blob":
                digest.update(after_entry[2].encode("ascii"))
                after = b""
            else:
                after = (
                    self._blob(after_entry[2], max_bytes=16_000_000)
                    if after_entry
                    else b""
                )
            if b"\0" in before or b"\0" in after:
                digest.update(b"binary\0")
                digest.update(hashlib.sha256(before).digest())
                digest.update(hashlib.sha256(after).digest())
            else:
                for tag, removed, added in self._line_delta(before, after):
                    digest.update(tag.encode("ascii") + b"\0")
                    digest.update(len(removed).to_bytes(8, "big") + removed)
                    digest.update(len(added).to_bytes(8, "big") + added)
        return digest.hexdigest()

    def reconstruct(
        self,
        integrated_after: str,
        *,
        integration: Literal["merge", "squash", "rebase", "linear", "unknown"],
        source_head: str | None = None,
        implementation_commits: tuple[str, ...] = (),
        source_commits: tuple[str, ...] = (),
    ) -> Reconstruction:
        reference = self.commit(integrated_after)
        if integration == "unknown":
            raise ValueError("integration method must be proven, not guessed")

        source_tree: str | None = None
        mapping: tuple[tuple[str, str, str], ...] = ()
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
            declared_source = tuple(self._revision(item) for item in source_commits)
            if not declared_source or declared_source[-1] != source.revision:
                raise ValueError("squash requires the complete declared source chain")
            self._linear_objects(declared_source, expected_parent=baseline.revision)
            if source.tree != reference.tree:
                raise ValueError("squash source tree does not equal integrated tree")
            source_tree = source.tree
            implementation = declared_source
        elif integration == "rebase":
            if source_head is None:
                raise ValueError("rebase integration requires a source head")
            declared_source = tuple(self._revision(item) for item in source_commits)
            if not declared_source or declared_source[-1] != self._revision(source_head):
                raise ValueError("rebase requires the complete declared source chain")
            source_objects = self._linear_objects(declared_source)
            integrated_objects = self._first_parent_span(reference.revision, len(declared_source))
            baseline = self.commit(integrated_objects[0].parents[0])
            implementation = tuple(item.revision for item in integrated_objects)
            if implementation == declared_source or any(
                left == right for left, right in zip(declared_source, implementation)
            ):
                raise ValueError("rebase must map distinct rewritten commit IDs")
            source_digests = tuple(
                self._delta_digest(item.parents[0], item.revision)
                for item in source_objects
            )
            integrated_digests = tuple(
                self._delta_digest(item.parents[0], item.revision)
                for item in integrated_objects
            )
            if (
                source_digests != integrated_digests
                or len(set(source_digests)) != len(source_digests)
            ):
                raise ValueError("rewritten rebase delta mapping is ambiguous or mismatched")
            mapping = tuple(zip(declared_source, implementation, source_digests))
        else:
            if not implementation_commits:
                raise ValueError(f"{integration} integration requires a declared contiguous span")
            implementation = tuple(self._revision(item) for item in implementation_commits)
            if implementation[-1] != reference.revision:
                raise ValueError("contiguous span must end at the integrated commit")
            objects = self._linear_objects(implementation)
            baseline = self.commit(objects[0].parents[0])

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
            source_commits=(
                tuple(item.revision for item in source_objects)
                if integration == "rebase"
                else implementation
            ),
            commit_mapping=mapping,
        )

    def changed_paths(self, baseline: str, reference: str) -> tuple[str, ...]:
        baseline = self.commit(baseline).revision
        reference = self.commit(reference).revision
        raw = self._run_bytes(
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--name-only",
            "-z",
            baseline,
            reference,
            "--",
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

    def path_bytes(self, revision: str, path: str, *, max_bytes: int) -> bytes:
        revision = self.commit(revision).revision
        parts = PurePosixPath(path).parts
        if not path or path.startswith("/") or ".." in parts or ":" in path:
            raise ValueError("Git path must be repository-relative")
        entry = self._tree_entry(revision, path)
        if entry is None or entry[1] != "blob":
            raise UnrecoverableHistory(f"path is not an available blob: {path}")
        return self._blob(entry[2], max_bytes=max_bytes)

    def archive_tree(self, revision: str, *, max_bytes: int) -> bytes:
        revision = self.commit(revision).revision
        return self._run_bytes("archive", "--format=tar", revision, max_bytes=max_bytes)

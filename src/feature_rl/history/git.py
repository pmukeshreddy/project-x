"""Read Git object data without checking out or executing repository content."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
_HUNK_HEADER = re.compile(
    rb"^@@ -([0-9]+)(?:,([0-9]+))? \+([0-9]+)(?:,([0-9]+))? @@"
)

_MAX_CHAIN_COMMITS = 4_096
_MAX_REWRITE_COMMITS = 128
_MAX_REWRITE_PATHS = 256
_MAX_REWRITE_PATH_BYTES = 16_384
_MAX_REWRITE_BLOB_BYTES = 1_000_000
_MAX_REWRITE_TOTAL_BLOB_BYTES = 8_000_000
_MAX_REWRITE_DIFF_BYTES = 8_000_000
_MAX_REWRITE_HUNKS = 1_024
_MAX_REWRITE_LINES = 500_000
_MAX_REWRITE_SEARCH_UNITS = 2_000_000


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


@dataclass(frozen=True)
class _RewriteDelta:
    proof_digest: str
    action_digest: str


@dataclass
class _RewriteBudget:
    deadline: float
    paths: int = 0
    blob_bytes: int = 0
    diff_bytes: int = 0
    hunks: int = 0
    lines: int = 0
    search_units: int = 0

    def charge(self, field: str, amount: int, limit: int) -> None:
        if amount < 0:
            raise ValueError("rewrite work charge must be nonnegative")
        if time.monotonic() >= self.deadline:
            raise HistoryError("history reconstruction timed out")
        value = getattr(self, field) + amount
        if value > limit:
            raise ValueError(f"rewritten rebase work budget exceeded: {field}")
        setattr(self, field, value)


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

    def _run_bytes(
        self,
        *args: str,
        max_bytes: int = 16_000_000,
        deadline: float | None = None,
        environment: dict[str, str] | None = None,
    ) -> bytes:
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        now = time.monotonic()
        command_deadline = now + self.timeout_seconds
        if deadline is not None:
            command_deadline = min(command_deadline, deadline)
        if command_deadline <= now:
            raise HistoryError(f"Git command timed out before start: {args[0]}")
        argv = self._argv(*args)
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._environment() | (environment or {}),
            start_new_session=True,
        )
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        output = bytearray()
        errors = bytearray()
        try:
            while selector.get_map():
                remaining = command_deadline - time.monotonic()
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
            remaining = command_deadline - time.monotonic()
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

    def _run_text(
        self,
        *args: str,
        max_bytes: int = 1_000_000,
        deadline: float | None = None,
    ) -> str:
        try:
            return self._run_bytes(
                *args, max_bytes=max_bytes, deadline=deadline
            ).decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise UnrecoverableHistory("Git metadata was not UTF-8") from exc

    def commit(self, revision: str, *, _deadline: float | None = None) -> CommitObject:
        revision = self._revision(revision)
        try:
            object_type = self._run_text(
                "cat-file", "-t", revision, deadline=_deadline
            )
        except UnrecoverableHistory as exc:
            raise UnrecoverableHistory(f"commit object is unavailable: {revision}") from exc
        if object_type != "commit":
            raise UnrecoverableHistory(f"object is not a commit: {revision}")
        raw = self._run_text("cat-file", "-p", revision, deadline=_deadline)
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

    @staticmethod
    def _check_deadline(deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise HistoryError("history reconstruction timed out")

    def _commits_not_in(
        self, head: str, baseline: str, *, deadline: float
    ) -> tuple[str, ...]:
        text = self._run_text(
            "rev-list", "--reverse", head, "--not", baseline, deadline=deadline
        )
        commits = tuple(line for line in text.splitlines() if line)
        if not commits:
            raise UnrecoverableHistory("implementation commit set is empty")
        if len(commits) > _MAX_CHAIN_COMMITS:
            raise ValueError("implementation commit chain exceeds supported bound")
        return commits

    def _patch_digest(self, baseline: str, reference: str, *, deadline: float) -> str:
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
            deadline=deadline,
        )
        value = hashlib.sha256(patch).hexdigest()
        self._check_deadline(deadline)
        return value

    def _linear_objects(
        self,
        commits: tuple[str, ...],
        *,
        expected_parent: str | None = None,
        deadline: float,
    ) -> tuple[CommitObject, ...]:
        if not commits:
            raise ValueError("declared commit chain must be nonempty")
        if len(commits) > _MAX_CHAIN_COMMITS:
            raise ValueError("declared commit chain exceeds supported bound")
        objects = tuple(
            self.commit(self._revision(item), _deadline=deadline) for item in commits
        )
        previous = expected_parent
        if previous is None and objects[0].parents:
            previous = objects[0].parents[0]
        if previous is None:
            raise ValueError("declared source chain is not contiguous from its baseline")
        for item in objects:
            self._check_deadline(deadline)
            if item.parents != (previous,):
                raise ValueError("declared commit chain is not contiguous")
            previous = item.revision
        return objects

    def _first_parent_span(
        self, head: str, count: int, *, deadline: float
    ) -> tuple[CommitObject, ...]:
        if count <= 0:
            raise ValueError("rewritten rebase requires source commits")
        if count > _MAX_REWRITE_COMMITS:
            raise ValueError("rewritten rebase commit chain exceeds supported bound")
        reversed_objects: list[CommitObject] = []
        current = self.commit(head, _deadline=deadline)
        for _ in range(count):
            self._check_deadline(deadline)
            reversed_objects.append(current)
            if len(current.parents) != 1:
                raise ValueError("integrated rebase span is not a one-parent chain")
            current = self.commit(current.parents[0], _deadline=deadline)
        return tuple(reversed(reversed_objects))

    def _tree_entry(
        self, revision: str, path: str, *, deadline: float
    ) -> tuple[str, str, str] | None:
        raw = self._run_bytes(
            "ls-tree",
            "-z",
            revision,
            "--",
            path,
            max_bytes=1_000_000,
            deadline=deadline,
        )
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

    def _blob_size(self, object_id: str, *, deadline: float) -> int:
        value = self._run_text("cat-file", "-s", object_id, deadline=deadline)
        if not value.isdecimal():
            raise UnrecoverableHistory(f"blob size is malformed: {object_id}")
        return int(value)

    def _blob(self, object_id: str, *, max_bytes: int, deadline: float) -> bytes:
        if self._run_text("cat-file", "-t", object_id, deadline=deadline) != "blob":
            raise UnrecoverableHistory(f"object is not a blob: {object_id}")
        return self._run_bytes(
            "cat-file", "blob", object_id, max_bytes=max_bytes, deadline=deadline
        )

    @staticmethod
    def _digest_value(digest, value: bytes) -> None:
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)

    @staticmethod
    def _hunk_ranges(patch: bytes) -> tuple[tuple[int, int, int, int], ...]:
        ranges = []
        for line in patch.splitlines():
            match = _HUNK_HEADER.match(line)
            if match is None:
                continue
            old_start = int(match.group(1))
            old_count = int(match.group(2) or b"1")
            new_start = int(match.group(3))
            new_count = int(match.group(4) or b"1")
            ranges.append((old_start, old_count, new_start, new_count))
        return tuple(ranges)

    @staticmethod
    def _line_index(start: int, count: int, length: int) -> int:
        index = start - 1 if count else start
        if index < 0 or index + count > length:
            raise UnrecoverableHistory("Git diff hunk range is malformed")
        return index

    @classmethod
    def _unique_hunk_location(
        cls,
        lines: tuple[bytes, ...],
        start: int,
        count: int,
        *,
        budget: _RewriteBudget,
    ) -> tuple[tuple[bytes, ...], tuple[bytes, ...], tuple[bytes, ...]]:
        index = cls._line_index(start, count, len(lines))
        prefix = lines[index - 1 : index] if index else ()
        changed = lines[index : index + count]
        suffix = lines[index + count : index + count + 1]
        needle = prefix + changed + suffix
        positions = (
            len(lines) + 1
            if not needle
            else max(0, len(lines) - len(needle) + 1)
        )
        budget.charge(
            "search_units",
            positions * max(1, len(needle)),
            _MAX_REWRITE_SEARCH_UNITS,
        )
        if not needle:
            matches = positions
        else:
            matches = 0
            for candidate in range(positions):
                if candidate % 1_024 == 0:
                    cls._check_deadline(budget.deadline)
                if lines[candidate : candidate + len(needle)] == needle:
                    matches += 1
                    if matches > 1:
                        break
        if matches != 1:
            raise ValueError("rewritten rebase context is ambiguous")
        return prefix, changed, suffix

    def _diff_for_path(
        self, parent: str, commit: str, path: str, *, deadline: float
    ) -> bytes:
        return self._run_bytes(
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--no-indent-heuristic",
            "--diff-algorithm=myers",
            "--binary",
            "--full-index",
            "--unified=0",
            "--no-color",
            "--no-prefix",
            parent,
            commit,
            "--",
            path,
            max_bytes=_MAX_REWRITE_DIFF_BYTES,
            deadline=deadline,
        )

    def _budgeted_blob(
        self,
        entry: tuple[str, str, str] | None,
        *,
        budget: _RewriteBudget,
    ) -> bytes:
        if entry is None:
            return b""
        size = self._blob_size(entry[2], deadline=budget.deadline)
        if size > _MAX_REWRITE_BLOB_BYTES:
            raise ValueError("rewritten rebase work budget exceeded: blob_bytes")
        budget.charge("blob_bytes", size, _MAX_REWRITE_TOTAL_BLOB_BYTES)
        return self._blob(
            entry[2], max_bytes=_MAX_REWRITE_BLOB_BYTES, deadline=budget.deadline
        )

    def _delta_digest(
        self, parent: str, commit: str, *, budget: _RewriteBudget
    ) -> _RewriteDelta:
        paths = self.changed_paths(
            parent,
            commit,
            _deadline=budget.deadline,
            _max_bytes=_MAX_REWRITE_DIFF_BYTES,
        )
        if not paths:
            raise ValueError("commit delta is empty")
        budget.charge("paths", len(paths), _MAX_REWRITE_PATHS)
        encoded_paths = tuple(path.encode("utf-8") for path in paths)
        if any(len(path) > _MAX_REWRITE_PATH_BYTES for path in encoded_paths):
            raise ValueError("rewritten rebase work budget exceeded: path_bytes")
        budget.charge(
            "diff_bytes",
            sum(len(path) + 1 for path in encoded_paths),
            _MAX_REWRITE_DIFF_BYTES,
        )
        proof = hashlib.sha256()
        action = hashlib.sha256()
        for path in paths:
            self._check_deadline(budget.deadline)
            before_entry = self._tree_entry(parent, path, deadline=budget.deadline)
            after_entry = self._tree_entry(commit, path, deadline=budget.deadline)
            descriptor = (
                path,
                before_entry[0] if before_entry else None,
                after_entry[0] if after_entry else None,
                before_entry[1] if before_entry else None,
                after_entry[1] if after_entry else None,
            )
            encoded_descriptor = repr(descriptor).encode("utf-8")
            self._digest_value(proof, encoded_descriptor)
            self._digest_value(action, encoded_descriptor)
            if (
                before_entry is not None and before_entry[1] != "blob"
            ) or (after_entry is not None and after_entry[1] != "blob"):
                opaque = repr(
                    (
                        before_entry[2] if before_entry else None,
                        after_entry[2] if after_entry else None,
                    )
                ).encode("ascii")
                self._digest_value(proof, b"object\0" + opaque)
                self._digest_value(action, b"object\0" + opaque)
                continue

            before = self._budgeted_blob(before_entry, budget=budget)
            after = self._budgeted_blob(after_entry, budget=budget)
            patch = self._diff_for_path(
                parent, commit, path, deadline=budget.deadline
            )
            budget.charge("diff_bytes", len(patch), _MAX_REWRITE_DIFF_BYTES)
            hunks = self._hunk_ranges(patch)
            budget.charge("hunks", len(hunks), _MAX_REWRITE_HUNKS)
            if b"\0" in before or b"\0" in after or (before != after and not hunks):
                opaque = b"opaque\0" + hashlib.sha256(before).digest()
                opaque += hashlib.sha256(after).digest()
                self._digest_value(proof, opaque)
                self._digest_value(action, opaque)
            else:
                before_lines = tuple(before.splitlines(keepends=True))
                after_lines = tuple(after.splitlines(keepends=True))
                budget.charge(
                    "lines",
                    len(before_lines) + len(after_lines),
                    _MAX_REWRITE_LINES,
                )
                previous_old_end = 0
                previous_new_end = 0
                self._digest_value(proof, b"text")
                self._digest_value(action, b"text")
                for old_start, old_count, new_start, new_count in hunks:
                    old_index = self._line_index(
                        old_start, old_count, len(before_lines)
                    )
                    new_index = self._line_index(
                        new_start, new_count, len(after_lines)
                    )
                    if old_index < previous_old_end or new_index < previous_new_end:
                        raise UnrecoverableHistory("Git diff hunks overlap")
                    previous_old_end = old_index + old_count
                    previous_new_end = new_index + new_count
                    old_prefix, removed, old_suffix = self._unique_hunk_location(
                        before_lines,
                        old_start,
                        old_count,
                        budget=budget,
                    )
                    new_prefix, added, new_suffix = self._unique_hunk_location(
                        after_lines,
                        new_start,
                        new_count,
                        budget=budget,
                    )
                    for value in (
                        b"hunk",
                        b"".join(old_prefix),
                        b"".join(removed),
                        b"".join(old_suffix),
                        b"".join(new_prefix),
                        b"".join(added),
                        b"".join(new_suffix),
                    ):
                        self._digest_value(proof, value)
                    for value in (
                        b"change",
                        b"".join(removed),
                        b"".join(added),
                    ):
                        self._digest_value(action, value)
        return _RewriteDelta(proof.hexdigest(), action.hexdigest())

    def reconstruct(
        self,
        integrated_after: str,
        *,
        integration: Literal["merge", "squash", "rebase", "linear", "unknown"],
        source_head: str | None = None,
        implementation_commits: tuple[str, ...] = (),
        source_commits: tuple[str, ...] = (),
    ) -> Reconstruction:
        deadline = time.monotonic() + self.timeout_seconds
        reference = self.commit(integrated_after, _deadline=deadline)
        if integration == "unknown":
            raise ValueError("integration method must be proven, not guessed")

        source_tree: str | None = None
        mapping: tuple[tuple[str, str, str], ...] = ()
        if integration == "merge":
            if len(reference.parents) != 2:
                raise ValueError("merge integration requires exactly two parents")
            baseline = self.commit(reference.parents[0], _deadline=deadline)
            actual_source_head = reference.parents[1]
            if source_head is not None and self._revision(source_head) != actual_source_head:
                raise ValueError("merge source head does not match second parent")
            source = self.commit(actual_source_head, _deadline=deadline)
            source_tree = source.tree
            implementation = self._commits_not_in(
                actual_source_head, baseline.revision, deadline=deadline
            )
        elif integration == "squash":
            if len(reference.parents) != 1:
                raise ValueError("squash integration requires one target parent")
            if source_head is None:
                raise ValueError("squash integration requires a source head")
            source = self.commit(source_head, _deadline=deadline)
            baseline = self.commit(reference.parents[0], _deadline=deadline)
            if len(source_commits) > _MAX_CHAIN_COMMITS:
                raise ValueError("declared commit chain exceeds supported bound")
            declared_source = tuple(self._revision(item) for item in source_commits)
            if not declared_source or declared_source[-1] != source.revision:
                raise ValueError("squash requires the complete declared source chain")
            self._linear_objects(
                declared_source, expected_parent=baseline.revision, deadline=deadline
            )
            if source.tree != reference.tree:
                raise ValueError("squash source tree does not equal integrated tree")
            source_tree = source.tree
            implementation = declared_source
        elif integration == "rebase":
            if source_head is None:
                raise ValueError("rebase integration requires a source head")
            if len(source_commits) > _MAX_REWRITE_COMMITS:
                raise ValueError("rewritten rebase commit chain exceeds supported bound")
            declared_source = tuple(self._revision(item) for item in source_commits)
            if not declared_source or declared_source[-1] != self._revision(source_head):
                raise ValueError("rebase requires the complete declared source chain")
            source_objects = self._linear_objects(
                declared_source, deadline=deadline
            )
            integrated_objects = self._first_parent_span(
                reference.revision, len(declared_source), deadline=deadline
            )
            baseline = self.commit(
                integrated_objects[0].parents[0], _deadline=deadline
            )
            implementation = tuple(item.revision for item in integrated_objects)
            if implementation == declared_source or any(
                left == right for left, right in zip(declared_source, implementation)
            ):
                raise ValueError("rebase must map distinct rewritten commit IDs")
            budget = _RewriteBudget(deadline)
            source_deltas = tuple(
                self._delta_digest(item.parents[0], item.revision, budget=budget)
                for item in source_objects
            )
            integrated_deltas = tuple(
                self._delta_digest(item.parents[0], item.revision, budget=budget)
                for item in integrated_objects
            )
            if (
                tuple(item.proof_digest for item in source_deltas)
                != tuple(item.proof_digest for item in integrated_deltas)
                or tuple(item.action_digest for item in source_deltas)
                != tuple(item.action_digest for item in integrated_deltas)
                or len({item.action_digest for item in source_deltas})
                != len(source_deltas)
            ):
                raise ValueError("rewritten rebase delta mapping is ambiguous or mismatched")
            mapping = tuple(
                (source, integrated, delta.proof_digest)
                for source, integrated, delta in zip(
                    declared_source, implementation, source_deltas
                )
            )
        else:
            if not implementation_commits:
                raise ValueError(f"{integration} integration requires a declared contiguous span")
            if len(implementation_commits) > _MAX_CHAIN_COMMITS:
                raise ValueError("declared commit chain exceeds supported bound")
            implementation = tuple(self._revision(item) for item in implementation_commits)
            if implementation[-1] != reference.revision:
                raise ValueError("contiguous span must end at the integrated commit")
            objects = self._linear_objects(implementation, deadline=deadline)
            baseline = self.commit(objects[0].parents[0], _deadline=deadline)

        return Reconstruction(
            integration=integration,
            baseline_commit=baseline.revision,
            reference_commit=reference.revision,
            implementation_commits=implementation,
            parents=reference.parents,
            baseline_tree=baseline.tree,
            reference_tree=reference.tree,
            source_tree=source_tree,
            patch_sha256=self._patch_digest(
                baseline.revision, reference.revision, deadline=deadline
            ),
            source_commits=(
                tuple(item.revision for item in source_objects)
                if integration == "rebase"
                else implementation
            ),
            commit_mapping=mapping,
        )

    def changed_paths(
        self,
        baseline: str,
        reference: str,
        *,
        _deadline: float | None = None,
        _max_bytes: int = 8_000_000,
    ) -> tuple[str, ...]:
        baseline = self.commit(baseline, _deadline=_deadline).revision
        reference = self.commit(reference, _deadline=_deadline).revision
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
            max_bytes=_max_bytes,
            deadline=_deadline,
        )
        try:
            result = tuple(item.decode("utf-8") for item in raw.split(b"\0") if item)
        except UnicodeDecodeError as exc:
            raise UnrecoverableHistory("changed path is not UTF-8") from exc
        if _deadline is not None:
            self._check_deadline(_deadline)
        return result

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
        deadline = time.monotonic() + self.timeout_seconds
        revision = self.commit(revision, _deadline=deadline).revision
        parts = PurePosixPath(path).parts
        if not path or path.startswith("/") or ".." in parts or ":" in path:
            raise ValueError("Git path must be repository-relative")
        entry = self._tree_entry(revision, path, deadline=deadline)
        if entry is None or entry[1] != "blob":
            raise UnrecoverableHistory(f"path is not an available blob: {path}")
        return self._blob(entry[2], max_bytes=max_bytes, deadline=deadline)

    def archive_tree(self, revision: str, *, max_bytes: int) -> bytes:
        revision = self.commit(revision).revision
        return self._run_bytes("archive", "--format=tar", revision, max_bytes=max_bytes)

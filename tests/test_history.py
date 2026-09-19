"""History reconstruction acceptance tests use inert local Git objects only."""
from __future__ import annotations

import subprocess
import os
from pathlib import Path
import time

import pytest


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", "-c", "core.hooksPath=/dev/null", *args),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={
            "PATH": __import__("os").environ["PATH"],
            "HOME": str(repo),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_AUTHOR_NAME": "fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        },
    )
    return result.stdout.strip()


def init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--initial-branch=main")
    return repo


def commit(repo: Path, name: str, content: str) -> str:
    (repo / name).write_text(content)
    git(repo, "add", name)
    git(repo, "commit", "-m", f"write {name}")
    return git(repo, "rev-parse", "HEAD")


def test_merge_uses_integration_first_parent_as_baseline(tmp_path):
    """Catches choosing the feature branch point instead of the target pre-merge tip."""
    from feature_rl.history import GitHistory

    repo = init_repo(tmp_path)
    root = commit(repo, "root.txt", "root\n")
    git(repo, "switch", "-c", "feature")
    feature = commit(repo, "feature.txt", "feature\n")
    git(repo, "switch", "main")
    target_tip = commit(repo, "target.txt", "target moved\n")
    git(repo, "merge", "--no-ff", "feature", "-m", "merge feature")
    integrated = git(repo, "rev-parse", "HEAD")

    result = GitHistory(repo / ".git").reconstruct(
        integrated, integration="merge"
    )

    assert result.baseline_commit == target_tip
    assert result.reference_commit == integrated
    assert result.parents == (target_tip, feature)
    assert result.implementation_commits == (feature,)
    assert result.baseline_commit != root


def test_squash_requires_source_tree_to_equal_integrated_tree(tmp_path):
    """Catches accepting a one-parent commit as a squash without source-diff proof."""
    from feature_rl.history import GitHistory

    repo = init_repo(tmp_path)
    baseline = commit(repo, "root.txt", "root\n")
    git(repo, "switch", "-c", "feature")
    first = commit(repo, "feature.txt", "one\n")
    head = commit(repo, "feature.txt", "two\n")
    git(repo, "switch", "main")
    git(repo, "merge", "--squash", "feature")
    git(repo, "commit", "-m", "squashed feature")
    integrated = git(repo, "rev-parse", "HEAD")

    result = GitHistory(repo / ".git").reconstruct(
        integrated,
        integration="squash",
        source_head=head,
        source_commits=(first, head),
    )

    assert result.baseline_commit == baseline
    assert result.implementation_commits == (first, head)
    assert result.reference_tree == result.source_tree
    assert len(result.patch_sha256) == 64

    git(repo, "reset", "--hard", baseline)
    commit(repo, "different.txt", "not the source tree\n")
    wrong_integration = git(repo, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="squash.*tree"):
        GitHistory(repo / ".git").reconstruct(
            wrong_integration,
            integration="squash",
            source_head=head,
            source_commits=(first, head),
        )


def test_squash_rejects_disconnected_equal_tree_source(tmp_path):
    """Catches binding an unrelated orphan history to H by tree equality alone."""
    from feature_rl.history import GitHistory

    repo = init_repo(tmp_path)
    baseline = commit(repo, "root.txt", "root\n")
    integrated = commit(repo, "feature.txt", "feature\n")
    tree = git(repo, "rev-parse", f"{integrated}^{{tree}}")
    orphan = git(repo, "commit-tree", tree, "-m", "unrelated equal tree")

    with pytest.raises(ValueError, match="source.*baseline|contiguous"):
        GitHistory(repo / ".git").reconstruct(
            integrated,
            integration="squash",
            source_head=orphan,
            source_commits=(orphan,),
        )


def test_linear_requires_exact_contiguous_first_parent_span(tmp_path):
    """Catches accepting interleaved target work inside a declared rebase span."""
    from feature_rl.history import GitHistory

    repo = init_repo(tmp_path)
    baseline = commit(repo, "root.txt", "root\n")
    first = commit(repo, "a.txt", "a\n")
    second = commit(repo, "b.txt", "b\n")

    result = GitHistory(repo / ".git").reconstruct(
        second,
        integration="linear",
        implementation_commits=(first, second),
    )
    assert result.baseline_commit == baseline
    assert result.implementation_commits == (first, second)

    git(repo, "reset", "--hard", first)
    interleaved = commit(repo, "target.txt", "target\n")
    final = commit(repo, "b.txt", "b\n")
    with pytest.raises(ValueError, match="contiguous"):
        GitHistory(repo / ".git").reconstruct(
            final,
            integration="linear",
            implementation_commits=(first, final),
        )
    assert interleaved not in (first, final)


def test_rebase_maps_distinct_source_commits_to_integrated_span(tmp_path):
    """Exercises a real rewritten source chain rather than relabeling a linear span."""
    from feature_rl.history import GitHistory

    repo = init_repo(tmp_path)
    baseline = commit(repo, "root.txt", "root\n")
    git(repo, "switch", "-c", "feature")
    source_first = commit(repo, "one.txt", "one\n")
    source_head = commit(repo, "two.txt", "two\n")
    git(repo, "switch", "main")
    target_tip = commit(repo, "target.txt", "target advanced\n")
    git(repo, "cherry-pick", source_first, source_head)
    integrated = git(repo, "rev-parse", "HEAD")
    integrated_first = git(repo, "rev-parse", "HEAD^")

    result = GitHistory(repo / ".git").reconstruct(
        integrated,
        integration="rebase",
        source_head=source_head,
        source_commits=(source_first, source_head),
    )

    assert result.baseline_commit == target_tip
    assert result.implementation_commits == (integrated_first, integrated)
    assert result.source_commits == (source_first, source_head)
    assert all(source != rewritten for source, rewritten, _ in result.commit_mapping)
    assert len({digest for _, _, digest in result.commit_mapping}) == 2


def test_rebase_rejects_reordered_and_whitespace_changed_deltas(tmp_path):
    """Catches patch-id-only binding and interleaved/reordered integrated spans."""
    from feature_rl.history import GitHistory

    repo = init_repo(tmp_path)
    commit(repo, "root.txt", "root\n")
    git(repo, "switch", "-c", "feature")
    source_first = commit(repo, "one.py", "if True:\n    value = 1\n")
    source_head = commit(repo, "two.py", "two = 2\n")
    git(repo, "switch", "main")
    commit(repo, "target.txt", "target\n")
    git(repo, "cherry-pick", source_head, source_first)
    reordered = git(repo, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="delta|mapping"):
        GitHistory(repo / ".git").reconstruct(
            reordered,
            integration="rebase",
            source_head=source_head,
            source_commits=(source_first, source_head),
        )

    git(repo, "reset", "--hard", "main~2")
    (repo / "one.py").write_text("if True:\n\tvalue = 1\n")
    whitespace_changed = commit(repo, "one.py", "if True:\n\tvalue = 1\n")
    with pytest.raises(ValueError, match="delta|mapping"):
        GitHistory(repo / ".git").reconstruct(
            whitespace_changed,
            integration="rebase",
            source_head=source_first,
            source_commits=(source_first,),
        )


def test_ambiguous_and_missing_graphs_are_rejected(tmp_path):
    """Catches guessing an integration method or silently accepting absent objects."""
    from feature_rl.history import GitHistory, UnrecoverableHistory

    repo = init_repo(tmp_path)
    integrated = commit(repo, "root.txt", "root\n")
    history = GitHistory(repo / ".git")

    with pytest.raises(ValueError, match="integration method"):
        history.reconstruct(integrated, integration="unknown")
    with pytest.raises(UnrecoverableHistory, match="commit object"):
        history.reconstruct("a" * 40, integration="linear")


def test_file_classification_preserves_mixed_review_gates():
    """Catches path heuristics falsely resolving a mixed-purpose source change."""
    from feature_rl.history import classify_changed_files

    result = classify_changed_files(
        (
            "CHANGES.rst",
            "src/click/core.py",
            "src/click/exceptions.py",
            "tests/test_commands.py",
            "pyproject.toml",
            "assets/unknown.bin",
        ),
        mixed_paths={
            "src/click/exceptions.py": "Feature implementation plus option formatting behavior",
        },
    )

    categories = {item.path: item.category for item in result.changed_files}
    assert categories == {
        "CHANGES.rst": "documentation",
        "src/click/core.py": "implementation",
        "src/click/exceptions.py": "mixed",
        "tests/test_commands.py": "tests",
        "pyproject.toml": "dependency_build",
        "assets/unknown.bin": "mixed",
    }
    assert result.manual_review_required == (
        "assets/unknown.bin",
        "src/click/exceptions.py",
    )


def test_git_archive_is_bounded_without_checkout(tmp_path):
    """Catches loading an unbounded source tree into controller memory."""
    from feature_rl.history import GitHistory, HistoryOutputLimit

    repo = init_repo(tmp_path)
    commit_id = commit(repo, "payload.txt", "x" * 32_000)
    history = GitHistory(repo / ".git")

    with pytest.raises(HistoryOutputLimit):
        history.archive_tree(commit_id, max_bytes=128)
    archive = history.archive_tree(commit_id, max_bytes=100_000)
    assert archive.startswith(b"payload.txt") is False
    assert b"x" * 100 in archive


def test_git_inspection_ignores_hostile_external_diff_configuration(tmp_path):
    """Catches repository-local diff helpers executing during read-only inspection."""
    from feature_rl.history import GitHistory

    repo = init_repo(tmp_path)
    baseline = commit(repo, "root.txt", "root\n")
    integrated = commit(repo, "feature.txt", "feature\n")
    marker = tmp_path / "helper-ran"
    helper = tmp_path / "external-diff"
    helper.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n")
    helper.chmod(0o755)
    git(repo, "config", "diff.external", str(helper))

    history = GitHistory(repo / ".git")
    result = history.reconstruct(
        integrated,
        integration="linear",
        implementation_commits=(integrated,),
    )
    assert result.baseline_commit == baseline
    assert history.changed_paths(baseline, integrated) == ("feature.txt",)
    history.path_object(integrated, "feature.txt")
    history.path_bytes(integrated, "feature.txt", max_bytes=100)
    history.archive_tree(integrated, max_bytes=100_000)
    assert not marker.exists()


def test_incomplete_promisor_repository_fails_without_fetching(tmp_path):
    """Catches read-only inspection lazily fetching and mutating missing objects."""
    from feature_rl.history import GitHistory, UnrecoverableHistory

    source_root = tmp_path / "source"
    source_root.mkdir()
    source = init_repo(source_root)
    revision = commit(source, "payload.txt", "payload\n")
    git(source, "config", "uploadpack.allowFilter", "true")
    cache = tmp_path / "cache.git"
    subprocess.run(
        (
            "git",
            "clone",
            "--bare",
            "--filter=blob:none",
            "--no-local",
            source.resolve().as_uri(),
            str(cache),
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    remote = cache / "objects" / "pack"
    before = tuple(sorted(path.name for path in remote.iterdir()))

    history = GitHistory(cache)
    with pytest.raises(UnrecoverableHistory, match="missing|failed"):
        history.archive_tree(revision, max_bytes=100_000)

    after = tuple(sorted(path.name for path in remote.iterdir()))
    assert after == before


@pytest.mark.parametrize("exit_status", [0, 7])
def test_git_runner_cleans_descendants_after_leader_exit(tmp_path, exit_status):
    """Catches treating a reaped command leader as proof its process group ended."""
    from feature_rl.history import GitHistory, UnrecoverableHistory

    repo = init_repo(tmp_path)
    commit(repo, "root.txt", "root\n")
    child_pid = tmp_path / "child.pid"
    helper = tmp_path / "background-child"
    helper.write_text(
        "#!/bin/sh\n"
        "sleep 30 </dev/null >/dev/null 2>&1 &\n"
        "echo $! > \"$1\"\n"
        "exit \"$2\"\n"
    )
    helper.chmod(0o755)
    history = GitHistory(repo / ".git")
    history._argv = lambda *_args: (str(helper), str(child_pid), str(exit_status))

    if exit_status:
        with pytest.raises(UnrecoverableHistory):
            history._run_bytes("diagnostic")
    else:
        assert history._run_bytes("diagnostic") == b""

    pid = int(child_pid.read_text())
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        status = subprocess.run(
            ("/bin/ps", "-o", "stat=", "-p", str(pid)),
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if not status or status.startswith("Z"):
            break
        time.sleep(0.01)
    else:
        os.kill(pid, 9)
        pytest.fail("Git command descendant survived process-group cleanup")


def test_git_runner_cleans_descendants_on_timeout(tmp_path):
    """Catches timeout cleanup killing only the foreground process."""
    from feature_rl.history import GitHistory, HistoryError

    repo = init_repo(tmp_path)
    commit(repo, "root.txt", "root\n")
    child_pid = tmp_path / "timeout-child.pid"
    helper = tmp_path / "blocked-with-child"
    helper.write_text(
        "#!/bin/sh\n"
        "sleep 30 </dev/null >/dev/null 2>&1 &\n"
        "echo $! > \"$1\"\n"
        "sleep 30\n"
    )
    helper.chmod(0o755)
    history = GitHistory(repo / ".git", timeout_seconds=0.2)
    history._argv = lambda *_args: (str(helper), str(child_pid))

    with pytest.raises(HistoryError, match="timed out"):
        history._run_bytes("diagnostic")

    pid = int(child_pid.read_text())
    status = subprocess.run(
        ("/bin/ps", "-o", "stat=", "-p", str(pid)),
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    assert not status or status.startswith("Z")

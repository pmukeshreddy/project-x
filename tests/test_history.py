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


def test_squash_rejects_source_rooted_before_advanced_target(tmp_path):
    """Documents the pilot's intentionally narrow B-rooted squash proof."""
    from feature_rl.history import GitHistory

    repo = init_repo(tmp_path)
    commit(repo, "root.txt", "root\n")
    git(repo, "switch", "-c", "feature")
    source = commit(repo, "feature.txt", "feature\n")
    git(repo, "switch", "main")
    commit(repo, "target.txt", "target advanced\n")
    git(repo, "merge", "--squash", "feature")
    git(repo, "commit", "-m", "squashed feature after target advance")
    integrated = git(repo, "rev-parse", "HEAD")

    with pytest.raises(ValueError, match="contiguous"):
        GitHistory(repo / ".git").reconstruct(
            integrated,
            integration="squash",
            source_head=source,
            source_commits=(source,),
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


def test_rebase_maps_unique_context_after_line_number_movement(tmp_path):
    """Allows target movement when the changed semantic location stays unique."""
    from feature_rl.history import GitHistory

    repo = init_repo(tmp_path)
    commit(repo, "settings.txt", "header\nold value\nfooter\n")
    git(repo, "switch", "-c", "feature")
    source = commit(repo, "settings.txt", "header\nnew value\nfooter\n")
    git(repo, "switch", "main")
    commit(repo, "settings.txt", "preface\nheader\nold value\nfooter\n")
    git(repo, "cherry-pick", source)
    integrated = git(repo, "rev-parse", "HEAD")

    result = GitHistory(repo / ".git").reconstruct(
        integrated,
        integration="rebase",
        source_head=source,
        source_commits=(source,),
    )

    assert result.source_commits == (source,)
    assert result.implementation_commits == (integrated,)
    assert result.commit_mapping[0][:2] == (source, integrated)


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


def test_rebase_rejects_ambiguous_duplicate_deltas(tmp_path):
    """Catches claiming a unique source mapping for indistinguishable deltas."""
    from feature_rl.history import GitHistory

    repo = init_repo(tmp_path)
    commit(repo, "repeated.txt", "")
    git(repo, "switch", "-c", "feature")
    source_first = commit(repo, "repeated.txt", "same\n")
    source_head = commit(repo, "repeated.txt", "same\nsame\n")
    git(repo, "switch", "main")
    commit(repo, "target.txt", "target\n")
    git(repo, "cherry-pick", source_first, source_head)
    integrated = git(repo, "rev-parse", "HEAD")

    with pytest.raises(ValueError, match="ambiguous"):
        GitHistory(repo / ".git").reconstruct(
            integrated,
            integration="rebase",
            source_head=source_head,
            source_commits=(source_first, source_head),
        )


def test_rebase_rejects_same_edit_at_different_semantic_occurrence(tmp_path):
    """Catches mapping identical bytes to a different uniquely named section."""
    from feature_rl.history import GitHistory

    repo = init_repo(tmp_path)
    commit(repo, "a.txt", "section-one\nx\nsection-two\nx\n")
    git(repo, "switch", "-c", "feature")
    source = commit(repo, "a.txt", "section-one\ny\nsection-two\nx\n")
    git(repo, "switch", "main")
    commit(repo, "target.txt", "target\n")
    integrated = commit(repo, "a.txt", "section-one\nx\nsection-two\ny\n")

    with pytest.raises(ValueError, match="mapping|context"):
        GitHistory(repo / ".git").reconstruct(
            integrated,
            integration="rebase",
            source_head=source,
            source_commits=(source,),
        )


def test_rebase_repeated_line_proof_rejects_inside_declared_bound(
    tmp_path, monkeypatch
):
    """Catches returning to unbounded SequenceMatcher work on repeated lines."""
    import difflib

    from feature_rl.history import GitHistory

    def unbounded_matcher_used(*_args, **_kwargs):
        pytest.fail("rewrite proof used unbounded SequenceMatcher")

    monkeypatch.setattr(difflib, "SequenceMatcher", unbounded_matcher_used)
    repo = init_repo(tmp_path)
    repeated = "x\n" * 10_000 + "end\n"
    commit(repo, "repeated.txt", repeated)
    git(repo, "switch", "-c", "feature")
    source = commit(repo, "repeated.txt", "new\n" + repeated)
    git(repo, "switch", "main")
    commit(repo, "target.txt", "target\n")
    git(repo, "cherry-pick", source)
    integrated = git(repo, "rev-parse", "HEAD")

    with pytest.raises(ValueError, match="ambiguous|work budget"):
        GitHistory(repo / ".git", timeout_seconds=3).reconstruct(
            integrated,
            integration="rebase",
            source_head=source,
            source_commits=(source,),
        )


def test_rebase_blob_work_cap_rejects_before_unbounded_matching(
    tmp_path, monkeypatch
):
    """Makes exhaustion of the controller-side rewrite budget explicit."""
    import importlib

    from feature_rl.history import GitHistory

    git_module = importlib.import_module("feature_rl.history.git")
    monkeypatch.setattr(git_module, "_MAX_REWRITE_BLOB_BYTES", 8)
    repo = init_repo(tmp_path)
    commit(repo, "bounded.txt", "123456789\n")
    git(repo, "switch", "-c", "feature")
    source = commit(repo, "bounded.txt", "changed value\n")
    git(repo, "switch", "main")
    commit(repo, "target.txt", "target\n")
    git(repo, "cherry-pick", source)
    integrated = git(repo, "rev-parse", "HEAD")

    with pytest.raises(ValueError, match="work budget.*blob_bytes"):
        GitHistory(repo / ".git").reconstruct(
            integrated,
            integration="rebase",
            source_head=source,
            source_commits=(source,),
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
    assert result.mixed_paths_for_qualification == (
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


def test_optional_path_lookup_distinguishes_absence_from_unavailable_history(tmp_path):
    """Optional repository metadata may be absent; missing Git objects remain errors."""
    from feature_rl.history import GitHistory, UnrecoverableHistory

    repo = init_repo(tmp_path)
    revision = commit(repo, 'root.txt', 'root\n')
    history = GitHistory(repo / '.git')
    assert history.optional_path_object(revision, '.github') is None
    assert history.optional_path_object(revision, 'root.txt') == git(repo, 'rev-parse', f'{revision}:root.txt')
    with pytest.raises(UnrecoverableHistory):
        history.path_object(revision, '.github')
    with pytest.raises(UnrecoverableHistory):
        history.optional_path_object('f' * 40, '.github')

    tree = git(repo, 'rev-parse', f'{revision}^{{tree}}')
    (repo / '.git' / 'objects' / tree[:2] / tree[2:]).unlink()
    with pytest.raises(UnrecoverableHistory):
        history.optional_path_object(revision, '.github')


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


def test_git_runner_cleans_descendants_on_timeout(tmp_path, monkeypatch):
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

    import importlib

    git_module = importlib.import_module("feature_rl.history.git")
    real_popen = subprocess.Popen
    observed_child = False

    def cleanup_setup(process):
        current_popen = git_module.subprocess.Popen
        git_module.subprocess.Popen = real_popen
        try:
            GitHistory._kill(process)
        finally:
            git_module.subprocess.Popen = current_popen

    def popen_after_child(*args, **kwargs):
        nonlocal observed_child
        process = real_popen(*args, **kwargs)
        if tuple(args[0])[0] != str(helper):
            return process
        startup_deadline = time.monotonic() + 2
        child = None
        while child is None:
            try:
                pid_text = child_pid.read_text().strip()
                candidate = int(pid_text) if pid_text.isdecimal() else 0
                if candidate > 0:
                    os.kill(candidate, 0)
                    child = candidate
                    break
            except (FileNotFoundError, ProcessLookupError):
                pass
            except OSError as error:
                cleanup_setup(process)
                pytest.fail(f"could not verify timeout helper child: {error}")
            if process.poll() is not None or time.monotonic() >= startup_deadline:
                cleanup_setup(process)
                pytest.fail("timeout helper did not start a live background child")
            time.sleep(0.005)
        observed_child = True
        return process

    with monkeypatch.context() as patch:
        patch.setattr(git_module.subprocess, "Popen", popen_after_child)
        with pytest.raises(HistoryError, match="timed out"):
            history._run_bytes("diagnostic")

    assert observed_child
    pid = int(child_pid.read_text())
    status = subprocess.run(
        ("/bin/ps", "-o", "stat=", "-p", str(pid)),
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    assert not status or status.startswith("Z")

"""History reconstruction acceptance tests use inert local Git objects only."""
from __future__ import annotations

import subprocess
from pathlib import Path

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
        integrated, integration="squash", source_head=head
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
            wrong_integration, integration="squash", source_head=head
        )


@pytest.mark.parametrize("integration", ["rebase", "linear"])
def test_rebase_and_linear_require_exact_contiguous_first_parent_span(
    tmp_path, integration
):
    """Catches accepting interleaved target work inside a declared rebase span."""
    from feature_rl.history import GitHistory

    repo = init_repo(tmp_path)
    baseline = commit(repo, "root.txt", "root\n")
    first = commit(repo, "a.txt", "a\n")
    second = commit(repo, "b.txt", "b\n")

    result = GitHistory(repo / ".git").reconstruct(
        second,
        integration=integration,
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
            integration=integration,
            implementation_commits=(first, final),
        )
    assert interleaved not in (first, final)


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

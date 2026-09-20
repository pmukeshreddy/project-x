"""Connected M1 intake uses synthetic source data; real Click evidence runs separately."""
from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", "-c", "core.hooksPath=/dev/null", *args),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(repo),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_AUTHOR_NAME": "fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "GIT_AUTHOR_DATE": "2026-02-21T17:26:34Z",
            "GIT_COMMITTER_DATE": "2026-02-21T17:26:34Z",
        },
    )
    return result.stdout.strip()


def commit(repo: Path, message: str) -> str:
    git(repo, "add", ".")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def write_cache(
    root: Path,
    sources: dict[str, tuple[str, bytes]],
    *,
    retrieved_at: dict[str, str] | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    cache = root / "cache"
    cache.mkdir()
    rows = ["name\thttp_status\tretrieved_at\turl\tbody_path\tsha256"]
    for name, (url, body) in sources.items():
        path = cache / f"{name}.body"
        path.write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        rows.append(
            f"{name}\t200\t{(retrieved_at or {}).get(name, '2026-09-19T00:00:00Z')}"
            f"\t{url}\tcache/{name}.body\t{digest}"
        )
    manifest = cache / "manifest.tsv"
    manifest.write_text("\n".join(rows) + "\n")
    return manifest


def test_connected_intake_builds_private_source_pair_and_safe_authoring_view(tmp_path):
    """Catches bypassing reconstruction or exposing H/diff through authoring outputs."""
    from feature_rl.artifacts import AccessDenied, ArtifactStore
    from feature_rl.contracts import ActorRole, CandidateRecord, Partition, SourcePair, Visibility
    from feature_rl.history import GitHistory
    from feature_rl.intake import (
        CachedSourceCatalog,
        GitHubPullRequestIntake,
        PullRequestIntakeSpec,
        SourceArchiver,
    )
    from feature_rl.splits import Relation, SplitPlanner

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--initial-branch=main")
    (repo / "LICENSE.txt").write_text("BSD fixture\n")
    (repo / ".github/workflows").mkdir(parents=True)
    (repo / ".github/workflows/tests.yml").write_text("name: tests\n")
    (repo / "src").mkdir()
    (repo / "src/core.py").write_text("OLD = True\n")
    baseline = commit(repo, "baseline")
    license_blob = git(repo, "rev-parse", f"{baseline}:LICENSE.txt")

    git(repo, "switch", "-c", "feature")
    (repo / "src/core.py").write_text("FEATURE = 'suggestions'\n")
    (repo / "src/options.py").write_text("FORMAT = 'changed too'\n")
    (repo / "tests").mkdir()
    (repo / "tests/test_commands.py").write_text("# feature checks\n")
    source_head = commit(repo, "implement feature")
    git(repo, "switch", "main")
    git(repo, "merge", "--squash", "feature")
    integrated = commit(repo, "squash feature")

    current_base_field = "f" * 40  # Deliberately wrong: graph ancestry must win.
    sources = {
        "pr": (
            "https://api.github.com/repos/example/project/pulls/7",
            json.dumps(
                {
                    "number": 7,
                    "title": "Add suggestions",
                    "body": "Implementation-aware PR body",
                    "created_at": "2026-02-23T16:10:36Z",
                    "updated_at": "2026-05-15T00:45:46Z",
                    "merged_at": "2026-04-29T23:53:15Z",
                    "merge_commit_sha": integrated,
                    "base": {"sha": current_base_field},
                    "head": {"sha": source_head},
                }
            ).encode(),
        ),
        "issue": (
            "https://api.github.com/repos/example/project/issues/3",
            json.dumps(
                {
                    "number": 3,
                    "title": "Suggest commands",
                    "body": "Current request text",
                    "created_at": "2025-10-13T12:17:36Z",
                    "updated_at": "2026-05-15T00:45:40Z",
                }
            ).encode(),
        ),
        "comments": (
            "https://api.github.com/repos/example/project/issues/3/comments",
            json.dumps(
                [
                    {
                        "id": 1,
                        "body": "Preimplementation clarification",
                        "created_at": "2026-02-13T20:25:22Z",
                        "updated_at": "2026-02-13T20:25:22Z",
                    },
                    {
                        "id": 2,
                        "body": "Future implementation detail",
                        "created_at": "2026-04-30T00:00:00Z",
                        "updated_at": "2026-04-30T00:00:00Z",
                    },
                    {
                        "id": 3,
                        "body": "A pre-cutoff comment edited after implementation",
                        "created_at": "2026-02-01T00:00:00Z",
                        "updated_at": "2026-04-30T00:00:00Z",
                    },
                ]
            ).encode(),
        ),
        "commits": (
            "https://api.github.com/repos/example/project/pulls/7/commits",
            json.dumps([{"sha": source_head}]).encode(),
        ),
        "files": (
            "https://api.github.com/repos/example/project/pulls/7/files",
            json.dumps(
                [
                    {"filename": "src/core.py"},
                    {"filename": "src/options.py"},
                    {"filename": "tests/test_commands.py"},
                ]
            ).encode(),
        ),
        "license": (
            "https://api.github.com/repos/example/project/license",
            json.dumps(
                {"sha": license_blob, "license": {"spdx_id": "BSD-3-Clause"}}
            ).encode(),
        ),
        "license-text": (
            "https://raw.githubusercontent.com/example/project/main/LICENSE.txt",
            b"BSD fixture\n",
        ),
        "checks": (
            f"https://api.github.com/repos/example/project/commits/{source_head}/check-runs",
            json.dumps({"total_count": 1, "check_runs": [{"conclusion": "success"}]}).encode(),
        ),
    }
    manifest_path = write_cache(tmp_path, sources)
    store_root = tmp_path / "objects"
    controller_store = ArtifactStore(store_root, ActorRole.CONTROLLER)
    catalog = CachedSourceCatalog(tmp_path, manifest_path, max_bytes=1_000_000)
    split = SplitPlanner(
        (
            Relation("upstream", "fork", "fork", "head repository metadata"),
            Relation("fork", "pr-7", "descendant", "PR belongs to fork"),
            Relation("pr-7", "issue-3", "same_request", "PR closes issue"),
        )
    ).assign({"pr-7": Partition.TRAIN})
    cutoff = datetime(2026, 2, 13, 20, 25, 22, tzinfo=timezone.utc)
    spec = PullRequestIntakeSpec(
        repository_url="https://github.com/example/project",
        repository_family="example-project",
        request_lineage=("pr-7", "issue-3"),
        partition_source_ids=("upstream", "fork", "pr-7", "issue-3"),
        source_names=("pr", "issue", "comments", "commits", "files", "license", "checks"),
        license_text_name="license-text",
        pr_name="pr",
        issue_name="issue",
        comments_name="comments",
        commits_name="commits",
        files_name="files",
        license_name="license",
        integration="squash",
        admissible_cutoff=cutoff,
        recorded_at=datetime(2026, 9, 19, 1, 0, tzinfo=timezone.utc),
        provenance_label="reconstructed_specification",
        mixed_paths={"src/options.py": "Companion option formatting behavior"},
        max_tree_archive_bytes=1_000_000,
    )
    intake = GitHubPullRequestIntake(
        store=controller_store,
        catalog=catalog,
        history=GitHistory(repo / ".git"),
        factory_revision="a" * 40,
    )
    result = intake.ingest(spec, split)
    repeated = intake.ingest(spec, split)
    assert repeated == result

    candidate = controller_store.get_artifact(result.candidate)
    pair = controller_store.get_artifact(result.source_pair)
    assert isinstance(candidate, CandidateRecord)
    assert isinstance(pair, SourcePair)
    assert candidate.partition is Partition.TRAIN
    assert candidate.schema_version == 2
    assert candidate.provenance_label == "reconstructed_specification"
    assert candidate.commits.integration == "squash"
    assert candidate.commits.target_before == baseline
    assert candidate.commits.integrated_after == integrated
    assert candidate.screening.disposition.value == "success"
    assert pair.baseline_commit == baseline
    assert pair.schema_version == 2
    assert pair.provenance_label == candidate.provenance_label
    assert result.provenance_label == candidate.provenance_label
    assert pair.reference_commit == integrated
    assert pair.reference.visibility is Visibility.PRIVATE
    assert pair.baseline.visibility is Visibility.AUTHORING
    assert {item.path: item.category for item in pair.changed_files}["src/options.py"] == "mixed"
    assert result.mixed_paths_for_qualification == ('src/options.py',)

    author_store = ArtifactStore(store_root, ActorRole.AUTHOR)
    baseline_tar = author_store.get_bytes(result.authoring.baseline)
    request_evidence = author_store.get_bytes(result.authoring.request_evidence)
    request_payload = json.loads(request_evidence)
    assert request_payload["provenance_label"] == "reconstructed_specification"
    assert request_payload["issue"]["retrieved_at"] == "2026-09-19T00:00:00Z"
    assert len(request_payload["issue"]["source_response_sha256"]) == 64
    assert [item["id"] for item in request_payload["comments"]] == [1]
    assert integrated.encode() not in request_evidence
    assert source_head.encode() not in request_evidence
    assert b"suggestions" not in baseline_tar
    with pytest.raises(AccessDenied):
        author_store.get_artifact(result.source_pair)

    with pytest.raises(ValueError, match="cutoff"):
        replace(
            spec,
            admissible_cutoff=datetime(2099, 1, 1, tzinfo=timezone.utc),
            recorded_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError, match="historical_request"):
        intake.ingest(replace(spec, provenance_label="historical_request"), split)

    historical_sources = dict(sources)
    historical_sources["issue"] = (
        sources["issue"][0],
        json.dumps(
            {
                "number": 3,
                "title": "Suggest commands",
                "body": "Archived preimplementation request",
                "created_at": "2025-10-13T12:17:36Z",
                "updated_at": "2026-01-15T20:38:10Z",
            }
        ).encode(),
    )
    historical_root = tmp_path / "historical"
    historical_manifest = write_cache(
        historical_root,
        historical_sources,
        retrieved_at={"issue": "2026-02-01T00:00:00Z"},
    )
    historical_store = ArtifactStore(
        historical_root / "objects", ActorRole.CONTROLLER
    )
    historical_intake = GitHubPullRequestIntake(
        store=historical_store,
        catalog=CachedSourceCatalog(
            historical_root, historical_manifest, max_bytes=1_000_000
        ),
        history=GitHistory(repo / ".git"),
        factory_revision="b" * 40,
    )
    historical_result = historical_intake.ingest(
        replace(spec, provenance_label="historical_request"), split
    )
    historical_candidate = historical_store.get_artifact(historical_result.candidate)
    historical_pair = historical_store.get_artifact(historical_result.source_pair)
    assert historical_result.provenance_label == "historical_request"
    assert historical_candidate.provenance_label == "historical_request"
    assert historical_pair.provenance_label == "historical_request"

    bad_license_sources = dict(sources)
    bad_license_sources["license-text"] = (
        sources["license-text"][0],
        b"unrelated license text\n",
    )
    bad_license_root = tmp_path / "bad-license"
    bad_license_manifest = write_cache(bad_license_root, bad_license_sources)
    bad_license_intake = GitHubPullRequestIntake(
        store=ArtifactStore(bad_license_root / "objects", ActorRole.CONTROLLER),
        catalog=CachedSourceCatalog(
            bad_license_root, bad_license_manifest, max_bytes=1_000_000
        ),
        history=GitHistory(repo / ".git"),
        factory_revision="c" * 40,
    )
    with pytest.raises(ValueError, match="license text"):
        bad_license_intake.ingest(spec, split)

    git(repo, "switch", "-c", "license-change")
    (repo / "LICENSE.txt").write_text("MIT fixture\n")
    changed_license_source = commit(repo, "change license")
    git(repo, "switch", "main")
    git(repo, "merge", "--squash", "license-change")
    changed_license_h = commit(repo, "squash changed license")
    changed_license_sources = dict(sources)
    changed_license_sources["pr"] = (
        sources["pr"][0],
        json.dumps(
            {
                **json.loads(sources["pr"][1]),
                "merge_commit_sha": changed_license_h,
                "head": {"sha": changed_license_source},
            }
        ).encode(),
    )
    changed_license_sources["commits"] = (
        sources["commits"][0], json.dumps([{"sha": changed_license_source}]).encode()
    )
    changed_license_sources["files"] = (
        sources["files"][0], json.dumps([{"filename": "LICENSE.txt"}]).encode()
    )
    changed_license_root = tmp_path / "changed-license"
    changed_license_manifest = write_cache(
        changed_license_root, changed_license_sources
    )
    changed_license_intake = GitHubPullRequestIntake(
        store=ArtifactStore(changed_license_root / "objects", ActorRole.CONTROLLER),
        catalog=CachedSourceCatalog(
            changed_license_root, changed_license_manifest, max_bytes=1_000_000
        ),
        history=GitHistory(repo / ".git"),
        factory_revision="d" * 40,
    )
    with pytest.raises(ValueError, match="reference changes LICENSE"):
        changed_license_intake.ingest(
            replace(spec, mixed_paths={"LICENSE.txt": "License change requires review"}),
            split,
        )


def test_connected_intake_maps_distinct_rebased_commits(tmp_path):
    """Catches passing source SHAs off as the post-rewrite integrated span."""
    from feature_rl.artifacts import ArtifactStore
    from feature_rl.contracts import ActorRole, Partition
    from feature_rl.history import GitHistory
    from feature_rl.intake import (
        CachedSourceCatalog,
        GitHubPullRequestIntake,
        PullRequestIntakeSpec,
    )
    from feature_rl.splits import Relation, SplitPlanner

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--initial-branch=main")
    (repo / "LICENSE.txt").write_text("BSD fixture\n")
    (repo / ".github/workflows").mkdir(parents=True)
    (repo / ".github/workflows/tests.yml").write_text("name: tests\n")
    (repo / "src").mkdir()
    (repo / "src/root.py").write_text("ROOT = True\n")
    baseline = commit(repo, "baseline")
    license_blob = git(repo, "rev-parse", f"{baseline}:LICENSE.txt")
    git(repo, "switch", "-c", "feature")
    (repo / "src/one.py").write_text("ONE = 1\n")
    source_first = commit(repo, "source one")
    (repo / "src/two.py").write_text("TWO = 2\n")
    source_head = commit(repo, "source two")
    git(repo, "switch", "main")
    (repo / "target.txt").write_text("target advanced\n")
    target_tip = commit(repo, "target advance")
    git(repo, "cherry-pick", source_first, source_head)
    integrated = git(repo, "rev-parse", "HEAD")
    integrated_first = git(repo, "rev-parse", "HEAD^")

    sources = {
        "pr": (
            "https://api.github.com/repos/example/project/pulls/8",
            json.dumps(
                {
                    "number": 8,
                    "title": "Rebased feature",
                    "body": "Current PR",
                    "created_at": "2026-02-23T16:10:36Z",
                    "updated_at": "2026-04-29T23:53:15Z",
                    "merged_at": "2026-04-29T23:53:15Z",
                    "merge_commit_sha": integrated,
                    "base": {"sha": baseline},
                    "head": {"sha": source_head},
                }
            ).encode(),
        ),
        "issue": (
            "https://api.github.com/repos/example/project/issues/4",
            json.dumps(
                {
                    "number": 4,
                    "title": "Feature",
                    "body": "Current request",
                    "created_at": "2025-10-13T12:17:36Z",
                    "updated_at": "2026-04-29T23:53:15Z",
                }
            ).encode(),
        ),
        "comments": (
            "https://api.github.com/repos/example/project/issues/4/comments",
            b"[]",
        ),
        "commits": (
            "https://api.github.com/repos/example/project/pulls/8/commits",
            json.dumps([{"sha": source_first}, {"sha": source_head}]).encode(),
        ),
        "files": (
            "https://api.github.com/repos/example/project/pulls/8/files",
            json.dumps(
                [{"filename": "src/one.py"}, {"filename": "src/two.py"}]
            ).encode(),
        ),
        "license": (
            "https://api.github.com/repos/example/project/license",
            json.dumps(
                {"sha": license_blob, "license": {"spdx_id": "BSD-3-Clause"}}
            ).encode(),
        ),
        "license-text": (
            "https://raw.githubusercontent.com/example/project/main/LICENSE.txt",
            b"BSD fixture\n",
        ),
    }
    manifest = write_cache(tmp_path / "cache-root", sources)
    store = ArtifactStore(tmp_path / "objects", ActorRole.CONTROLLER)
    split = SplitPlanner(
        (Relation("pr-8", "issue-4", "same_request", "PR closes issue"),)
    ).assign({"pr-8": Partition.TRAIN})
    spec = PullRequestIntakeSpec(
        repository_url="https://github.com/example/project",
        repository_family="example-project",
        request_lineage=("pr-8", "issue-4"),
        partition_source_ids=("pr-8", "issue-4"),
        source_names=("pr", "issue", "comments", "commits", "files", "license"),
        license_text_name="license-text",
        pr_name="pr",
        issue_name="issue",
        comments_name="comments",
        commits_name="commits",
        files_name="files",
        license_name="license",
        integration="rebase",
        admissible_cutoff=datetime(2026, 2, 13, tzinfo=timezone.utc),
        recorded_at=datetime(2026, 9, 19, 1, tzinfo=timezone.utc),
        provenance_label="reconstructed_specification",
        mixed_paths={},
        max_tree_archive_bytes=1_000_000,
    )
    result = GitHubPullRequestIntake(
        store=store,
        catalog=CachedSourceCatalog(
            tmp_path / "cache-root", manifest, max_bytes=1_000_000
        ),
        history=GitHistory(repo / ".git"),
        factory_revision="e" * 40,
    ).ingest(spec, split)
    pair = store.get_artifact(result.source_pair)
    proof = json.loads(store.get_bytes(pair.verification[0].artifacts[0]))

    assert pair.baseline_commit == target_tip
    assert pair.relationship.implementation_commits == (integrated_first, integrated)
    assert [item["source"] for item in proof["commit_mapping"]] == [
        source_first,
        source_head,
    ]
    assert [item["integrated"] for item in proof["commit_mapping"]] == [
        integrated_first,
        integrated,
    ]

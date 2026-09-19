"""Reproduce the real Click 3228 intake from inert cached source data."""
from __future__ import annotations

import argparse
from datetime import datetime
import io
import json
from pathlib import Path
import tarfile

from feature_rl.artifacts import AccessDenied, ArtifactStore
from feature_rl.contracts import ActorRole, Partition
from feature_rl.history import GitHistory
from feature_rl.intake import (
    CachedSourceCatalog,
    GitHubPullRequestIntake,
    PullRequestIntakeSpec,
)
from feature_rl.splits import Relation, SplitPlanner


def utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must explicitly use UTC")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--factory-revision", required=True)
    parser.add_argument("--recorded-at", type=utc, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve(strict=True)
    research = workspace / ".feature-rl/research/M1"
    store = ArtifactStore(research / "production-store", ActorRole.CONTROLLER)
    catalog = CachedSourceCatalog(
        workspace, research / "source-manifest.tsv", max_bytes=10_000_000
    )
    relations = (
        Relation(
            "pallets-click",
            "rc2215-click",
            "fork",
            "PR metadata identifies RC2215/click as the head fork of pallets/click",
        ),
        Relation(
            "rc2215-click",
            "click-pr-3228",
            "descendant",
            "PR 3228 head belongs to RC2215/click",
        ),
        Relation(
            "pallets-click",
            "click-issue-3107",
            "descendant",
            "Issue 3107 belongs to pallets/click",
        ),
        Relation(
            "click-pr-3228",
            "click-issue-3107",
            "same_request",
            "PR body and closure event link PR 3228 to issue 3107",
        ),
    )
    split = SplitPlanner(relations).assign({"click-pr-3228": Partition.TRAIN})
    source_names = (
        "repo",
        "pr-3228",
        "issue-3228",
        "issue-3228-comments",
        "issue-3228-timeline",
        "pr-3228-commits",
        "pr-3228-files",
        "pr-3228-review-comments",
        "pr-3228-reviews",
        "issue-3107",
        "issue-3107-comments",
        "issue-3107-timeline",
        "license",
        "commit-integration-831c8f0",
        "commit-pr-head-dc3e1e0",
        "commit-pr-source-f6da6a7",
        "compare-base-integration",
        "check-runs-pr-head",
        "check-suites-pr-head",
        "status-pr-head",
        "check-runs-integration",
        "check-suites-integration",
        "status-integration",
    )
    spec = PullRequestIntakeSpec(
        repository_url="https://github.com/pallets/click",
        repository_family="pallets-click",
        request_lineage=("click-pr-3228", "click-issue-3107"),
        partition_source_ids=(
            "pallets-click",
            "rc2215-click",
            "click-pr-3228",
            "click-issue-3107",
        ),
        source_names=source_names,
        license_text_name="license-main",
        pr_name="pr-3228",
        issue_name="issue-3107",
        comments_name="issue-3107-comments",
        commits_name="pr-3228-commits",
        files_name="pr-3228-files",
        license_name="license",
        integration="squash",
        admissible_cutoff=datetime.fromisoformat("2026-02-13T20:25:22+00:00"),
        recorded_at=args.recorded_at,
        provenance_label="reconstructed_specification",
        mixed_paths={
            "src/click/exceptions.py": (
                "Adds NoSuchCommand while also changing NoSuchOption formatting and matching"
            ),
            "src/click/parser.py": (
                "Moves NoSuchOption close-match calculation as a companion behavior change"
            ),
            "tests/test_options.py": (
                "Changes option-formatting expectations outside the core command suggestion request"
            ),
        },
        max_tree_archive_bytes=10_000_000,
    )
    intake = GitHubPullRequestIntake(
        store=store,
        catalog=catalog,
        history=GitHistory(research / "git/click.git"),
        factory_revision=args.factory_revision,
    )
    result = intake.ingest(spec, split)
    repeated = intake.ingest(spec, split)
    candidate = store.get_artifact(result.candidate)
    pair = store.get_artifact(result.source_pair)
    reconstruction_proof = json.loads(
        store.get_bytes(pair.verification[0].artifacts[0])
    )
    author_store = ArtifactStore(research / "production-store", ActorRole.AUTHOR)
    author_request = author_store.get_bytes(result.authoring.request_evidence)
    author_baseline = author_store.get_bytes(result.authoring.baseline)
    with tarfile.open(fileobj=io.BytesIO(author_baseline), mode="r:") as archive:
        baseline_names = archive.getnames()
    private_pair_denied = False
    try:
        author_store.get_artifact(result.source_pair)
    except AccessDenied:
        private_pair_denied = True
    private_needles = (
        pair.reference_commit,
        *pair.relationship.implementation_commits,
    )
    output = {
        "factory_revision": args.factory_revision,
        "recorded_at": args.recorded_at.isoformat().replace("+00:00", "Z"),
        "idempotent_retry": repeated == result,
        "candidate_ref": result.candidate.model_dump(mode="json"),
        "source_pair_ref": result.source_pair.model_dump(mode="json"),
        "authoring_view": {
            "request_evidence": result.authoring.request_evidence.model_dump(mode="json"),
            "baseline": result.authoring.baseline.model_dump(mode="json"),
            "license_text": result.authoring.license_text.model_dump(mode="json"),
        },
        "reference_ref": result.reference.model_dump(mode="json"),
        "authoring_view_checks": {
            "all_refs_authoring_or_public": all(
                ref.visibility.value in {"authoring", "public"}
                for ref in (
                    result.authoring.request_evidence,
                    result.authoring.baseline,
                    result.authoring.license_text,
                )
            ),
            "private_source_pair_denied_to_author": private_pair_denied,
            "request_excludes_reference_and_source_commit_ids": all(
                item.encode("ascii") not in author_request for item in private_needles
            ),
            "baseline_archive_excludes_git_metadata": all(
                name != ".git" and not name.startswith(".git/")
                for name in baseline_names
            ),
        },
        "candidate": {
            "schema_version": candidate.schema_version,
            "provenance_label": candidate.provenance_label,
            "repository_family": candidate.repository_family,
            "request_lineage": list(candidate.request_lineage),
            "partition": candidate.partition.value,
            "source_count": len(candidate.sources),
            "license": candidate.license.spdx_id,
            "screening_disposition": candidate.screening.disposition.value,
            "screening_reason": candidate.screening.reason,
            "costs": [item.model_dump(mode="json") for item in candidate.costs],
            "redirect_evidence": {
                "known": sum(item.redirect_chain is not None for item in candidate.sources),
                "unavailable": sum(item.redirect_chain is None for item in candidate.sources),
            },
        },
        "source_pair": {
            "schema_version": pair.schema_version,
            "provenance_label": pair.provenance_label,
            "baseline_commit": pair.baseline_commit,
            "reference_commit": pair.reference_commit,
            "integration": pair.relationship.integration,
            "implementation_commits": list(pair.relationship.implementation_commits),
            "parents": list(pair.relationship.parents),
            "admissible_cutoff": pair.admissible_cutoff.isoformat().replace(
                "+00:00", "Z"
            ),
            "changed_files": [
                item.model_dump(mode="json") for item in pair.changed_files
            ],
            "costs": [item.model_dump(mode="json") for item in pair.costs],
        },
        "reconstruction_proof": reconstruction_proof,
        "manual_review_required": list(result.manual_review_required),
        "result_provenance_label": result.provenance_label,
        "split_assignments": [
            {
                "source_id": item.source_id,
                "partition": item.partition.value,
                "component_id": item.component_id,
            }
            for item in split.assignments
        ],
        "split_relations": [
            {
                "left": item.left,
                "right": item.right,
                "kind": item.kind,
                "proof": item.proof,
            }
            for item in split.relations
        ],
    }
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

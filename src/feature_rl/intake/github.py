"""Connected GitHub PR intake over verified response and Git object caches."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import json
import re
from typing import Literal, Mapping

from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import (
    ArtifactRef,
    CandidateRecord,
    CommitRelationship,
    CostRecord,
    Disposition,
    EvidenceRecord,
    LicenseRecord,
    Partition,
    Provenance,
    ScreeningDecision,
    SourcePair,
    SourceSnapshot,
    Visibility,
)
from feature_rl.history import GitHistory, classify_changed_files
from feature_rl.splits import PartitionManifest

from .sources import CachedSourceCatalog, FetchedSource, SourceArchiver


_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("source timestamp is invalid") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("source timestamp must use UTC")
    return parsed


def _json(data: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("source JSON has duplicate keys")
            result[key] = value
        return result

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("source body is not valid JSON") from exc


def _canonical(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _reconstruct_pull_request(history, integration, pr_data, commits_data):
    """Use the same graph proof during acquisition and immutable intake."""
    if not isinstance(commits_data, list) or not commits_data:
        raise ValueError("PR commit response must be a nonempty array")
    source_commits = tuple(item["sha"] for item in commits_data)
    if any(not isinstance(item, str) or not _REVISION.fullmatch(item) for item in source_commits):
        raise ValueError("PR commits contain an invalid full object ID")
    source_head = pr_data["head"]["sha"]
    if source_head != source_commits[-1]:
        raise ValueError("PR head does not match the final source commit")
    reconstruction = history.reconstruct(
        pr_data["merge_commit_sha"], integration=integration,
        source_head=source_head if integration in {"merge", "squash", "rebase"} else None,
        implementation_commits=source_commits if integration == "linear" else (),
        source_commits=source_commits if integration in {"squash", "rebase"} else (),
    )
    recovered = (reconstruction.source_commits if integration == "rebase"
                 else reconstruction.implementation_commits)
    if recovered != source_commits:
        raise ValueError("Git graph and PR commit list disagree")
    return reconstruction, source_commits


def _unknown_cost(category: str, note: str) -> CostRecord:
    return CostRecord(
        category=category,
        wall_seconds=None,
        cpu_seconds=None,
        gpu_seconds=None,
        input_tokens=None,
        output_tokens=None,
        human_minutes=None,
        usd=None,
        measurement="unknown",
        note=note,
    )


@dataclass(frozen=True)
class PullRequestIntakeSpec:
    repository_url: str
    repository_family: str
    request_lineage: tuple[str, ...]
    partition_source_ids: tuple[str, ...]
    source_names: tuple[str, ...]
    license_text_name: str
    pr_name: str
    issue_name: str
    comments_name: str
    commits_name: str
    files_name: str
    license_name: str
    integration: Literal["merge", "squash", "rebase", "linear"]
    admissible_cutoff: datetime
    recorded_at: datetime
    provenance_label: Literal["historical_request", "reconstructed_specification"]
    mixed_paths: Mapping[str, str]
    max_tree_archive_bytes: int
    license_path: str = 'LICENSE.txt'
    additional_pages: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self):
        from feature_rl.environments.archive import safe_path
        if safe_path(self.license_path) != self.license_path:
            raise ValueError('canonical repository license path required')
        required = {
            self.pr_name,
            self.issue_name,
            self.comments_name,
            self.commits_name,
            self.files_name,
            self.license_name,
        }
        if not required.issubset(self.source_names):
            raise ValueError("source_names omits a required PR intake response")
        if len(set(self.source_names)) != len(self.source_names):
            raise ValueError("source_names must be unique")
        if set(self.additional_pages).difference({self.comments_name, self.commits_name, self.files_name}):
            raise ValueError("only comments, commits and files can have additional response pages")
        pages = tuple(name for names in self.additional_pages.values() for name in names)
        if (len(pages) != len(set(pages)) or set(pages).intersection(required)
                or not set(pages).issubset(self.source_names)
                or any(len(names) > 29 for names in self.additional_pages.values())):
            raise ValueError("additional response pages must be unique named source bodies")
        if not self.request_lineage or not self.partition_source_ids:
            raise ValueError("request lineage and partition source IDs are required")
        if self.admissible_cutoff.utcoffset() != timezone.utc.utcoffset(
            self.admissible_cutoff
        ):
            raise ValueError("admissible_cutoff must explicitly use UTC")
        if self.recorded_at.utcoffset() != timezone.utc.utcoffset(self.recorded_at):
            raise ValueError("recorded_at must explicitly use UTC")
        if self.admissible_cutoff > self.recorded_at:
            raise ValueError("admissible_cutoff cannot follow recorded_at")
        if type(self.max_tree_archive_bytes) is not int or self.max_tree_archive_bytes <= 0:
            raise ValueError("max_tree_archive_bytes must be positive")


@dataclass(frozen=True)
class AuthoringSourceView:
    request_evidence: ArtifactRef
    baseline: ArtifactRef
    license_text: ArtifactRef

    def __post_init__(self):
        for ref in (self.request_evidence, self.baseline, self.license_text):
            if ref.visibility not in {Visibility.PUBLIC, Visibility.AUTHORING}:
                raise ValueError("authoring view contains a restricted reference")


@dataclass(frozen=True)
class PullRequestIntakeResult:
    candidate: ArtifactRef
    source_pair: ArtifactRef
    authoring: AuthoringSourceView
    reference: ArtifactRef
    mixed_paths_for_qualification: tuple[str, ...]
    provenance_label: Literal["historical_request", "reconstructed_specification"]


class GitHubPullRequestIntake:
    """Build reviewed M0 artifacts without importing or executing source code."""

    def __init__(
        self,
        *,
        store: ArtifactStore,
        catalog: CachedSourceCatalog,
        history: GitHistory,
        factory_revision: str,
    ):
        if not isinstance(store, ArtifactStore):
            raise TypeError("store must be an ArtifactStore")
        if not isinstance(catalog, CachedSourceCatalog) or not isinstance(history, GitHistory):
            raise TypeError("catalog and history must use M1 production types")
        if not _REVISION.fullmatch(factory_revision):
            raise ValueError("factory_revision must be a full commit digest")
        self.store = store
        self.catalog = catalog
        self.history = history
        self.factory_revision = factory_revision
        self.archiver = SourceArchiver(store)

    @staticmethod
    def _partition(spec: PullRequestIntakeSpec, manifest: PartitionManifest) -> Partition:
        by_id = {item.source_id: item.partition for item in manifest.assignments}
        missing = set(spec.partition_source_ids).difference(by_id)
        if missing:
            raise ValueError(f"partition closure omits source IDs: {sorted(missing)!r}")
        values = {by_id[item] for item in spec.partition_source_ids}
        if len(values) != 1 or Partition.UNASSIGNED in values:
            raise ValueError("related sources do not have one explicit partition")
        return next(iter(values))

    def _load_sources(self, spec: PullRequestIntakeSpec):
        editable = {spec.pr_name, spec.issue_name, spec.comments_name,
                    *spec.additional_pages.get(spec.comments_name, ())}
        loaded = {
            name: self.catalog.load(
                name,
                edit_history=(
                    "unavailable"
                    if name in editable
                    else "not_applicable"
                ),
                media_type="application/json",
            )
            for name in spec.source_names
        }
        pr_data = _json(loaded[spec.pr_name].body)
        issue_data = _json(loaded[spec.issue_name].body)
        if not isinstance(pr_data, dict) or not isinstance(issue_data, dict):
            raise ValueError("PR and issue sources must be JSON objects")
        loaded[spec.pr_name] = replace(
            loaded[spec.pr_name], published_at=_parse_time(pr_data["created_at"])
        )
        loaded[spec.issue_name] = replace(
            loaded[spec.issue_name], published_at=_parse_time(issue_data["created_at"])
        )
        license_text = self.catalog.load(
            spec.license_text_name,
            edit_history="not_applicable",
            media_type="text/plain",
        )
        return loaded, license_text, pr_data, issue_data

    @staticmethod
    def _array(loaded, spec, name):
        values = []
        for page in (name, *spec.additional_pages.get(name, ())):
            value = _json(loaded[page].body)
            if not isinstance(value, list):
                raise ValueError("paginated source body must be a JSON array")
            values.extend(value)
        key = {spec.commits_name: 'sha', spec.files_name: 'filename', spec.comments_name: 'id'}[name]
        identities = [item[key] for item in values]
        if len(identities) != len(set(identities)):
            raise ValueError("source pages contain duplicate identities")
        return values

    def ingest(
        self, spec: PullRequestIntakeSpec, partition_manifest: PartitionManifest
    ) -> PullRequestIntakeResult:
        if not isinstance(spec, PullRequestIntakeSpec) or not isinstance(
            partition_manifest, PartitionManifest
        ):
            raise TypeError("ingest requires a spec and partition manifest")
        partition = self._partition(spec, partition_manifest)
        loaded, license_text_source, pr_data, issue_data = self._load_sources(spec)
        commits_data = self._array(loaded, spec, spec.commits_name)
        files_data = self._array(loaded, spec, spec.files_name)
        comments_data = self._array(loaded, spec, spec.comments_name)
        license_data = _json(loaded[spec.license_name].body)
        reconstruction, source_commits = _reconstruct_pull_request(
            self.history, spec.integration, pr_data, commits_data)

        issue_created = _parse_time(issue_data["created_at"])
        merged_at = _parse_time(pr_data["merged_at"])
        if issue_created > spec.admissible_cutoff:
            raise ValueError("visible issue did not exist by the admissible cutoff")
        if spec.admissible_cutoff > merged_at or merged_at > spec.recorded_at:
            raise ValueError("cutoff, integration, and recording chronology is contradictory")
        all_sources = (*loaded.values(), license_text_source)
        if any(item.retrieved_at > spec.recorded_at for item in all_sources):
            raise ValueError("recorded_at predates a source retrieval")
        source_objects = tuple(self.history.commit(item) for item in source_commits)
        implementation_started = min(
            timestamp
            for item in source_objects
            for timestamp in (item.authored_at, item.committed_at)
        )
        if not (
            spec.admissible_cutoff <= implementation_started <= merged_at
        ):
            raise ValueError("admissible cutoff is not preimplementation")
        historical_snapshot = loaded[spec.issue_name].retrieved_at <= spec.admissible_cutoff
        if spec.provenance_label == "historical_request" and not historical_snapshot:
            raise ValueError(
                "historical_request requires an archived preimplementation issue snapshot"
            )
        issue_updated = _parse_time(issue_data["updated_at"])
        if spec.provenance_label == "historical_request" and (
            issue_updated > loaded[spec.issue_name].retrieved_at
        ):
            raise ValueError("historical issue metadata postdates its archived snapshot")

        response_paths = tuple(item["filename"] for item in files_data)
        graph_paths = self.history.changed_paths(
            reconstruction.baseline_commit, reconstruction.reference_commit
        )
        if set(response_paths) != set(graph_paths) or len(response_paths) != len(graph_paths):
            raise ValueError("Git graph and PR changed-file response disagree")
        classification = classify_changed_files(
            graph_paths, mixed_paths=spec.mixed_paths
        )

        license_blob = license_data["sha"]
        if not isinstance(license_blob, str) or not _REVISION.fullmatch(license_blob):
            raise ValueError("license response lacks a full Git object ID")
        baseline_license_blob = self.history.path_object(
            reconstruction.baseline_commit, spec.license_path
        )
        reference_license_blob = self.history.path_object(
            reconstruction.reference_commit, spec.license_path
        )
        if baseline_license_blob != license_blob:
            raise ValueError("license response does not match the configured baseline license path")
        if reference_license_blob != baseline_license_blob:
            raise ValueError("reference changes the configured license; automatic license scope is unsafe")
        baseline_license_text = self.history.path_bytes(
            reconstruction.baseline_commit,
            spec.license_path,
            max_bytes=spec.max_tree_archive_bytes,
        )
        reference_license_text = self.history.path_bytes(
            reconstruction.reference_commit,
            spec.license_path,
            max_bytes=spec.max_tree_archive_bytes,
        )
        if (
            license_text_source.body != baseline_license_text
            or reference_license_text != baseline_license_text
        ):
            raise ValueError("archived license text does not match B and H at the configured license path")
        spdx_id = license_data["license"]["spdx_id"]
        if not isinstance(spdx_id, str) or not spdx_id:
            raise ValueError("license response lacks an SPDX identifier")

        snapshots: list[SourceSnapshot] = [
            self.archiver.archive(loaded[name], Visibility.PRIVATE)
            for name in spec.source_names
        ]
        license_snapshot = self.archiver.archive(
            license_text_source, Visibility.AUTHORING
        )
        snapshots.append(license_snapshot)

        baseline_archive = self.history.archive_tree(
            reconstruction.baseline_commit, max_bytes=spec.max_tree_archive_bytes
        )
        reference_archive = self.history.archive_tree(
            reconstruction.reference_commit, max_bytes=spec.max_tree_archive_bytes
        )
        baseline_ref = self.store.put_bytes(
            baseline_archive, "source-archive", Visibility.AUTHORING
        )
        reference_ref = self.store.put_bytes(
            reference_archive, "source-archive", Visibility.PRIVATE
        )

        assignments = [
            item
            for item in partition_manifest.assignments
            if item.source_id in spec.partition_source_ids
        ]
        proof = {
            "baseline_commit": reconstruction.baseline_commit,
            "reference_commit": reconstruction.reference_commit,
            "baseline_tree": reconstruction.baseline_tree,
            "reference_tree": reconstruction.reference_tree,
            "source_tree": reconstruction.source_tree,
            "patch_sha256": reconstruction.patch_sha256,
            "current_api_base": pr_data["base"]["sha"],
            "current_api_base_matches_graph": (
                pr_data["base"]["sha"] == reconstruction.baseline_commit
            ),
            "ci_config_baseline_object": self.history.path_object(
                reconstruction.baseline_commit, ".github"
            ),
            "ci_config_reference_object": self.history.path_object(
                reconstruction.reference_commit, ".github"
            ),
            "partition_assignments": [
                {
                    "source_id": item.source_id,
                    "partition": item.partition.value,
                    "component_id": item.component_id,
                }
                for item in assignments
            ],
            "relations": [
                {
                    "left": item.left,
                    "right": item.right,
                    "kind": item.kind,
                    "proof": item.proof,
                }
                for item in partition_manifest.relations
            ],
            "mixed_paths_for_qualification": list(classification.mixed_paths_for_qualification),
            "provenance_label": spec.provenance_label,
            "source_commits": list(reconstruction.source_commits),
            "commit_mapping": [
                {"source": source, "integrated": integrated, "delta_sha256": digest}
                for source, integrated, digest in reconstruction.commit_mapping
            ],
            "implementation_started_at": implementation_started.isoformat().replace(
                "+00:00", "Z"
            ),
            "implementation_timestamp_limit": (
                "Git author and committer timestamps are repository assertions, not "
                "independent wall-clock attestations"
            ),
            "license_baseline_object": baseline_license_blob,
            "license_reference_object": reference_license_blob,
        }
        proof_ref = self.store.put_bytes(
            _canonical(proof), "source-inspection-log", Visibility.PRIVATE
        )
        recorded_at = spec.recorded_at
        evidence = (
            EvidenceRecord(
                producer="feature_rl.intake.GitHubPullRequestIntake",
                command=(
                    "GitHubPullRequestIntake.ingest",
                    spec.repository_url,
                    str(pr_data["number"]),
                ),
                recorded_at=recorded_at,
                exit_status=0,
                artifacts=(proof_ref,),
                revision=self.factory_revision,
                scope="source_inspection",
            ),
        )
        relationship = CommitRelationship(
            integration=reconstruction.integration,
            target_before=reconstruction.baseline_commit,
            integrated_after=reconstruction.reference_commit,
            implementation_commits=reconstruction.implementation_commits,
            parents=reconstruction.parents,
            evidence=evidence,
        )
        screening_disposition = Disposition.SUCCESS
        screening_reason = (
            "Source history and licensing were reconstructed; mixed changes require automated permitted-source qualification"
            if classification.mixed_paths_for_qualification
            else "Source history and licensing were reconstructed"
        )
        inputs = tuple(item.content for item in snapshots)
        candidate = CandidateRecord(
            kind="CandidateRecord",
            schema_version=2,
            provenance_label=spec.provenance_label,
            visibility=Visibility.PRIVATE,
            provenance=Provenance(
                producer="feature_rl.intake.GitHubPullRequestIntake",
                producer_version="1",
                created_at=recorded_at,
                inputs=inputs,
                evidence=evidence,
            ),
            costs=(
                _unknown_cost(
                    "discovery", "Source acquisition costs were not measured; not zero"
                ),
            ),
            repository_url=spec.repository_url,
            repository_family=spec.repository_family,
            request_lineage=spec.request_lineage,
            partition=partition,
            sources=tuple(snapshots),
            license=LicenseRecord(
                spdx_id=spdx_id,
                license_text=license_snapshot.content,
                status="verified",
                evidence=evidence,
            ),
            commits=relationship,
            screening=ScreeningDecision(
                disposition=screening_disposition,
                reason=screening_reason,
                evidence=evidence,
            ),
        )
        candidate_ref = self.store.put_artifact(candidate)

        source_pair = SourcePair(
            kind="SourcePair",
            schema_version=2,
            provenance_label=spec.provenance_label,
            visibility=Visibility.PRIVATE,
            provenance=Provenance(
                producer="feature_rl.history.GitHistory",
                producer_version="1",
                created_at=recorded_at,
                inputs=(candidate_ref, baseline_ref, reference_ref),
                evidence=evidence,
            ),
            costs=(
                _unknown_cost(
                    "construction", "History reconstruction costs were not measured; not zero"
                ),
            ),
            candidate=candidate_ref,
            baseline_commit=reconstruction.baseline_commit,
            reference_commit=reconstruction.reference_commit,
            baseline=baseline_ref,
            reference=reference_ref,
            relationship=relationship,
            changed_files=classification.changed_files,
            admissible_cutoff=spec.admissible_cutoff,
            verification=evidence,
        )
        source_pair_ref = self.store.put_artifact(source_pair)

        cutoff_comments = []
        for comment in comments_data:
            created = _parse_time(comment["created_at"])
            updated = _parse_time(comment["updated_at"])
            if updated < created or updated > spec.recorded_at:
                raise ValueError("comment chronology is contradictory")
            if created <= spec.admissible_cutoff and updated <= spec.admissible_cutoff:
                cutoff_comments.append(
                    {
                        "id": comment["id"],
                        "body": comment["body"],
                        "created_at": comment["created_at"],
                        "updated_at": comment["updated_at"],
                    }
                )
        request_payload = {
            "provenance_label": spec.provenance_label,
            "admissible_cutoff": spec.admissible_cutoff.isoformat().replace(
                "+00:00", "Z"
            ),
            "issue": {
                "url": loaded[spec.issue_name].url,
                "title": issue_data["title"],
                "body": issue_data["body"],
                "created_at": issue_data["created_at"],
                "retrieved_at": loaded[spec.issue_name]
                .retrieved_at.isoformat()
                .replace("+00:00", "Z"),
                "source_response_sha256": snapshots[
                    spec.source_names.index(spec.issue_name)
                ].content.sha256,
                "edit_history": "unavailable",
            },
            "comments": cutoff_comments,
            "caveat": (
                "Current request text has no recoverable preimplementation body revision; "
                "later contract evidence must retain reconstructed-specification provenance."
                if spec.provenance_label == "reconstructed_specification"
                else "Archived issue response was captured before implementation began."
            ),
        }
        request_ref = self.store.put_bytes(
            _canonical(request_payload), "authoring-request", Visibility.AUTHORING
        )
        return PullRequestIntakeResult(
            candidate=candidate_ref,
            source_pair=source_pair_ref,
            authoring=AuthoringSourceView(
                request_evidence=request_ref,
                baseline=baseline_ref,
                license_text=license_snapshot.content,
            ),
            reference=reference_ref,
            mixed_paths_for_qualification=classification.mixed_paths_for_qualification,
            provenance_label=spec.provenance_label,
        )

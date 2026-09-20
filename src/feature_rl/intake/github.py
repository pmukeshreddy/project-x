"""Connected GitHub PR intake over verified response and Git object caches."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import json
import hashlib
import re
from typing import Literal, Mapping
from urllib.parse import urlsplit

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

from .sources import CachedSourceCatalog, SourceArchiver


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


def _response_paths(files_data):
    """Git's no-renames inventory includes both sides of a GitHub rename."""
    from feature_rl.environments.archive import safe_path
    paths = []
    for item in files_data:
        names = [item["filename"]]
        if item.get("status") == "renamed":
            names.append(item["previous_filename"])
        for name in names:
            if safe_path(name) != name:
                raise ValueError("changed-file paths must be canonical repository paths")
            paths.append(name)
    return tuple(dict.fromkeys(paths))


def _validate_subjects(repository_url, pr, issue, pr_url, issue_url=None, timeline=(), *, source_commits=()):
    """Bind cached subjects to this repository and prove any optional issue link."""
    slug = repository_url.removeprefix("https://github.com/")
    api = "https://api.github.com/repos/" + slug
    number = pr.get("number")
    if (type(number) is not int or number < 1
            or urlsplit(pr_url)._replace(query="", fragment="").geturl().lower()
            != (api + "/pulls/" + str(number)).lower()
            or pr.get("merged") is False or pr.get("state", "closed") != "closed"
            or not pr.get("merged_at")
            or pr.get("base", {}).get("repo", {}).get("full_name", slug).lower() != slug.lower()):
        raise ValueError("a merged PR from the configured repository is required")
    if issue is None:
        return
    issue_number = issue.get("number")
    expected = api + "/issues/" + str(issue_number)
    if (type(issue_number) is not int or issue_number < 1 or "pull_request" in issue
            or issue_number == number or issue_url is None
            or urlsplit(issue_url)._replace(query="", fragment="").geturl().lower() != expected.lower()
            or issue.get("repository_url", api).lower() != api.lower()):
        raise ValueError("the optional linked issue must be an issue in the PR repository")

    def mentions(text, target, kind):
        text = text or ""
        return bool(re.search(r"(?<![\w/#])#" + str(target) + r"\b", text)
                    or re.search(r"(?<![\w/])" + re.escape(slug) + r"#" + str(target) + r"\b", text, re.I)
                    or re.search(re.escape(repository_url) + "/" + kind + "/" + str(target) + r"\b", text, re.I))

    linked = mentions(pr.get("body"), issue_number, "issues") or mentions(issue.get("body"), number, "pull")
    linked_commits = {*source_commits, pr.get("merge_commit_sha")}
    if any(not isinstance(revision, str) or not _REVISION.fullmatch(revision) for revision in linked_commits):
        raise ValueError("issue linkage requires full PR commit object IDs")
    for event in timeline:
        source = (event.get("source") or {}).get("issue") or {}
        pull = source.get("pull_request") or {}
        if (event.get("event") == "cross-referenced"
                and (pull.get("url", "").lower() == (api + "/pulls/" + str(number)).lower()
                     or source.get("html_url", "").lower() == (repository_url + "/pull/" + str(number)).lower())):
            linked = True
        if event.get("event") in {"referenced", "closed"} and event.get("commit_id") in linked_commits:
            linked = True
    if not linked:
        raise ValueError("the optional issue has no archived link to the selected PR")


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
    commits_name: str
    files_name: str
    license_name: str
    integration: Literal["merge", "squash", "rebase", "linear"]
    admissible_cutoff: datetime
    recorded_at: datetime
    provenance_label: Literal["historical_request", "reconstructed_specification"]
    mixed_paths: Mapping[str, str]
    max_tree_archive_bytes: int
    issue_name: str | None = None
    comments_name: str | None = None
    pr_comments_name: str | None = None
    reviews_name: str | None = None
    review_comments_name: str | None = None
    issue_timeline_name: str | None = None
    license_path: str = 'LICENSE.txt'
    additional_pages: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self):
        from feature_rl.environments.archive import safe_path
        if safe_path(self.license_path) != self.license_path:
            raise ValueError('canonical repository license path required')
        names = (self.pr_name, self.issue_name, self.comments_name, self.commits_name,
                 self.files_name, self.license_name, self.pr_comments_name,
                 self.reviews_name, self.review_comments_name, self.issue_timeline_name)
        required = {name for name in names if name is not None}
        if len(required) != sum(name is not None for name in names) or any(not name for name in required):
            raise ValueError("response roles must have distinct nonempty source names")
        if self.issue_name is None and (self.comments_name or self.issue_timeline_name):
            raise ValueError("issue comments and timeline require an optional linked issue")
        if not required.issubset(self.source_names):
            raise ValueError("source_names omits a required PR intake response")
        if len(set(self.source_names)) != len(self.source_names):
            raise ValueError("source_names must be unique")
        arrays = {self.comments_name, self.commits_name, self.files_name,
                  self.pr_comments_name, self.reviews_name, self.review_comments_name,
                  self.issue_timeline_name} - {None}
        if set(self.additional_pages).difference(arrays):
            raise ValueError("only array responses can have additional response pages")
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
        immutable = {spec.commits_name, spec.files_name, spec.license_name}
        immutable.update(page for name in tuple(immutable) for page in spec.additional_pages.get(name, ()))
        loaded = {
            name: self.catalog.load(
                name,
                edit_history=(
                    "unavailable"
                    if name not in immutable
                    else "not_applicable"
                ),
                media_type="application/json",
            )
            for name in spec.source_names
        }
        pr_data = _json(loaded[spec.pr_name].body)
        issue_data = None if spec.issue_name is None else _json(loaded[spec.issue_name].body)
        if not isinstance(pr_data, dict) or (issue_data is not None and not isinstance(issue_data, dict)):
            raise ValueError("PR and optional issue sources must be JSON objects")
        for name, value in ((spec.pr_name, pr_data), (spec.issue_name, issue_data)):
            if name is not None:
                loaded[name] = replace(loaded[name], published_at=_parse_time(value["created_at"]),
                                       edited_at=_parse_time(value["updated_at"]))
        license_text = self.catalog.load(
            spec.license_text_name,
            edit_history="not_applicable",
            media_type="text/plain",
        )
        return loaded, license_text, pr_data, issue_data

    @staticmethod
    def _validate_endpoints(spec, loaded, pr_data, issue_data):
        api = 'https://api.github.com/repos/' + spec.repository_url.removeprefix('https://github.com/')
        pr_url = api + '/pulls/' + str(pr_data['number'])
        endpoints = {spec.commits_name: pr_url+'/commits', spec.files_name: pr_url+'/files'}
        for name, url in ((spec.pr_comments_name, api+'/issues/'+str(pr_data['number'])+'/comments'),
                          (spec.reviews_name, pr_url+'/reviews'),
                          (spec.review_comments_name, pr_url+'/comments')):
            if name is not None:
                endpoints[name] = url
        if issue_data is not None:
            for name, suffix in ((spec.comments_name, '/comments'), (spec.issue_timeline_name, '/timeline')):
                if name is not None:
                    endpoints[name] = api+'/issues/'+str(issue_data['number'])+suffix
        for name, endpoint in endpoints.items():
            for page in (name, *spec.additional_pages.get(name, ())):
                parsed = urlsplit(loaded[page].url)
                if parsed._replace(query='', fragment='').geturl().lower() != endpoint.lower():
                    raise ValueError('response page does not belong to the selected PR or linked issue')

    @staticmethod
    def _array(loaded, spec, name):
        if name is None:
            return []
        values = []
        for page in (name, *spec.additional_pages.get(name, ())):
            value = _json(loaded[page].body)
            if not isinstance(value, list):
                raise ValueError("paginated source body must be a JSON array")
            values.extend(value)
        key = {spec.commits_name: 'sha', spec.files_name: 'filename'}.get(name, 'id')
        # Some timeline event types have no numeric ID; their canonical body is the identity.
        identities = [item.get(key, _canonical(item).decode()) if name == spec.issue_timeline_name
                      else item[key] for item in values]
        if len(identities) != len(set(identities)):
            raise ValueError("source pages contain duplicate identities")
        return values

    @staticmethod
    def _request_payload(spec, loaded, pr_data, issue_data, classification, source_objects, reconstruction):
        """Project traceable text without claiming late captures are historical intent."""
        historical = spec.provenance_label == "historical_request"

        def provenance(name):
            source = loaded[name]
            return {"source_url": source.url,
                    "source_response_sha256": hashlib.sha256(source.body).hexdigest(),
                    "retrieved_at": source.retrieved_at.isoformat().replace("+00:00", "Z"),
                    "edit_history": source.edit_history}

        def admissible(item, name, *, review=False):
            source = loaded[name]
            created_value = item.get("submitted_at") if review else item.get("created_at")
            if created_value is None:
                if review and item.get("state") == "PENDING":
                    return not historical
                raise ValueError("request evidence lacks its publication timestamp")
            created = _parse_time(created_value)
            updated = _parse_time(item.get("updated_at") or created_value)
            if updated < created or updated > source.retrieved_at or source.retrieved_at > spec.recorded_at:
                raise ValueError("request evidence chronology is contradictory")
            return not historical or updated <= source.retrieved_at <= spec.admissible_cutoff

        def subject(item, name):
            if name is None or not admissible(item, name):
                return None
            return {key: item[key] for key in (
                "number", "html_url", "title", "body", "state", "created_at", "updated_at",
                "merged_at", "merge_commit_sha", "user", "author_association", "labels") if key in item} | provenance(name)

        def discussion(name, *, review=False):
            if name is None:
                return []
            projected = []
            for page in (name, *spec.additional_pages.get(name, ())):
                for item in _json(loaded[page].body):
                    if admissible(item, page, review=review):
                        projected.append({key: item[key] for key in (
                            "id", "html_url", "body", "user", "author_association", "state",
                            "created_at", "updated_at", "submitted_at", "commit_id",
                            "original_commit_id", "pull_request_review_id", "in_reply_to_id",
                            "path", "line", "original_line", "start_line", "side", "start_side",
                            "position", "original_position") if key in item} | provenance(page))
            return projected

        request = {
            "provenance_label": spec.provenance_label,
            "admissible_cutoff": spec.admissible_cutoff.isoformat().replace("+00:00", "Z"),
            "pull_request": subject(pr_data, spec.pr_name),
            "issue": subject(issue_data, spec.issue_name),
            "comments": discussion(spec.comments_name),
            "pr_comments": discussion(spec.pr_comments_name),
            "reviews": discussion(spec.reviews_name, review=True),
            "review_comments": discussion(spec.review_comments_name),
            "caveat": (
                "Reconstructed specification from captured PR, optional linked issue, discussion, "
                "review and commit metadata, including evidence after the cutoff. Current bodies "
                "have no recoverable edit history and are not claimed as preimplementation intent. "
                "Raw responses and implementation history remain archived privately."
                if not historical else
                "Request prose is limited to evidence archived by the preimplementation cutoff. "
                "Creation timestamps alone do not prove the historical body revision. The separately "
                "labelled changed-file inventory was captured after implementation and only identifies "
                "paths for feature-file selection; it is not historical request evidence."
            ),
        }
        if historical:
            if not any(request[key] for key in (
                    "pull_request", "issue", "comments", "pr_comments", "reviews", "review_comments")):
                raise ValueError("historical_request requires archived preimplementation request evidence")

        request["changed_files"] = []
        file_metadata = {}
        for page in (spec.files_name, *spec.additional_pages.get(spec.files_name, ())):
            for item in _json(loaded[page].body):
                file_metadata[item["filename"]] = (item, page, False)
                if item.get("status") == "renamed":
                    file_metadata.setdefault(item["previous_filename"], (item, page, True))
        for classified in classification.changed_files:
            item, page, old_side = file_metadata[classified.path]
            projected = {key: item[key] for key in (
                "status", "previous_filename", "additions", "deletions", "changes") if key in item}
            if old_side:
                projected.update(status="removed", renamed_to=item["filename"])
            metadata = {"path": classified.path, "category": classified.category,
                        "evidence_scope": "postimplementation_file_inventory"}
            if not historical:
                metadata["rationale"] = classified.rationale
            request["changed_files"].append(projected | metadata | provenance(page))
        if historical:
            return request
        request["commits"] = []
        commit_objects = {item.revision: item for item in source_objects}
        for page in (spec.commits_name, *spec.additional_pages.get(spec.commits_name, ())):
            for item in _json(loaded[page].body):
                commit = commit_objects[item["sha"]]
                request["commits"].append({
                    "sha": item["sha"], "message": item.get("commit", {}).get("message"),
                    "parents": list(commit.parents),
                    "authored_at": commit.authored_at.isoformat().replace("+00:00", "Z"),
                    "committed_at": commit.committed_at.isoformat().replace("+00:00", "Z"),
                    **provenance(page)})
        request["history"] = {"baseline_commit": reconstruction.baseline_commit,
                              "reference_commit": reconstruction.reference_commit,
                              "patch_sha256": reconstruction.patch_sha256,
                              "integration": reconstruction.integration}
        return request

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
        for name in (spec.comments_name, spec.pr_comments_name, spec.reviews_name, spec.review_comments_name):
            self._array(loaded, spec, name)
        timeline = self._array(loaded, spec, spec.issue_timeline_name)
        _validate_subjects(spec.repository_url, pr_data, issue_data, loaded[spec.pr_name].url,
                           None if spec.issue_name is None else loaded[spec.issue_name].url, timeline,
                           source_commits=tuple(item["sha"] for item in commits_data))
        self._validate_endpoints(spec, loaded, pr_data, issue_data)
        license_data = _json(loaded[spec.license_name].body)
        reconstruction, source_commits = _reconstruct_pull_request(
            self.history, spec.integration, pr_data, commits_data)

        merged_at = _parse_time(pr_data["merged_at"])
        if (spec.admissible_cutoff > merged_at or merged_at > loaded[spec.pr_name].retrieved_at
                or _parse_time(pr_data["created_at"]) > merged_at):
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
        response_paths = _response_paths(files_data)
        graph_paths = self.history.changed_paths(
            reconstruction.baseline_commit, reconstruction.reference_commit
        )
        if set(response_paths) != set(graph_paths) or len(response_paths) != len(graph_paths):
            raise ValueError("Git graph and PR changed-file response disagree")
        classification = classify_changed_files(
            graph_paths, mixed_paths=spec.mixed_paths
        )
        request_payload = self._request_payload(
            spec, loaded, pr_data, issue_data, classification, source_objects, reconstruction)

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

"""Pinned external-row ingestion into actual M1 artifacts and M6 source screening."""
from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import Field, model_validator

from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.pipeline import Factory
from feature_rl.verifiers.language import decode_json
from feature_rl.verifiers.loader import read_bytes, read_local

from .models import AdaptationFunnel, AdaptationStage


DATASET_ID = "TuringEnterprises/SWE-Bench-plus-plus"
DATASET_REVISION = "da364537055b9bb5091783af78a02b6a3bc0e130"
HARNESS_REVISION = "f938edd189049806fef7a76fdf01f0da55baa565"


class ExternalCorpusRow(c.StrictModel):
    repo: c.Text
    instance_id: c.Identifier
    base_commit: c.Revision
    created_at: c.Text
    language: c.Identifier
    task_type: c.Identifier
    repo_type: c.Identifier
    difficulty: c.Text
    problem_statement: c.Text
    patch: c.Text
    test_patch: c.Text
    FAIL_TO_PASS: c.Text
    PASS_TO_PASS: c.Text
    environment_config: c.Text


class ExternalOriginMapping(c.StrictModel):
    version: Literal["m8-external-origin-v1"]
    row: c.ArtifactRef
    row_payload_sha256: c.Digest
    dataset_repository: c.Text
    canonical_origin_url: c.Text
    request_locator: c.Text
    candidate: c.ArtifactRef
    source_pair: c.ArtifactRef
    authoring_request: c.ArtifactRef
    authoring_baseline: c.ArtifactRef
    authoring_license: c.ArtifactRef
    reference_commit: c.Revision
    normalized_request_sha256: c.Digest
    patch_sha256: c.Digest
    test_patch_sha256: c.Digest
    native_case_ids_sha256: c.Digest
    environment_config_sha256: c.Digest
    changed_paths: Annotated[tuple[c.Text, ...], Field(min_length=1)]
    intended_use: Literal["noncommercial_research", "academic", "educational", "unresolved"]
    evidence: c.Evidence


class ExternalSourceAssignment(c.StrictModel):
    instance_id: c.Identifier
    row: c.ArtifactRef
    candidate: c.ArtifactRef
    repository_family: c.Identifier
    request_lineage: Annotated[tuple[c.Identifier, ...], Field(min_length=1)]
    local_partition: Literal[c.Partition.TRAIN, c.Partition.DEVELOPMENT]
    normalized_request_sha256: c.Digest
    patch_sha256: c.Digest
    test_patch_sha256: c.Digest
    baseline_tree_sha256: c.Digest
    environment_config_sha256: c.Digest
    relation_evidence: c.ArtifactRef


class ExternalCorpusFrame(c.StrictModel):
    version: Literal["m8-external-source-frame-v1"]
    dataset_id: Literal[DATASET_ID]
    release_revision: Literal[DATASET_REVISION]
    upstream_split: Literal["test"]
    assignments: Annotated[tuple[ExternalSourceAssignment, ...], Field(min_length=1)]
    exclusions: c.ArtifactRef
    created_at: c.UTCDateTime

    @model_validator(mode="after")
    def uniqueness(self):
        if len({item.instance_id for item in self.assignments}) != len(self.assignments):
            raise ValueError("duplicate external instance assignment")
        if len({item.row.sha256 for item in self.assignments}) != len(self.assignments):
            raise ValueError("duplicate external row assignment")
        return self


class ExternalAdaptationConfig(c.StrictModel):
    version: Literal["m8-external-adaptation-v1"]
    dataset_id: Literal[DATASET_ID]
    release_revision: Literal[DATASET_REVISION]
    harness_revision: Literal[HARNESS_REVISION]
    upstream_config: Literal["default"]
    upstream_split: Literal["test"]
    local_partition: Literal[c.Partition.TRAIN, c.Partition.DEVELOPMENT]
    dataset_license: c.Text
    intended_use: Literal["noncommercial_research", "academic", "educational", "unresolved"]
    rows: Annotated[tuple[c.ArtifactRef, ...], Field(min_length=1)]
    origin_mappings: Annotated[tuple[c.ArtifactRef, ...], Field(min_length=1)]
    source_frame: c.ArtifactRef
    supported_languages: Annotated[tuple[c.Identifier, ...], Field(min_length=1)]
    supported_task_types: Annotated[tuple[c.Identifier, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def aligned_inputs(self):
        if len(self.rows) != len(self.origin_mappings):
            raise ValueError("every external row requires one authenticated origin mapping")
        if len(set(self.rows)) != len(self.rows) or len(set(self.origin_mappings)) != len(self.origin_mappings):
            raise ValueError("duplicate external row or origin mapping")
        return self


class AdaptationItem(c.StrictModel):
    row: c.ArtifactRef
    origin_mapping: c.ArtifactRef
    instance_id: c.Text
    candidate: c.ArtifactRef | None
    source_pair: c.ArtifactRef | None
    source_only_allowlist: tuple[c.ArtifactRef, ...]
    source_result: tuple[c.ArtifactRef, ...]
    disposition: c.Disposition
    reason: c.Text
    costs: c.Costs
    required_next_gates: tuple[c.Text, ...]

    @model_validator(mode="after")
    def source_only(self):
        if any(ref.visibility not in {c.Visibility.PUBLIC, c.Visibility.AUTHORING} for ref in self.source_only_allowlist):
            raise ValueError("adaptation solver allowlist contains restricted material")
        return self


class AdaptationBatch(c.StrictModel):
    version: Literal["m8-external-adaptation-batch-v1"]
    configuration: c.ArtifactRef
    source_frame: c.ArtifactRef
    items: Annotated[tuple[AdaptationItem, ...], Field(min_length=1)]
    funnel: AdaptationFunnel

    @model_validator(mode="after")
    def accounts_for_rows(self):
        if self.funnel.stages[0].entered != len(self.items):
            raise ValueError("adaptation batch and funnel row counts differ")
        return self


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized_request(value: str) -> str:
    return _digest(" ".join(value.split()))


def _unknown_cost(note: str) -> tuple[c.CostRecord, ...]:
    return (c.CostRecord(
        category="construction", wall_seconds=None, cpu_seconds=None, gpu_seconds=None,
        input_tokens=None, output_tokens=None, human_minutes=None, usd=None,
        measurement="unknown", note=note,
    ),)


class ExternalCorpusAdapter:
    """Validate supplied private rows; never fetch data or interpret native tests as rewards."""

    def __init__(
        self, *, store: ArtifactStore, factory: Factory,
        configuration: c.ArtifactRef, revision: str,
    ):
        if not isinstance(store, ArtifactStore) or not isinstance(factory, Factory) or factory.store is not store:
            raise TypeError("external adaptation requires the actual M6 Factory and controller store")
        if type(revision) is not str or len(revision) not in (40, 64) or any(ch not in "0123456789abcdef" for ch in revision):
            raise ValueError("implementation revision required")
        self.store, self.factory, self.registry, self.revision = store, factory, factory.registry, revision
        self.configuration_ref = c.ArtifactRef.model_validate(configuration)
        self.configuration = read_local(
            store, self.configuration_ref, ExternalAdaptationConfig,
            "m8-external-adaptation-configuration",
        )
        self.frame = read_local(
            store, self.configuration.source_frame, ExternalCorpusFrame,
            "m8-external-source-frame",
        )
        if (
            self.frame.dataset_id != self.configuration.dataset_id
            or self.frame.release_revision != self.configuration.release_revision
            or any(item.local_partition != self.configuration.local_partition for item in self.frame.assignments)
        ):
            raise ValueError("external frame differs from the frozen adaptation configuration")
        for reference in self.configuration.origin_mappings:
            mapping = read_local(store, reference, ExternalOriginMapping, "m8-external-origin")
            dependencies = tuple(dict.fromkeys((
                mapping.row, mapping.candidate, mapping.source_pair,
                mapping.authoring_request, mapping.authoring_baseline,
                mapping.authoring_license, *_refs(mapping.evidence),
            )))
            self.registry.register(reference, dependencies=dependencies)
        self.registry.register(
            self.configuration.source_frame,
            dependencies=tuple(dict.fromkeys((self.frame.exclusions, *(item.row for item in self.frame.assignments),
                *(item.candidate for item in self.frame.assignments), *(item.relation_evidence for item in self.frame.assignments)))),
        )
        self.registry.register(
            self.configuration_ref,
            dependencies=(self.configuration.source_frame, *self.configuration.rows, *self.configuration.origin_mappings),
        )

    def _row(self, reference):
        raw = read_bytes(self.store, reference, 8 * 1024 * 1024, "m8-external-row", True)
        return raw, ExternalCorpusRow.model_validate(decode_json(raw, 8 * 1024 * 1024))

    def _validated(self, row_ref, raw, row, mapping_ref, mapping, assignment):
        candidate = self.store.get_artifact(mapping.candidate)
        pair = self.store.get_artifact(mapping.source_pair)
        if type(candidate) is not c.CandidateRecord or type(pair) is not c.SourcePair:
            raise ValueError("origin mapping must reference actual M1 CandidateRecord and SourcePair")
        if mapping.row != row_ref or hashlib.sha256(raw).hexdigest() != mapping.row_payload_sha256:
            raise ValueError("external row bytes differ from the authenticated origin mapping")
        expected = (
            mapping.dataset_repository == row.repo,
            mapping.canonical_origin_url == candidate.repository_url,
            pair.candidate == mapping.candidate,
            pair.baseline_commit == row.base_commit,
            pair.reference_commit == mapping.reference_commit,
            mapping.normalized_request_sha256 == _normalized_request(row.problem_statement),
            mapping.patch_sha256 == _digest(row.patch),
            mapping.test_patch_sha256 == _digest(row.test_patch),
            mapping.native_case_ids_sha256 == _digest(row.FAIL_TO_PASS + "\0" + row.PASS_TO_PASS),
            mapping.environment_config_sha256 == _digest(row.environment_config),
            tuple(sorted(mapping.changed_paths)) == tuple(sorted(item.path for item in pair.changed_files)),
            mapping.evidence == candidate.screening.evidence,
            candidate.license.status == "verified",
            candidate.partition == self.configuration.local_partition,
            mapping.intended_use == self.configuration.intended_use,
        )
        if not all(expected):
            raise ValueError("external row, M1 provenance, rights, or privileged digests do not join exactly")
        if self.configuration.intended_use == "unresolved":
            raise ValueError("dataset intended use remains unresolved")
        if any(ref.visibility not in {c.Visibility.PUBLIC, c.Visibility.AUTHORING} for ref in (
            mapping.authoring_request, mapping.authoring_baseline, mapping.authoring_license,
        )):
            raise ValueError("source-only allowlist contains a restricted artifact")
        if (
            assignment.instance_id != row.instance_id or assignment.row != row_ref
            or assignment.candidate != mapping.candidate
            or assignment.repository_family != candidate.repository_family
            or assignment.request_lineage != candidate.request_lineage
            or assignment.local_partition != candidate.partition
            or assignment.normalized_request_sha256 != mapping.normalized_request_sha256
            or assignment.patch_sha256 != mapping.patch_sha256
            or assignment.test_patch_sha256 != mapping.test_patch_sha256
            or assignment.baseline_tree_sha256 != pair.baseline.sha256
            or assignment.environment_config_sha256 != mapping.environment_config_sha256
        ):
            raise ValueError("external row differs from its pre-authoring family/dedup assignment")
        return candidate, pair

    def adapt(self) -> tuple[c.ArtifactRef, AdaptationBatch]:
        assignments = {item.row: item for item in self.frame.assignments}
        if set(assignments) != set(self.configuration.rows):
            raise ValueError("source frame must assign every configured row exactly once")
        items = []
        metadata_accepted = origin_accepted = 0
        metadata_invalid = origin_invalid = origin_rejected = 0
        screening_accepted = screening_rejected = screening_invalid = 0
        incurred = []
        next_gates = (
            "actual M2/M3/M4 construction with fresh workers and external observations",
            "M5 qualification and selected M6 release",
            "actual M7 collection and matched-budget training",
        )
        for row_ref, mapping_ref in zip(self.configuration.rows, self.configuration.origin_mappings):
            instance_id = "unreadable-" + row_ref.sha256[:16]
            try:
                raw, row = self._row(row_ref)
                instance_id = row.instance_id
                metadata_accepted += 1
            except Exception as exc:
                metadata_invalid += 1
                costs = _unknown_cost("external row schema validation failed before source screening")
                items.append(AdaptationItem(
                    row=row_ref, origin_mapping=mapping_ref, instance_id=instance_id,
                    candidate=None, source_pair=None, source_only_allowlist=(), source_result=(),
                    disposition=c.Disposition.INVALID, reason=(type(exc).__name__ + ": " + str(exc))[:2048], costs=costs,
                    required_next_gates=next_gates,
                ))
                incurred.extend(costs)
                continue
            try:
                mapping = read_local(self.store, mapping_ref, ExternalOriginMapping, "m8-external-origin")
                assignment = assignments[row_ref]
                candidate, pair = self._validated(row_ref, raw, row, mapping_ref, mapping, assignment)
                if row.language not in self.configuration.supported_languages or row.task_type not in self.configuration.supported_task_types:
                    raise NotImplementedError("row is outside the frozen language/task-type allowlist")
                origin_accepted += 1
            except NotImplementedError as exc:
                origin_rejected += 1
                costs = _unknown_cost("external row excluded by the frozen capability allowlist")
                items.append(AdaptationItem(
                    row=row_ref, origin_mapping=mapping_ref, instance_id=instance_id,
                    candidate=None, source_pair=None, source_only_allowlist=(), source_result=(),
                    disposition=c.Disposition.UNSUPPORTED, reason=str(exc), costs=costs,
                    required_next_gates=next_gates,
                ))
                incurred.extend(costs)
                continue
            except Exception as exc:
                origin_invalid += 1
                costs = _unknown_cost("external origin mapping validation failed before source screening")
                items.append(AdaptationItem(
                    row=row_ref, origin_mapping=mapping_ref, instance_id=instance_id,
                    candidate=None, source_pair=None, source_only_allowlist=(), source_result=(),
                    disposition=c.Disposition.INVALID, reason=(type(exc).__name__ + ": " + str(exc))[:2048], costs=costs,
                    required_next_gates=next_gates,
                ))
                incurred.extend(costs)
                continue
            result = self.factory.screen_source(mapping.candidate)
            incurred.extend(result.costs)
            if result.disposition == c.Disposition.SUCCESS:
                screening_accepted += 1
            elif result.disposition in {c.Disposition.REJECTED, c.Disposition.UNSUPPORTED}:
                screening_rejected += 1
            else:
                screening_invalid += 1
            items.append(AdaptationItem(
                row=row_ref, origin_mapping=mapping_ref, instance_id=instance_id,
                candidate=mapping.candidate, source_pair=mapping.source_pair,
                source_only_allowlist=(mapping.authoring_request, mapping.authoring_baseline, mapping.authoring_license),
                source_result=result.artifacts, disposition=result.disposition,
                reason=result.reason, costs=result.costs,
                required_next_gates=next_gates,
            ))
        stages = (
            AdaptationStage(
                name="metadata", entered=len(self.configuration.rows), accepted=metadata_accepted,
                rejected=0, invalid=metadata_invalid,
                reasons=() if not metadata_invalid else ("row schema or bounded JSON invalid",),
            ),
            AdaptationStage(
                name="origin", entered=metadata_accepted, accepted=origin_accepted,
                rejected=origin_rejected, invalid=origin_invalid,
                reasons=() if not origin_rejected and not origin_invalid else ("capability exclusion or origin/rights/dedup mismatch",),
            ),
            AdaptationStage(
                name="source-screen", entered=origin_accepted, accepted=screening_accepted,
                rejected=screening_rejected, invalid=screening_invalid,
                reasons=() if not screening_rejected and not screening_invalid else ("actual M6 source disposition",),
            ),
        )
        disposition = c.Disposition.SUCCESS if screening_accepted == len(items) else c.Disposition.PROVISIONAL
        funnel = AdaptationFunnel(
            version="m8-adaptation-funnel-v1", corpus_id=self.configuration.dataset_id,
            release_revision=self.configuration.release_revision, upstream_split=self.configuration.upstream_split,
            local_partition=self.configuration.local_partition,
            license_constraint=self.configuration.dataset_license,
            source_frame=self.configuration.source_frame, stages=stages,
            selection_bias=(
                "Only frozen supported languages and task types enter M6 source screening",
                "Upstream test label is retained; local assignment is excluded from locked evaluation",
            ),
            costs=tuple(incurred) or _unknown_cost("no external adaptation cost observations"),
            disposition=disposition,
        )
        batch = AdaptationBatch(
            version="m8-external-adaptation-batch-v1", configuration=self.configuration_ref,
            source_frame=self.configuration.source_frame, items=tuple(items), funnel=funnel,
        )
        reference = self.store.put_bytes(
            canonical_json(batch.model_dump(mode="json")), "m8-external-adaptation-batch", c.Visibility.PRIVATE,
        )
        dependencies = tuple(dict.fromkeys((self.configuration_ref, self.configuration.source_frame,
            *self.configuration.rows, *self.configuration.origin_mappings,
            *(ref for item in items for ref in item.source_result))))
        self.registry.register(reference, dependencies=dependencies)
        return reference, batch


def _refs(evidence: tuple[c.EvidenceRecord, ...]) -> tuple[c.ArtifactRef, ...]:
    return tuple(dict.fromkeys(ref for item in evidence for ref in item.artifacts))

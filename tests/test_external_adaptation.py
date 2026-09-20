from datetime import datetime, timezone
import hashlib

from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.registry import Registry
from m5_fixtures import task_fixture


NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def records(tmp_path, *, bad_patch_digest=False):
    from feature_rl.evaluation import (
        ExternalAdaptationConfig, ExternalCorpusFrame, ExternalCorpusRow,
        ExternalOriginMapping, ExternalSourceAssignment,
    )

    store = ArtifactStore(tmp_path / "objects", c.ActorRole.CONTROLLER)
    registry = Registry(tmp_path / "registry", store)
    task = store.get_artifact(task_fixture(store))
    pair = store.get_artifact(task.source_pair)
    candidate = store.get_artifact(pair.candidate)
    row = ExternalCorpusRow(
        repo="example/synthetic", instance_id="synthetic-1",
        base_commit=pair.baseline_commit, created_at="2026-01-01T00:00:00Z",
        language="python", task_type="feature", repo_type="library", difficulty="unknown",
        problem_statement="Add the synthetic requested behavior.",
        patch="PRIVATE SOLUTION PATCH", test_patch="PRIVATE TEST PATCH",
        FAIL_TO_PASS='["test_feature"]', PASS_TO_PASS='["test_existing"]',
        environment_config='{"untrusted":"hint"}',
    )
    raw = canonical_json(row.model_dump(mode="json"))
    row_ref = store.put_bytes(raw, "m8-external-row", c.Visibility.PRIVATE)
    changed = tuple(sorted(item.path for item in pair.changed_files))
    mapping = ExternalOriginMapping(
        version="m8-external-origin-v1", row=row_ref,
        row_payload_sha256=hashlib.sha256(raw).hexdigest(),
        dataset_repository=row.repo, canonical_origin_url=candidate.repository_url,
        request_locator="https://example.invalid/synthetic/pull/1",
        candidate=pair.candidate, source_pair=task.source_pair,
        authoring_request=task.solver_view.instruction,
        authoring_baseline=pair.baseline, authoring_license=candidate.license.license_text,
        reference_commit=pair.reference_commit,
        normalized_request_sha256=digest(" ".join(row.problem_statement.split())),
        patch_sha256="0" * 64 if bad_patch_digest else digest(row.patch),
        test_patch_sha256=digest(row.test_patch),
        native_case_ids_sha256=digest(row.FAIL_TO_PASS + "\0" + row.PASS_TO_PASS),
        environment_config_sha256=digest(row.environment_config),
        changed_paths=changed, intended_use="noncommercial_research",
        evidence=candidate.screening.evidence,
    )
    mapping_ref = store.put_bytes(canonical_json(mapping.model_dump(mode="json")), "m8-external-origin", c.Visibility.PRIVATE)
    assignment = ExternalSourceAssignment(
        instance_id=row.instance_id, row=row_ref, candidate=pair.candidate,
        repository_family=candidate.repository_family, request_lineage=candidate.request_lineage,
        local_partition=candidate.partition,
        normalized_request_sha256=mapping.normalized_request_sha256,
        patch_sha256=mapping.patch_sha256, test_patch_sha256=mapping.test_patch_sha256,
        baseline_tree_sha256=pair.baseline.sha256,
        environment_config_sha256=mapping.environment_config_sha256,
        relation_evidence=candidate.screening.evidence[0].artifacts[0],
    )
    exclusions = store.put_bytes(b"synthetic external exclusions", "m8-external-exclusions", c.Visibility.PRIVATE)
    frame = ExternalCorpusFrame(
        version="m8-external-source-frame-v1",
        dataset_id="TuringEnterprises/SWE-Bench-plus-plus",
        release_revision="da364537055b9bb5091783af78a02b6a3bc0e130",
        upstream_split="test", assignments=(assignment,), exclusions=exclusions, created_at=NOW,
    )
    frame_ref = store.put_bytes(canonical_json(frame.model_dump(mode="json")), "m8-external-source-frame", c.Visibility.PRIVATE)
    config = ExternalAdaptationConfig(
        version="m8-external-adaptation-v1",
        dataset_id="TuringEnterprises/SWE-Bench-plus-plus",
        release_revision="da364537055b9bb5091783af78a02b6a3bc0e130",
        harness_revision="f938edd189049806fef7a76fdf01f0da55baa565",
        upstream_config="default", upstream_split="test",
        local_partition=candidate.partition,
        dataset_license="non-commercial research, academic, or educational use only",
        intended_use="noncommercial_research", rows=(row_ref,),
        origin_mappings=(mapping_ref,), source_frame=frame_ref,
        supported_languages=("python",), supported_task_types=("feature",),
    )
    config_ref = store.put_bytes(canonical_json(config.model_dump(mode="json")), "m8-external-adaptation-configuration", c.Visibility.PRIVATE)
    return store, registry, config_ref, row


def test_external_adapter_uses_actual_m1_artifacts_and_m6_screen_without_exposing_privileged_row(tmp_path):
    from feature_rl.evaluation import ExternalCorpusAdapter
    from feature_rl.pipeline import Factory

    store, registry, config_ref, row = records(tmp_path)
    factory = Factory(store=store, registry=registry, revision="e" * 40)
    adapter = ExternalCorpusAdapter(store=store, factory=factory, configuration=config_ref, revision="f" * 40)
    batch_ref, batch = adapter.adapt()
    item = batch.items[0]
    assert item.disposition == c.Disposition.SUCCESS
    assert all(ref.visibility in {c.Visibility.PUBLIC, c.Visibility.AUTHORING} for ref in item.source_only_allowlist)
    assert [stage.accepted for stage in batch.funnel.stages] == [1, 1, 1]
    published = store.get_bytes(batch_ref)
    assert row.patch.encode() not in published and row.test_patch.encode() not in published
    assert batch_ref in registry.trace(item.candidate).artifacts


def test_external_adapter_rejects_digest_drift_before_m6_source_screening(tmp_path):
    from feature_rl.evaluation import ExternalCorpusAdapter
    from feature_rl.pipeline import Factory

    store, registry, config_ref, _ = records(tmp_path, bad_patch_digest=True)
    factory = Factory(store=store, registry=registry, revision="e" * 40)
    _, batch = ExternalCorpusAdapter(
        store=store, factory=factory, configuration=config_ref, revision="f" * 40,
    ).adapt()
    assert batch.items[0].disposition == c.Disposition.INVALID
    assert [(stage.entered, stage.accepted, stage.invalid) for stage in batch.funnel.stages] == [
        (1, 1, 0), (1, 0, 1), (0, 0, 0),
    ]
    assert not any(job for job in registry.trace(batch.items[0].origin_mapping).jobs)

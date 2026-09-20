from datetime import datetime, timezone
import hashlib
import json

from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.registry import Registry
from m5_fixtures import task_fixture


NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def records(tmp_path, *, bad_patch_digest=False, bad_relation=False, locked_overlap=False,
            listed_train_overlap=False, with_build_inputs=False, bad_build_pair=False):
    from feature_rl.evaluation import (
        ExternalAdaptationConfig, ExternalCorpusFrame, ExternalCorpusRow,
        ExternalOriginMapping, ExternalSourceAssignment, FrozenRoster,
        SourceAssignment,
    )
    from feature_rl.environments import SandboxPolicy, SourceArchive
    from feature_rl.environments import PreparedEnvironment
    from feature_rl.pipeline import BuildInputs

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
    baseline = SourceArchive.read(store.get_bytes(pair.baseline), SandboxPolicy())
    baseline_tree = "a" * 40
    reference_tree = "b" * 40
    proof = {
        "baseline_commit": pair.baseline_commit,
        "reference_commit": pair.reference_commit,
        "baseline_tree": baseline_tree,
        "reference_tree": reference_tree,
        "source_tree": reference_tree,
        "patch_sha256": digest(row.patch),
        "source_commits": list(candidate.commits.implementation_commits),
        "commit_mapping": [],
        "partition_assignments": [{"source_id": "synthetic-1", "partition": candidate.partition.value, "component_id": "synthetic"}],
        "relations": [],
    }
    proof_ref = store.put_bytes(canonical_json(proof), "source-inspection-log", c.Visibility.PRIVATE)
    evidence = (c.EvidenceRecord(
        producer="feature_rl.intake.GitHubPullRequestIntake",
        command=("diagnostic-external-origin",), recorded_at=NOW, exit_status=0,
        artifacts=(proof_ref,), revision="1" * 40, scope="source_inspection",
    ),)
    candidate = candidate.model_copy(update={
        "provenance": candidate.provenance.model_copy(update={"evidence": evidence}),
        "license": candidate.license.model_copy(update={"evidence": evidence}),
        "commits": candidate.commits.model_copy(update={"evidence": evidence}),
        "screening": candidate.screening.model_copy(update={"evidence": evidence}),
    })
    candidate_ref = store.put_artifact(candidate)
    pair = pair.model_copy(update={
        "candidate": candidate_ref,
        "provenance": pair.provenance.model_copy(update={
            "inputs": (candidate_ref, pair.baseline, pair.reference), "evidence": evidence,
        }),
        "relationship": pair.relationship.model_copy(update={"evidence": evidence}),
        "verification": evidence,
    })
    pair_ref = store.put_artifact(pair)
    request_locator = "https://example.invalid/synthetic/pull/1"
    request_ref = store.put_bytes(canonical_json({
        "provenance_label": candidate.provenance_label,
        "admissible_cutoff": pair.admissible_cutoff.isoformat().replace("+00:00", "Z"),
        "issue": {"url": request_locator, "title": "Synthetic request",
                  "body": row.problem_statement,
                  "source_response_sha256": candidate.sources[0].content.sha256},
        "comments": [], "caveat": "diagnostic exact request binding",
    }), "authoring-request", c.Visibility.AUTHORING)
    changed = tuple(sorted(item.path for item in pair.changed_files))
    mapping = ExternalOriginMapping(
        version="m8-external-origin-v1", row=row_ref,
        row_payload_sha256=hashlib.sha256(raw).hexdigest(),
        dataset_repository=row.repo, canonical_origin_url=candidate.repository_url,
        request_locator=request_locator,
        candidate=candidate_ref, source_pair=pair_ref,
        authoring_request=request_ref,
        authoring_baseline=pair.baseline, authoring_license=candidate.license.license_text,
        reference_commit=pair.reference_commit,
        normalized_request_sha256=digest(" ".join(row.problem_statement.split())),
        patch_sha256="0" * 64 if bad_patch_digest else digest(row.patch),
        test_patch_sha256=digest(row.test_patch),
        native_case_ids_sha256=digest(row.FAIL_TO_PASS + "\0" + row.PASS_TO_PASS),
        environment_config_sha256=digest(row.environment_config),
        changed_paths=changed, intended_use="noncommercial_research",
        evidence=evidence,
    )
    mapping_ref = store.put_bytes(canonical_json(mapping.model_dump(mode="json")), "m8-external-origin", c.Visibility.PRIVATE)
    assignment = ExternalSourceAssignment(
        instance_id=row.instance_id, row=row_ref, candidate=candidate_ref,
        repository_family=candidate.repository_family, request_lineage=candidate.request_lineage,
        local_partition=candidate.partition,
        normalized_request_sha256=mapping.normalized_request_sha256,
        patch_sha256=mapping.patch_sha256, test_patch_sha256=mapping.test_patch_sha256,
        baseline_tree_id=baseline_tree,
        environment_config_sha256=mapping.environment_config_sha256,
        relation_evidence=(row_ref if bad_relation else proof_ref),
    )
    locked_task = store.put_artifact(task.model_copy(update={
        "partition": c.Partition.LOCKED_TEST,
        "repository_family": candidate.repository_family if locked_overlap else "unrelated-family",
        "request_lineage": candidate.request_lineage if locked_overlap else ("unrelated-request",),
    }))
    exclusion_sources = [SourceAssignment(
        source_id="locked-control", task=locked_task,
        repository_family=candidate.repository_family if locked_overlap else "unrelated-family",
        request_lineage=candidate.request_lineage if locked_overlap else ("unrelated-request",),
        partition=c.Partition.LOCKED_TEST, evidence=proof_ref,
    )]
    if listed_train_overlap:
        exclusion_sources.append(SourceAssignment(
            source_id="listed-external-train", task=task_fixture(store),
            repository_family=candidate.repository_family,
            request_lineage=candidate.request_lineage,
            partition=c.Partition.TRAIN, evidence=proof_ref,
        ))
    exclusions = FrozenRoster(
        version="m8-frozen-roster-v1", locked_tasks=(locked_task,),
        sources=tuple(exclusion_sources), relations=(), test_source_frame=proof_ref,
        exclusions=proof_ref, created_at=NOW,
    )
    exclusions = store.put_bytes(canonical_json(exclusions.model_dump(mode="json")), "m8-frozen-roster", c.Visibility.PRIVATE)
    frame = ExternalCorpusFrame(
        version="m8-external-source-frame-v1",
        dataset_id="TuringEnterprises/SWE-Bench-plus-plus",
        release_revision="da364537055b9bb5091783af78a02b6a3bc0e130",
        upstream_split="test", assignments=(assignment,), exclusions=exclusions, created_at=NOW,
    )
    frame_ref = store.put_bytes(canonical_json(frame.model_dump(mode="json")), "m8-external-source-frame", c.Visibility.PRIVATE)
    build_inputs = None
    if with_build_inputs:
        sandbox = store.put_bytes(
            canonical_json(SandboxPolicy().model_dump(mode="json")),
            "sandbox-policy", c.Visibility.PRIVATE,
        )
        verifier = store.get_artifact(task.private_oracle)
        build_inputs = BuildInputs(
            source_pair=(task.source_pair if bad_build_pair else pair_ref), contract=task.contract,
            scenario_plan=verifier.scenario_plan, verifier=task.private_oracle,
            environment=PreparedEnvironment(recipe=task.environment, policy=sandbox),
            baseline_files=tuple(sorted(baseline.files)), invocation="external-diagnostic-build",
        )
    config = ExternalAdaptationConfig(
        version="m8-external-adaptation-v1",
        dataset_id="TuringEnterprises/SWE-Bench-plus-plus",
        release_revision="da364537055b9bb5091783af78a02b6a3bc0e130",
        harness_revision="f938edd189049806fef7a76fdf01f0da55baa565",
        upstream_config="default", upstream_split="test",
        local_partition=candidate.partition,
        dataset_license="non-commercial research, academic, or educational use only",
        intended_use="noncommercial_research", rows=(row_ref,),
        origin_mappings=(mapping_ref,), construction_inputs=(build_inputs,), source_frame=frame_ref,
        supported_languages=("python",), supported_task_types=("feature",),
    )
    config_ref = store.put_bytes(canonical_json(config.model_dump(mode="json")), "m8-external-adaptation-configuration", c.Visibility.PRIVATE)
    return store, registry, config_ref, row


def test_external_adapter_delegates_eligible_source_to_actual_factory_construct(tmp_path):
    from feature_rl.evaluation import ExternalCorpusAdapter
    from feature_rl.pipeline import Factory

    store, registry, config_ref, row = records(tmp_path)
    factory = Factory(store=store, registry=registry, revision="e" * 40)
    adapter = ExternalCorpusAdapter(store=store, factory=factory, configuration=config_ref, revision="f" * 40)
    batch_ref, batch = adapter.adapt()
    item = batch.items[0]
    assert item.disposition == c.Disposition.BLOCKED
    assert all(ref.visibility in {c.Visibility.PUBLIC, c.Visibility.AUTHORING} for ref in item.source_only_allowlist)
    assert [stage.accepted for stage in batch.funnel.stages] == [1, 1, 1, 0]
    assert len(item.source_result) == 1 and item.source_result[0].kind == "m6-source-disposition"
    assert len(item.construction_result) == 1 and item.construction_result[0].kind == "m6-construction-result"
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
        (1, 1, 0), (1, 0, 1), (0, 0, 0), (0, 0, 0),
    ]
    assert not any(job for job in registry.trace(batch.items[0].origin_mapping).jobs)


def test_external_adapter_rejects_family_or_lineage_overlap_with_frozen_locked_roster(tmp_path):
    from feature_rl.evaluation import ExternalCorpusAdapter
    from feature_rl.pipeline import Factory

    store, registry, config_ref, _ = records(tmp_path, locked_overlap=True)
    _, batch = ExternalCorpusAdapter(
        store=store, factory=Factory(store=store, registry=registry, revision="e" * 40),
        configuration=config_ref, revision="f" * 40,
    ).adapt()
    assert batch.items[0].disposition == c.Disposition.INVALID
    assert "locked evaluation" in batch.items[0].reason
    assert not any(job for job in registry.trace(batch.items[0].origin_mapping).jobs)


def test_external_adapter_does_not_treat_listed_train_source_as_locked_exclusion(tmp_path):
    from feature_rl.evaluation import ExternalCorpusAdapter
    from feature_rl.pipeline import Factory

    store, registry, config_ref, _ = records(tmp_path, listed_train_overlap=True)
    _, batch = ExternalCorpusAdapter(
        store=store, factory=Factory(store=store, registry=registry, revision="e" * 40),
        configuration=config_ref, revision="f" * 40,
    ).adapt()
    assert batch.items[0].disposition == c.Disposition.BLOCKED


def test_external_adapter_rejects_unbound_relation_evidence_before_factory(tmp_path):
    from feature_rl.evaluation import ExternalCorpusAdapter
    from feature_rl.pipeline import Factory

    store, registry, config_ref, _ = records(tmp_path, bad_relation=True)
    _, batch = ExternalCorpusAdapter(
        store=store, factory=Factory(store=store, registry=registry, revision="e" * 40),
        configuration=config_ref, revision="f" * 40,
    ).adapt()
    assert batch.items[0].disposition == c.Disposition.INVALID
    assert not any(job for job in registry.trace(batch.items[0].origin_mapping).jobs)


def test_external_adapter_passes_supplied_real_build_inputs_to_factory_construct(tmp_path):
    from feature_rl.evaluation import ExternalCorpusAdapter
    from feature_rl.pipeline import Factory
    from feature_rl.pipeline.construction import ConstructionResult
    from feature_rl.verifiers.loader import read_local

    store, registry, config_ref, _ = records(tmp_path, with_build_inputs=True)
    _, batch = ExternalCorpusAdapter(
        store=store, factory=Factory(store=store, registry=registry, revision="e" * 40),
        configuration=config_ref, revision="f" * 40,
    ).adapt()
    item = batch.items[0]
    assert item.construction_result
    assert "BuildInputs are missing" not in item.reason
    assert any(registry.job(job_id).spec.invocation == "m6-construct"
               for job_id in registry.trace(item.candidate).jobs)
    receipt_ref = next(ref for ref in item.construction_result
                       if ref.kind == "m6-construction-result")
    receipt = read_local(store, receipt_ref, ConstructionResult, "m6-construction-result")
    child = registry.job(receipt.build_job)
    assert child.state == "completed" and child.result == receipt.build_result
    child_notes = {cost.note for cost in child.result.costs}
    assert child_notes <= {cost.note for cost in batch.funnel.costs}
    assert child_notes <= {cost.note for cost in item.costs}


def test_external_adapter_rejects_build_inputs_for_another_source_pair(tmp_path):
    from feature_rl.evaluation import ExternalCorpusAdapter
    from feature_rl.pipeline import Factory

    store, registry, config_ref, _ = records(
        tmp_path, with_build_inputs=True, bad_build_pair=True,
    )
    _, batch = ExternalCorpusAdapter(
        store=store, factory=Factory(store=store, registry=registry, revision="e" * 40),
        configuration=config_ref, revision="f" * 40,
    ).adapt()
    assert batch.items[0].disposition == c.Disposition.INVALID
    assert "BuildInputs" in batch.items[0].reason
    assert not any(job for job in registry.trace(batch.items[0].origin_mapping).jobs)


def test_external_adapter_accepts_authentic_local_git_m1_tree_ids(tmp_path):
    from feature_rl.evaluation import (
        ExternalAdaptationConfig, ExternalCorpusAdapter, ExternalCorpusFrame,
        ExternalCorpusRow, ExternalOriginMapping, ExternalSourceAssignment,
        FrozenRoster, SourceAssignment,
    )
    from feature_rl.environments import SandboxPolicy, SourceArchive
    from feature_rl.history import GitHistory
    from feature_rl.intake import (
        CachedSourceCatalog, GitHubPullRequestIntake, PullRequestIntakeSpec,
    )
    from feature_rl.pipeline import Factory
    from feature_rl.splits import SplitPlanner
    from feature_rl.verifiers.language import decode_json
    from test_click_intake import commit, git, write_cache

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--initial-branch=main")
    (repo / "LICENSE.txt").write_text("BSD fixture\n")
    (repo / "src").mkdir()
    (repo / "src/core.py").write_text("OLD = True\n")
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text("name: ci\n")
    baseline_commit = commit(repo, "baseline")
    license_blob = git(repo, "rev-parse", f"{baseline_commit}:LICENSE.txt")
    git(repo, "switch", "-c", "feature")
    (repo / "src/core.py").write_text("FEATURE = True\n")
    (repo / "tests").mkdir()
    (repo / "tests/test_feature.py").write_text("# feature check\n")
    source_head = commit(repo, "feature")
    git(repo, "switch", "main")
    git(repo, "merge", "--squash", "feature")
    integrated = commit(repo, "integrate feature")
    history = GitHistory(repo / ".git")
    patch = history._run_bytes(
        "diff", "--no-ext-diff", "--no-textconv", "--no-renames",
        "--diff-algorithm=myers", "--binary", "--full-index",
        "--src-prefix=a/", "--dst-prefix=b/", baseline_commit, integrated, "--",
    ).decode()
    issue_url = "https://api.github.com/repos/example/project/issues/1"
    sources = {
        "pr": ("https://api.github.com/repos/example/project/pulls/1", json.dumps({
            "number": 1, "title": "Add feature", "body": "Implementation PR",
            "created_at": "2026-02-01T00:00:00Z", "updated_at": "2026-02-25T00:00:00Z",
            "merged_at": "2026-02-25T00:00:00Z", "merge_commit_sha": integrated,
            "base": {"sha": baseline_commit}, "head": {"sha": source_head},
        }).encode()),
        "issue": (issue_url, json.dumps({
            "number": 1, "title": "Feature request", "body": "Add the requested feature.",
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        }).encode()),
        "comments": (issue_url + "/comments", b"[]"),
        "commits": ("https://api.github.com/repos/example/project/pulls/1/commits",
                    json.dumps([{"sha": source_head}]).encode()),
        "files": ("https://api.github.com/repos/example/project/pulls/1/files",
                  json.dumps([{"filename": "src/core.py"}, {"filename": "tests/test_feature.py"}]).encode()),
        "license": ("https://api.github.com/repos/example/project/license",
                    json.dumps({"sha": license_blob, "license": {"spdx_id": "BSD-3-Clause"}}).encode()),
        "license-text": ("https://raw.githubusercontent.com/example/project/main/LICENSE.txt", b"BSD fixture\n"),
    }
    manifest = write_cache(tmp_path / "responses", sources)
    store = ArtifactStore(tmp_path / "objects", c.ActorRole.CONTROLLER)
    registry = Registry(tmp_path / "registry", store)
    intake = GitHubPullRequestIntake(
        store=store,
        catalog=CachedSourceCatalog(tmp_path / "responses", manifest, max_bytes=1_000_000),
        history=history,
        factory_revision="1" * 40,
    )
    spec = PullRequestIntakeSpec(
        repository_url="https://github.com/example/project",
        repository_family="example-project", request_lineage=("pr-1",),
        partition_source_ids=("pr-1",),
        source_names=("pr", "issue", "comments", "commits", "files", "license"),
        license_text_name="license-text", pr_name="pr", issue_name="issue",
        comments_name="comments", commits_name="commits", files_name="files",
        license_name="license", integration="squash",
        admissible_cutoff=datetime(2026, 2, 2, tzinfo=timezone.utc),
        recorded_at=NOW, provenance_label="reconstructed_specification",
        mixed_paths={}, max_tree_archive_bytes=1_000_000,
    )
    result = intake.ingest(spec, SplitPlanner(()).assign({"pr-1": c.Partition.TRAIN}))
    candidate = store.get_artifact(result.candidate)
    pair = store.get_artifact(result.source_pair)
    proof_ref = candidate.screening.evidence[0].artifacts[0]
    proof = decode_json(store.get_bytes(proof_ref), 4 * 1024 * 1024)
    assert proof["baseline_tree"] == git(repo, "rev-parse", f"{baseline_commit}^{{tree}}")
    assert proof["reference_tree"] == git(repo, "rev-parse", f"{integrated}^{{tree}}")
    archive_tree = SourceArchive.read(store.get_bytes(pair.baseline), SandboxPolicy()).tree_sha256
    assert proof["baseline_tree"] != archive_tree
    assert proof["patch_sha256"] == digest(patch)

    row = ExternalCorpusRow(
        repo="example/project", instance_id="local-git-1", base_commit=baseline_commit,
        created_at="2026-01-01T00:00:00Z", language="python", task_type="feature",
        repo_type="library", difficulty="unknown", problem_statement="Add the requested feature.",
        patch=patch, test_patch="PRIVATE TEST PATCH", FAIL_TO_PASS='["test_feature"]',
        PASS_TO_PASS='[]', environment_config='{"untrusted":"hint"}',
    )
    raw = canonical_json(row.model_dump(mode="json"))
    row_ref = store.put_bytes(raw, "m8-external-row", c.Visibility.PRIVATE)
    mapping = ExternalOriginMapping(
        version="m8-external-origin-v1", row=row_ref,
        row_payload_sha256=hashlib.sha256(raw).hexdigest(), dataset_repository=row.repo,
        canonical_origin_url=candidate.repository_url, request_locator=issue_url,
        candidate=result.candidate, source_pair=result.source_pair,
        authoring_request=result.authoring.request_evidence,
        authoring_baseline=result.authoring.baseline,
        authoring_license=result.authoring.license_text,
        reference_commit=pair.reference_commit,
        normalized_request_sha256=digest(" ".join(row.problem_statement.split())),
        patch_sha256=digest(row.patch), test_patch_sha256=digest(row.test_patch),
        native_case_ids_sha256=digest(row.FAIL_TO_PASS + "\0" + row.PASS_TO_PASS),
        environment_config_sha256=digest(row.environment_config),
        changed_paths=tuple(sorted(item.path for item in pair.changed_files)),
        intended_use="noncommercial_research", evidence=candidate.screening.evidence,
    )
    mapping_ref = store.put_bytes(canonical_json(mapping.model_dump(mode="json")), "m8-external-origin", c.Visibility.PRIVATE)
    assignment = ExternalSourceAssignment(
        instance_id=row.instance_id, row=row_ref, candidate=result.candidate,
        repository_family=candidate.repository_family, request_lineage=candidate.request_lineage,
        local_partition=candidate.partition,
        normalized_request_sha256=mapping.normalized_request_sha256,
        patch_sha256=mapping.patch_sha256, test_patch_sha256=mapping.test_patch_sha256,
        baseline_tree_id=proof["baseline_tree"],
        environment_config_sha256=mapping.environment_config_sha256,
        relation_evidence=proof_ref,
    )
    locked_task_ref = task_fixture(store)
    locked_task = store.get_artifact(locked_task_ref).model_copy(update={
        "partition": c.Partition.LOCKED_TEST, "repository_family": "unrelated-family",
        "request_lineage": ("unrelated-request",),
    })
    locked_task_ref = store.put_artifact(locked_task)
    locked = FrozenRoster(
        version="m8-frozen-roster-v1", locked_tasks=(locked_task_ref,),
        sources=(SourceAssignment(
            source_id="locked-control", task=locked_task_ref,
            repository_family=locked_task.repository_family,
            request_lineage=locked_task.request_lineage,
            partition=c.Partition.LOCKED_TEST, evidence=proof_ref,
        ),), relations=(), test_source_frame=proof_ref, exclusions=proof_ref, created_at=NOW,
    )
    locked_ref = store.put_bytes(canonical_json(locked.model_dump(mode="json")), "m8-frozen-roster", c.Visibility.PRIVATE)
    frame = ExternalCorpusFrame(
        version="m8-external-source-frame-v1",
        dataset_id="TuringEnterprises/SWE-Bench-plus-plus",
        release_revision="da364537055b9bb5091783af78a02b6a3bc0e130",
        upstream_split="test", assignments=(assignment,), exclusions=locked_ref, created_at=NOW,
    )
    frame_ref = store.put_bytes(canonical_json(frame.model_dump(mode="json")), "m8-external-source-frame", c.Visibility.PRIVATE)
    config = ExternalAdaptationConfig(
        version="m8-external-adaptation-v1",
        dataset_id="TuringEnterprises/SWE-Bench-plus-plus",
        release_revision="da364537055b9bb5091783af78a02b6a3bc0e130",
        harness_revision="f938edd189049806fef7a76fdf01f0da55baa565",
        upstream_config="default", upstream_split="test", local_partition=c.Partition.TRAIN,
        dataset_license="non-commercial research, academic, or educational use only",
        intended_use="noncommercial_research", rows=(row_ref,), origin_mappings=(mapping_ref,),
        construction_inputs=(None,), source_frame=frame_ref,
        supported_languages=("python",), supported_task_types=("feature",),
    )
    config_ref = store.put_bytes(canonical_json(config.model_dump(mode="json")), "m8-external-adaptation-configuration", c.Visibility.PRIVATE)
    _, batch = ExternalCorpusAdapter(
        store=store, factory=Factory(store=store, registry=registry, revision="e" * 40),
        configuration=config_ref, revision="f" * 40,
    ).adapt()
    assert batch.items[0].disposition == c.Disposition.BLOCKED
    assert batch.funnel.stages[1].accepted == 1

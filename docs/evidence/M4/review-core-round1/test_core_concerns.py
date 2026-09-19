"""Review-only preflight diagnostics; no Docker, candidate execution, H or models."""
import json

from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.environments import EnvironmentRuntime, SandboxPolicy, SourceArchive
from feature_rl.grading import GradingService, read_grade
from feature_rl.verifiers import load_verifier
from m4_fixtures import diagnostic, replace_artifact


def test_task_resource_limits_below_fixed_runtime_must_reject(tmp_path):
    store = ArtifactStore(tmp_path / "store", c.ActorRole.CONTROLLER)
    task_ref = diagnostic(store)
    task = store.get_artifact(task_ref)
    contract = store.get_artifact(task.contract).model_dump(mode="json")
    declared = dict(memory_bytes=64 * 1024 * 1024, cpu_seconds=1.0, pids=8,
                    disk_bytes=8 * 1024 * 1024)
    contract["episode_limits"].update(declared)
    contract_ref = store.put_artifact(c.RequirementContract.model_validate_json(json.dumps(contract)))
    verifier = store.get_artifact(task.private_oracle)
    plan_ref = replace_artifact(store, verifier.scenario_plan, contract=contract_ref)
    verifier_ref = replace_artifact(store, task.private_oracle, contract=contract_ref, scenario_plan=plan_ref)
    task_ref = replace_artifact(store, task_ref, contract=contract_ref, private_oracle=verifier_ref)
    try:
        loaded = load_verifier(store, task_ref)
    except ValueError:
        return
    enforced = {name: getattr(loaded.recipe.limits, name) for name in declared}
    print("RESOURCE_GATE", json.dumps({"scope": "unit_diagnostic_load_only", "declared": declared,
          "recipe": enforced, "accepted": True}), flush=True)
    assert not any(enforced[name] > value for name, value in declared.items()), \
        "Unsupported resource envelope was admitted by M4"


def test_malformed_trusted_baseline_is_not_a_candidate_zero(tmp_path):
    store = ArtifactStore(tmp_path / "store", c.ActorRole.CONTROLLER)
    baseline = store.put_bytes(b"not an archive", "source-archive", c.Visibility.AUTHORING)
    task_ref = diagnostic(store, baseline=baseline)
    delta = store.put_bytes(SourceArchive({}).to_tar(), "m4-source-delta", c.Visibility.PRIVATE)
    submission = store.put_bytes(canonical_json({"version": "m4-submission-v1",
        "baseline": baseline.model_dump(mode="json"), "changes": delta.model_dump(mode="json"),
        "deletions": []}), "m4-submission", c.Visibility.PRIVATE)
    # Only the already supported preflight route is exercised. This diagnostic
    # object has no engine or callable worker methods replaced with fake results.
    runtime = object.__new__(EnvironmentRuntime)
    runtime.store = store
    runtime.policy = SandboxPolicy()
    result = GradingService(store=store, runtime=runtime, revision="a" * 40).grade(task_ref, submission, 11)
    receipt = read_grade(store, result.artifacts[0])
    print("BASELINE_CLASSIFICATION", json.dumps({"scope": "unit_diagnostic_preflight_only",
        "reward": receipt.reward, "disposition": receipt.disposition.value,
        "reason": receipt.reason, "case_statuses": [case.status for case in receipt.cases],
        "build_evidence": receipt.build_evidence}), flush=True)
    assert receipt.reward is None, "An invalid trusted baseline was attributed to this candidate"

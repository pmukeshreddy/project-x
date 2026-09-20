"""Validation of immutable evaluation rosters and preregistrations."""
from __future__ import annotations

import hashlib

from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.splits import Relation, SplitConflict, SplitPlanner

from .models import EvaluationPreregistration, FrozenRoster, TrialAssignment


class FrozenStudyError(ValueError):
    """The proposed run differs from its frozen study declaration."""


class LineageLeakage(FrozenStudyError):
    """Related sources cross a partition boundary."""


def _ref_key(ref: c.ArtifactRef) -> tuple[str, int, str]:
    return ref.sha256, ref.schema_version, ref.kind


def episode_sampling_seed(
    preregistration: EvaluationPreregistration,
    assignment: TrialAssignment,
) -> int:
    """Return the actual policy seed while retaining pass@1 seed semantics."""
    if preregistration.metric == "pass_at_1":
        return assignment.policy_seed
    payload = canonical_json((
        "m8-best-of-k-episode-seed-v1",
        preregistration.seeds.algorithm,
        assignment.policy_seed,
        _ref_key(assignment.task),
        assignment.episode_index,
    ))
    return int(hashlib.sha256(payload).hexdigest()[:15], 16)


def validate_lineage_freeze(roster: FrozenRoster) -> FrozenRoster:
    source_ids = {item.source_id for item in roster.sources}
    for relation in roster.relations:
        if relation.left not in source_ids or relation.right not in source_ids:
            raise FrozenStudyError("lineage relation references a source outside the frozen roster")

    requested = {item.source_id: item.partition for item in roster.sources}
    relations = list(
        Relation(item.left, item.right, item.kind, item.evidence.sha256)
        for item in roster.relations
    )
    # Repository-family and request-lineage equality are closure facts even when a
    # separately authored relation was omitted.
    for index, left in enumerate(roster.sources):
        for right in roster.sources[index + 1:]:
            if left.repository_family == right.repository_family:
                relations.append(Relation(left.source_id, right.source_id, "monorepo", "roster:repository_family"))
            if set(left.request_lineage) & set(right.request_lineage):
                relations.append(Relation(left.source_id, right.source_id, "same_request", "roster:request_lineage"))
    try:
        SplitPlanner(tuple(sorted(set(relations)))).assign(requested)
    except SplitConflict as exc:
        raise LineageLeakage(str(exc)) from exc

    expected_locked = {
        _ref_key(item.task) for item in roster.sources
        if item.partition == c.Partition.LOCKED_TEST
    }
    actual_locked = {_ref_key(item) for item in roster.locked_tasks}
    if actual_locked != expected_locked or len(actual_locked) != len(roster.locked_tasks):
        raise FrozenStudyError("locked task list must exactly match locked-test source assignments")
    return roster


def validate_preregistration(
    roster: FrozenRoster,
    preregistration: EvaluationPreregistration,
    config: c.EvaluationConfig,
) -> EvaluationPreregistration:
    validate_lineage_freeze(roster)
    if config.partition != c.Partition.LOCKED_TEST:
        raise FrozenStudyError("frozen evaluation must use the locked-test partition")
    if tuple(map(_ref_key, config.tasks)) != tuple(map(_ref_key, roster.locked_tasks)):
        raise FrozenStudyError("evaluation tasks differ from the frozen roster")

    protocols = {item.arm: item for item in preregistration.arms}
    configured = {item.arm: item for item in config.arms}
    if set(protocols) != {"base", "feature_grpo"} or set(configured) != set(protocols):
        raise FrozenStudyError("evaluation requires base and feature-env GRPO")
    expected_methods = {"base": "starting", "feature_grpo": "factory_rl"}
    if any(protocols[name].method != method for name, method in expected_methods.items()):
        raise FrozenStudyError("arm method differs from the frozen study")
    for name, arm in configured.items():
        protocol = protocols[name]
        if arm.policy != protocol.policy or arm.checkpoint != protocol.checkpoint or arm.training_config != protocol.training_config:
            raise FrozenStudyError(f"configured arm {name} differs from its preregistered protocol")

    initial = protocols["base"].initial_checkpoint
    if any(item.initial_checkpoint != initial for item in protocols.values()):
        raise FrozenStudyError("all arms must share the same initial checkpoint")
    if protocols["base"].checkpoint != initial or protocols["base"].training_config is not None or protocols["base"].training_budget is not None:
        raise FrozenStudyError("base arm must be the untrained starting checkpoint")
    if protocols["feature_grpo"].training_config is None or protocols["feature_grpo"].training_budget is None:
        raise FrozenStudyError("feature-env GRPO requires a frozen training configuration and budget")

    shared = ("tools", "action_format", "optimizer_family", "harness_version", "development_budget")
    if any(len({getattr(protocol, field) for protocol in protocols.values()}) != 1 for field in shared):
        raise FrozenStudyError("all arms must share tools, actions, optimizer, harness, and development budget")

    locked_values = (
        (preregistration.limits, config.limits, "resource limits"),
        (preregistration.seeds, config.seeds, "seed policy"),
        (preregistration.metric, config.metric, "metric"),
        (preregistration.episodes_per_trial, config.episodes_per_trial, "episode count"),
        (preregistration.harness_version, config.harness_version, "harness version"),
        (preregistration.checkpoint_selection_rule, config.checkpoint_selection_rule, "checkpoint rule"),
        (preregistration.invalid_trial_rule, config.invalid_trial_rule, "invalid-trial rule"),
    )
    for frozen, actual, label in locked_values:
        if frozen != actual:
            raise FrozenStudyError(f"configured {label} differs from preregistration")

    expected = {
        (_ref_key(task), arm, seed, episode)
        for task in roster.locked_tasks
        for arm in protocols
        for seed in preregistration.seeds.seeds
        for episode in range(preregistration.episodes_per_trial)
    }
    actual = {
        (_ref_key(trial.task), trial.arm, trial.policy_seed, trial.episode_index)
        for trial in preregistration.trials
    }
    if actual != expected or len(actual) != len(preregistration.trials):
        raise FrozenStudyError("assigned trial roster is not the complete frozen Cartesian product")

    if preregistration.metric == "best_of_k":
        cells = {
            (_ref_key(trial.task), trial.policy_seed, trial.episode_index): trial
            for trial in preregistration.trials
        }
        sampling_seeds = {
            key: episode_sampling_seed(preregistration, trial)
            for key, trial in cells.items()
        }
        if len(set(sampling_seeds.values())) != len(sampling_seeds):
            raise FrozenStudyError("best-of-k episode sampling seed collision")

    case_seeds: dict[tuple[tuple[str, int, str], int, int], set[int]] = {}
    for trial in preregistration.trials:
        key = (_ref_key(trial.task), trial.policy_seed, trial.episode_index)
        case_seeds.setdefault(key, set()).add(trial.case_seed)
    if any(len(values) != 1 for values in case_seeds.values()):
        raise FrozenStudyError("paired case seed must be identical across arms")
    if any(left == right or left not in protocols or right not in protocols for left, right in preregistration.comparisons):
        raise FrozenStudyError("comparison references invalid arms")
    return preregistration

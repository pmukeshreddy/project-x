from datetime import datetime, timezone

import pytest

from feature_rl import contracts as c


NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


def ref(kind="evidence", visibility=c.Visibility.PRIVATE, digest="a"):
    return c.ArtifactRef(
        sha256=digest * 64,
        kind=kind,
        schema_version=1,
        visibility=visibility,
        encoding="json" if kind in c.ARTIFACT_TYPES else "bytes",
    )


def limits(wall=30.0):
    return c.ResourceLimits(
        wall_seconds=wall,
        cpu_seconds=20.0,
        memory_bytes=1024,
        pids=4,
        output_bytes=1024,
        disk_bytes=2048,
        tool_calls=8,
        input_tokens=100,
        output_tokens=100,
    )


def policy(arm, checkpoint):
    return c.PolicyConfig(
        identity=c.ModelIdentity(
            provider="local",
            model="student",
            revision="model-r1",
            weights=checkpoint,
            tokenizer_digest="f" * 64,
        ),
        policy_version=f"policy-{arm.lower()}",
        temperature=0.0,
        top_p=1.0,
        seed=7,
        system_prompt=ref("system-prompt", c.Visibility.PUBLIC, "b"),
        harness_version="runner-v1",
        require_token_probabilities=False,
    )


def study_objects():
    from feature_rl.evaluation import (
        ArmProtocol,
        BudgetLimit,
        EvaluationPreregistration,
        FrozenRoster,
        LineageRelation,
        SourceAssignment,
        TrialAssignment,
    )

    task_one = ref("TaskBundle", c.Visibility.EVALUATION, "1")
    task_two = ref("TaskBundle", c.Visibility.EVALUATION, "2")
    train_task = ref("TaskBundle", c.Visibility.PRIVATE, "3")
    roster = FrozenRoster(
        version="m8-frozen-roster-v1",
        locked_tasks=(task_one, task_two),
        sources=(
            SourceAssignment(
                source_id="locked-one",
                task=task_one,
                repository_family="family-one",
                request_lineage=("request-one",),
                partition=c.Partition.LOCKED_TEST,
                evidence=ref("lineage-proof", c.Visibility.PRIVATE, "4"),
            ),
            SourceAssignment(
                source_id="locked-two",
                task=task_two,
                repository_family="family-two",
                request_lineage=("request-two",),
                partition=c.Partition.LOCKED_TEST,
                evidence=ref("lineage-proof", c.Visibility.PRIVATE, "5"),
            ),
            SourceAssignment(
                source_id="train-one",
                task=train_task,
                repository_family="train-family",
                request_lineage=("train-request",),
                partition=c.Partition.TRAIN,
                evidence=ref("lineage-proof", c.Visibility.PRIVATE, "6"),
            ),
        ),
        relations=(),
        test_source_frame=ref("test-source-frame", c.Visibility.EVALUATION, "7"),
        exclusions=ref("test-source-exclusions", c.Visibility.EVALUATION, "8"),
        created_at=NOW,
    )
    initial = ref("checkpoint", c.Visibility.TRAINING, "9")
    checkpoints = {
        "A": initial,
        "B": ref("checkpoint", c.Visibility.TRAINING, "a"),
        "C": ref("checkpoint", c.Visibility.TRAINING, "b"),
        "D": ref("checkpoint", c.Visibility.TRAINING, "c"),
    }
    budget = BudgetLimit(max_updates=10, max_rollouts=40, max_assistant_tokens=4000, gpu_seconds=500.0, usd=None)
    arms = tuple(
        ArmProtocol(
            arm=arm,
            method={"A": "starting", "B": "sft", "C": "external_rl", "D": "factory_rl"}[arm],
            policy=policy(arm, checkpoint),
            checkpoint=checkpoint,
            training_config=None if arm == "A" else ref("training-config", c.Visibility.TRAINING, arm.lower()),
            initial_checkpoint=initial,
            tools=ref("tool-protocol", c.Visibility.PUBLIC, "d"),
            action_format="actions-v1",
            optimizer_family="adamw",
            harness_version="runner-v1",
            training_budget=None if arm == "A" else budget,
            development_budget=BudgetLimit(max_updates=2, max_rollouts=8, max_assistant_tokens=800, gpu_seconds=100.0, usd=None),
        )
        for arm, checkpoint in checkpoints.items()
    )
    assignments = tuple(
        TrialAssignment(
            trial_id=f"trial-{task.sha256[0]}-{arm}",
            task=task,
            arm=arm,
            policy_seed=11,
            case_seed=101 if task == task_one else 102,
            episode_index=0,
        )
        for task in (task_one, task_two)
        for arm in ("A", "B", "C", "D")
    )
    prereg = EvaluationPreregistration(
        version="m8-preregistration-v1",
        arms=arms,
        trials=assignments,
        limits=limits(),
        seeds=c.SeedPolicy(algorithm="m8-fixed-v1", seeds=(11,), same_cases_within_group=True),
        metric="pass_at_1",
        episodes_per_trial=1,
        harness_version="runner-v1",
        checkpoint_selection_rule="development-only fixed rule",
        invalid_trial_rule="report all assigned and valid-only",
        locked_test_access_rule="one final run after freeze",
        comparisons=(("A", "D"), ("B", "D"), ("C", "D")),
        created_at=NOW,
    )
    config = c.EvaluationConfig(
        tasks=(task_one, task_two),
        arms=tuple(
            c.EvaluationArm(
                arm=arm.arm,
                policy=arm.policy,
                checkpoint=arm.checkpoint,
                training_config=arm.training_config,
            )
            for arm in arms
        ),
        limits=limits(),
        seeds=c.SeedPolicy(algorithm="m8-fixed-v1", seeds=(11,), same_cases_within_group=True),
        partition=c.Partition.LOCKED_TEST,
        harness_version="runner-v1",
        checkpoint_selection_rule="development-only fixed rule",
        invalid_trial_rule="report all assigned and valid-only",
        metric="pass_at_1",
        episodes_per_trial=1,
        frozen_roster=ref("m8-frozen-roster", c.Visibility.EVALUATION, "e"),
        preregistration=ref("m8-preregistration", c.Visibility.EVALUATION, "f"),
    )
    return roster, prereg, config


def test_lineage_freeze_rejects_related_sources_crossing_train_and_locked_test():
    from feature_rl.evaluation import LineageLeakage, validate_lineage_freeze

    roster, _, _ = study_objects()
    leaked = roster.model_copy(
        update={
            "relations": (
                # A fork relation closes the two sources into one component.
                __import__("feature_rl.evaluation", fromlist=["LineageRelation"]).LineageRelation(
                    left="locked-one",
                    right="train-one",
                    kind="fork",
                    evidence=ref("lineage-proof", c.Visibility.PRIVATE, "0"),
                ),
            )
        }
    )
    with pytest.raises(LineageLeakage, match="conflicting partitions"):
        validate_lineage_freeze(leaked)


def test_preregistration_rejects_missing_trial_and_case_seed_drift_across_arms():
    from feature_rl.evaluation import FrozenStudyError, validate_preregistration

    roster, prereg, config = study_objects()
    with pytest.raises(FrozenStudyError, match="assigned trial roster"):
        validate_preregistration(roster, prereg.model_copy(update={"trials": prereg.trials[:-1]}), config)

    trials = list(prereg.trials)
    trials[1] = trials[1].model_copy(update={"case_seed": 999})
    with pytest.raises(FrozenStudyError, match="paired case seed"):
        validate_preregistration(roster, prereg.model_copy(update={"trials": tuple(trials)}), config)


def test_preregistration_rejects_c_d_budget_or_protocol_drift():
    from feature_rl.evaluation import FrozenStudyError, validate_preregistration

    roster, prereg, config = study_objects()
    arms = list(prereg.arms)
    arms[3] = arms[3].model_copy(
        update={
            "training_budget": arms[3].training_budget.model_copy(update={"max_rollouts": 39}),
            "action_format": "different-actions",
        }
    )
    with pytest.raises(FrozenStudyError, match="C and D"):
        validate_preregistration(roster, prereg.model_copy(update={"arms": tuple(arms)}), config)


def test_valid_preregistration_is_complete_and_paired():
    from feature_rl.evaluation import validate_preregistration

    roster, prereg, config = study_objects()
    validated = validate_preregistration(roster, prereg, config)
    assert len(validated.trials) == 8
    assert {item.arm for item in validated.arms} == {"A", "B", "C", "D"}


def test_adaptation_funnel_rejects_hidden_losses_and_accepts_accounted_stages():
    from feature_rl.evaluation import AdaptationFunnel, AdaptationStage

    common = dict(
        version="m8-adaptation-funnel-v1",
        corpus_id="TuringEnterprises.SWE-Bench-plus-plus",
        release_revision="d" * 40,
        upstream_split="test",
        local_partition=c.Partition.TRAIN,
        license_constraint="non-commercial research, academic, or educational use",
        source_frame=ref("external-source-frame", c.Visibility.PRIVATE, "1"),
        stages=(
            AdaptationStage(name="metadata", entered=500, accepted=500, rejected=0, invalid=0, reasons=()),
            AdaptationStage(name="reconstruction", entered=500, accepted=300, rejected=150, invalid=50, reasons=("history", "license")),
        ),
        selection_bias=("Python CLI/API support only",),
        costs=(
            c.CostRecord(
                category="construction",
                wall_seconds=None,
                cpu_seconds=None,
                gpu_seconds=None,
                input_tokens=None,
                output_tokens=None,
                human_minutes=None,
                usd=None,
                measurement="unknown",
                note="not incurred in metadata-only research",
            ),
        ),
        disposition=c.Disposition.PROVISIONAL,
    )
    funnel = AdaptationFunnel(**common)
    assert funnel.stages[-1].accepted == 300
    broken = common | {
        "stages": (
            common["stages"][0].model_copy(update={"accepted": 499}),
        )
    }
    with pytest.raises(ValueError, match="account"):
        AdaptationFunnel(**broken)

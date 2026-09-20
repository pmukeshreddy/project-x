from datetime import datetime, timezone

import pytest

from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from test_agent_runner import fixture as runner_fixture


NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)
REVISION = "8" * 40


def _opaque(store, kind, value):
    return store.put_bytes(canonical_json(value), kind, c.Visibility.EVALUATION)


def evaluation_inputs(tmp_path, monkeypatch):
    from feature_rl.agents.protocol import protocol_payload
    from feature_rl.evaluation import (
        ArmProtocol, BudgetLimit, EvaluationPreregistration, FrozenRoster,
        SourceAssignment, TrialAssignment,
    )

    context = runner_fixture(tmp_path, monkeypatch, outputs=("{\"action\":\"submit\"}",) * 4)
    task = context.store.get_artifact(context.task)
    lineage = _opaque(context.store, "lineage-proof", {"diagnostic": True})
    source_frame = _opaque(context.store, "test-source-frame", {"diagnostic": True})
    exclusions = _opaque(context.store, "test-source-exclusions", {"diagnostic": True})
    roster = FrozenRoster(
        version="m8-frozen-roster-v1", locked_tasks=(context.task,),
        sources=(SourceAssignment(
            source_id="locked-diagnostic", task=context.task,
            repository_family=task.repository_family,
            request_lineage=task.request_lineage,
            partition=c.Partition.LOCKED_TEST, evidence=lineage,
        ),), relations=(), test_source_frame=source_frame,
        exclusions=exclusions, created_at=NOW,
    )
    roster_ref = _opaque(context.store, "m8-frozen-roster", roster.model_dump(mode="json"))
    initial = _opaque(context.store, "checkpoint", {"arm": "initial"})
    tools = context.store.put_bytes(
        protocol_payload(), "m7-tool-protocol", c.Visibility.PUBLIC,
    )
    budget = BudgetLimit(max_updates=1, max_rollouts=4, max_assistant_tokens=100,
                         gpu_seconds=1.0, usd=None)
    arms = []
    assignments = []
    for arm, method in (("base", "starting"), ("feature_grpo", "factory_rl")):
        checkpoint = initial if arm == "base" else _opaque(context.store, "checkpoint", {"arm": arm})
        training = None if arm == "base" else _opaque(context.store, "training-config", {"arm": arm})
        policy = context.policy.model_copy(update={"policy_version": f"diagnostic-{arm}", "seed": 11})
        arms.append(ArmProtocol(
            arm=arm, method=method, policy=policy, checkpoint=checkpoint,
            training_config=training, initial_checkpoint=initial, tools=tools,
            action_format="actions-v1", optimizer_family="adamw",
            harness_version=context.policy.harness_version,
            training_budget=None if arm == "base" else budget,
            development_budget=budget,
        ))
        assignments.append(TrialAssignment(
            trial_id=f"trial-{arm}", task=context.task, arm=arm,
            policy_seed=11, case_seed=73, episode_index=0,
        ))
    prereg = EvaluationPreregistration(
        version="m8-preregistration-v1", arms=tuple(arms), trials=tuple(assignments),
        limits=context.limits,
        seeds=c.SeedPolicy(algorithm="m8-fixed-v1", seeds=(11,), same_cases_within_group=True),
        metric="pass_at_1", episodes_per_trial=1,
        harness_version=context.policy.harness_version,
        checkpoint_selection_rule="development-only fixed rule",
        invalid_trial_rule="report all assigned and valid-only",
        locked_test_access_rule="one final run after freeze",
        comparisons=(("base", "feature_grpo"),), created_at=NOW,
    )
    prereg_ref = _opaque(context.store, "m8-preregistration", prereg.model_dump(mode="json"))
    config = c.EvaluationConfig(
        tasks=(context.task,),
        arms=tuple(c.EvaluationArm(arm=item.arm, policy=item.policy,
                                   checkpoint=item.checkpoint,
                                   training_config=item.training_config) for item in arms),
        limits=context.limits,
        seeds=c.SeedPolicy(algorithm="m8-fixed-v1", seeds=(11,), same_cases_within_group=True),
        partition=c.Partition.LOCKED_TEST,
        harness_version=context.policy.harness_version,
        checkpoint_selection_rule="development-only fixed rule",
        invalid_trial_rule="report all assigned and valid-only",
        metric="pass_at_1", episodes_per_trial=1,
        frozen_roster=roster_ref, preregistration=prereg_ref,
    )
    return context, config


def test_arm_protocol_freezes_training_source_roots(tmp_path, monkeypatch):
    from feature_rl.evaluation import ArmProtocol, EvaluationPreregistration

    context, config = evaluation_inputs(tmp_path, monkeypatch)
    prereg = EvaluationPreregistration.model_validate_json(
        context.store.get_bytes(config.preregistration)
    )
    source = _opaque(context.store, "m6-construction-result", {"diagnostic": True})
    payload = prereg.arms[1].model_dump(mode="json")
    payload["training_sources"] = (source.model_dump(mode="json"),)
    protocol = ArmProtocol.model_validate_json(canonical_json(payload))
    assert protocol.training_sources == (source,)


def test_evaluation_service_revalidates_external_config_json(tmp_path, monkeypatch):
    from pydantic import ValidationError
    from feature_rl.evaluation import EvaluationService

    context, config = evaluation_inputs(tmp_path, monkeypatch)
    invalid = config.model_copy(update={"episodes_per_trial": 2})
    service = EvaluationService(
        store=context.store, registry=context.registry,
        runner=context.runner, revision=REVISION,
        evidence_scope="unit_diagnostic",
    )
    with pytest.raises(ValidationError, match="pass_at_1 permits exactly one episode"):
        service.evaluate(invalid)


def test_evaluation_service_runs_each_frozen_assignment_once_with_explicit_case_seed(tmp_path, monkeypatch):
    from feature_rl.evaluation import EvaluationService

    context, config = evaluation_inputs(tmp_path, monkeypatch)
    service = EvaluationService(
        store=context.store, registry=context.registry,
        runner=context.runner, revision=REVISION,
        evidence_scope="unit_diagnostic",
    )
    result = service.evaluate(config)
    assert result.operation == "evaluate" and result.disposition == c.Disposition.SUCCESS
    report = context.store.get_artifact(result.artifacts[0])
    assert type(report) is c.EvaluationReport
    assert len(report.trials) == 2
    assert {trial.case_seed for trial in report.trials} == {73}
    assert {trial.policy_seed for trial in report.trials} == {11}
    assert len(context.backend.calls) == len(context.grades) == 2
    assert [seed for _, _, seed in context.grades] == [73, 73]
    assert all(context.runner.validate_record(context.store.get_artifact(trial.rollout)).run_id
               == context.store.get_artifact(trial.rollout).run_id
               for trial in report.trials)

    replay = service.evaluate(config)
    assert replay == result
    assert len(context.backend.calls) == len(context.grades) == 2


def test_evaluation_service_keeps_authenticated_invalid_assignments_provisional(tmp_path, monkeypatch):
    from feature_rl.evaluation import EvaluationService
    from feature_rl.pipeline import TaskLifecycle

    context, config = evaluation_inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(
        context.runner.lifecycle, "resolve_released",
        TaskLifecycle.resolve_released.__get__(context.runner.lifecycle),
    )
    result = EvaluationService(
        store=context.store, registry=context.registry,
        runner=context.runner, revision=REVISION,
        evidence_scope="unit_diagnostic",
    ).evaluate(config)
    report = context.store.get_artifact(result.artifacts[0])
    assert result.disposition == report.disposition == c.Disposition.PROVISIONAL
    assert all(trial.resolved is None and trial.disposition == c.Disposition.INVALID
               for trial in report.trials)
    assert not context.backend.calls and not context.grades
    metrics = {metric.name: metric for metric in report.paired_metrics}
    assert metrics["pass_at_1/A/all_assigned"].estimate == 0.0
    assert metrics["pass_at_1/A/valid_only"].estimate is None


def test_evaluation_recovery_replays_assigned_runner_receipts_without_new_episodes(tmp_path, monkeypatch):
    from feature_rl.artifacts import ArtifactError
    from feature_rl.evaluation import EvaluationRecoveryRequired, EvaluationService

    context, config = evaluation_inputs(tmp_path, monkeypatch)
    service = EvaluationService(
        store=context.store, registry=context.registry,
        runner=context.runner, revision=REVISION,
        evidence_scope="unit_diagnostic",
    )
    put = context.store.put_artifact

    def fail_report(value):
        if isinstance(value, c.EvaluationReport):
            raise ArtifactError("diagnostic evaluation publication outage")
        return put(value)

    monkeypatch.setattr(context.store, "put_artifact", fail_report)
    with pytest.raises(EvaluationRecoveryRequired) as caught:
        service.evaluate(config)
    assert len(context.backend.calls) == len(context.grades) == 2
    monkeypatch.setattr(context.store, "put_artifact", put)
    result = service.recover(caught.value.claim)
    assert result.disposition == c.Disposition.SUCCESS
    assert len(context.backend.calls) == len(context.grades) == 2


def test_native_composition_diagnostic_uses_claimed_factory_and_live_activation_contract(tmp_path, monkeypatch):
    from feature_rl.agents import AgentRunner
    from feature_rl.evaluation import (
        EvaluationPreregistration, EvaluationService, FrozenRoster, SourceAssignment,
    )
    from feature_rl.pipeline.construction import ConstructionResult
    from feature_rl.registry import CostObservation, JobSpec
    from feature_rl.training.factory import NativeSessionFactory
    from feature_rl.training.native import ActivationReceipt, NativeSettings, read_activation
    from feature_rl.training.skyrl_bridge import PINNED_HARBOR, PINNED_SKYRL
    import feature_rl.training.factory as factory_module

    context, config = evaluation_inputs(tmp_path, monkeypatch)
    original = context.store.get_artifact(context.task)
    original_pair = context.store.get_artifact(original.source_pair)
    original_candidate = context.store.get_artifact(original_pair.candidate)
    origin_evidence = original.provenance.evidence
    factory_candidate_value = original_candidate.model_copy(update={
        "repository_family": "factory-family", "request_lineage": ("factory-source",),
        "partition": c.Partition.TRAIN,
    })
    factory_candidate = context.store.put_artifact(factory_candidate_value)
    factory_pair = context.store.put_artifact(original_pair.model_copy(update={
        "candidate": factory_candidate,
        "provenance": original_pair.provenance.model_copy(update={
            "inputs": (factory_candidate, original_pair.baseline, original_pair.reference),
        }),
    }))

    def released_training_task(label, pair_ref, family, lineage):
        built = original.model_copy(update={
            "state": c.TaskState.BUILT, "qualification": None,
            "partition": c.Partition.TRAIN, "repository_family": family,
            "request_lineage": (lineage,), "source_pair": pair_ref,
        })
        built_ref = context.store.put_artifact(built)
        report = c.QualificationReport(
            kind="QualificationReport", schema_version=1, visibility=c.Visibility.PRIVATE,
            provenance=c.Provenance(
                producer="diagnostic.lifecycle", producer_version="6" * 40,
                created_at=NOW, inputs=(built_ref,), evidence=origin_evidence,
            ),
            costs=original.costs,
            task=built_ref, disposition=c.Disposition.PROVISIONAL,
            baseline_health=None, baseline_absence=None, reference_run=None,
            controls=(), fresh_runs=(), interrupted_reset_runs=(),
            rejection_reasons=("labeled lifecycle diagnostic",), repair_attempts=0,
            policy_version="diagnostic-" + label,
        )
        report_ref = context.store.put_artifact(report)
        released_ref = context.store.put_artifact(built.model_copy(update={
            "state": c.TaskState.RELEASED, "qualification": report_ref,
        }))
        for ref in (built_ref, report_ref, released_ref):
            context.registry.register(ref)
        return built_ref, released_ref

    factory_built, factory_task = released_training_task(
        "factory", factory_pair, "factory-family", "factory-source",
    )
    assert factory_built != factory_task
    locked_task = context.store.put_artifact(
        original.model_copy(update={"partition": c.Partition.LOCKED_TEST})
    )
    context.registry.register(locked_task)
    roster = FrozenRoster.model_validate_json(context.store.get_bytes(config.frozen_roster))
    roster = roster.model_copy(update={
        "locked_tasks": (locked_task,),
        "sources": (
            roster.sources[0].model_copy(update={"task": locked_task}),
            SourceAssignment(
                source_id="factory-source", task=factory_task,
                repository_family="factory-family", request_lineage=("factory-source",),
                partition=c.Partition.TRAIN, evidence=roster.sources[0].evidence,
            ),
        ),
    })
    roster_ref = _opaque(context.store, "m8-frozen-roster", roster.model_dump(mode="json"))
    prereg = EvaluationPreregistration.model_validate_json(
        context.store.get_bytes(config.preregistration)
    )
    initial = _opaque(context.store, "checkpoint", {"native": "initial"})
    context.registry.register(initial)
    a_policy = prereg.arms[0].policy.model_copy(update={
        "identity": prereg.arms[0].policy.identity.model_copy(update={"weights": initial}),
    })
    settings = NativeSettings(
        skyrl_checkout=str(tmp_path / "checkout"), model_directory=str(tmp_path / "model"),
        reference_directory=str(tmp_path / "reference"),
        tokenizer_directory=str(tmp_path / "tokenizer"), work_directory=str(tmp_path / "work"),
        tokenizer_sha256=context.backend.tokenizer_digest, probe_tokens=(1, 2),
        max_groups=1,
    )

    def selected_checkpoint(arm, algorithm, training_task, *, native_settings=settings,
                            learning_rate=1e-6):
        weights = _opaque(context.store, "checkpoint", {"weights": arm})
        context.registry.register(weights)
        policy = a_policy.model_copy(update={
            "identity": a_policy.identity.model_copy(update={"weights": weights}),
            "policy_version": "diagnostic-" + arm,
        })
        training = c.TrainingConfig(
            initial_policy=a_policy, reference_checkpoint=initial, tasks=(training_task,),
            limits=config.limits, seeds=config.seeds, algorithm=algorithm, group_size=4,
            max_updates=1, learning_rate=learning_rate, framework="skyrl",
            framework_version=PINNED_SKYRL, backend_version=PINNED_HARBOR,
            budget_usd=None,
        )
        request = {
            "version": "m7-training-request-v1", "configuration": training.model_dump(mode="json"),
            "settings": native_settings.model_dump(mode="json"), "revision": "7" * 40,
        }
        request_ref = _opaque(context.store, "m7-training-request", request)
        context.registry.register(request_ref, dependencies=(training_task, initial))
        job = context.registry.enqueue(JobSpec(
            operation="train", inputs=training.tasks, configuration=request_ref,
            implementation="7" * 40, invocation="diagnostic-train-" + arm, attempt_limit=1,
        ))
        claim = context.registry.claim(job.job_id, owner="diagnostic-training", claim_key="train-" + arm)
        optimizer = _opaque(context.store, "checkpoint", {"optimizer": arm})
        context.registry.register(optimizer)
        progress = {
            "version": "m7-checkpoint-progress-v1", "configuration": training.model_dump(mode="json"),
            "settings": native_settings.model_dump(mode="json"), "request": request_ref.model_dump(mode="json"),
            "progress": {
                "claim": claim.model_dump(mode="json"), "request": request_ref.model_dump(mode="json"),
                "policy": policy.model_dump(mode="json"),
                "native": {"checkpoint": optimizer.model_dump(mode="json"), "path": "/diagnostic"},
                "data_position": 1, "optimizer_steps": 1,
                "consumed": [training_task.model_dump(mode="json")],
            },
        }
        progress_ref = _opaque(context.store, "m7-checkpoint-progress", progress)
        context.registry.register(
            progress_ref, dependencies=(request_ref, training_task, weights, optimizer),
        )
        phase = c.CostRecord(
            category="training", wall_seconds=.1, cpu_seconds=None, gpu_seconds=None,
            input_tokens=None, output_tokens=None, human_minutes=None, usd=None,
            measurement="partial", note="Labeled selected-checkpoint diagnostic",
        )
        update = c.EvidenceRecord(
            producer="feature_rl.training", command=("diagnostic", arm), recorded_at=NOW,
            exit_status=0, artifacts=(progress_ref,), revision="7" * 40,
            scope="unit_diagnostic",
        )
        checkpoint = c.TrainingCheckpoint(
            kind="TrainingCheckpoint", schema_version=1, visibility=c.Visibility.PRIVATE,
            provenance=c.Provenance(
                producer="feature_rl.training", producer_version="7" * 40,
                created_at=NOW, inputs=(request_ref, progress_ref), evidence=(update,),
            ), costs=(phase,), weights=weights, optimizer_state=optimizer,
            reference_checkpoint=initial, data_position=1, policy_version=policy.policy_version,
            configuration=training, consumed_tasks=(training_task,), optimizer_steps=1,
            update_evidence=(update,), reload_evidence=(update,),
        )
        checkpoint_ref = context.store.put_artifact(checkpoint)
        context.registry.register(checkpoint_ref, dependencies=(request_ref, progress_ref))
        summary = _opaque(context.store, "m7-training-summary", {"checkpoint": checkpoint_ref.model_dump(mode="json")})
        context.registry.register(summary, dependencies=(checkpoint_ref,))
        outcome = c.EvidenceRecord(
            producer="feature_rl.training", command=("diagnostic-complete", arm), recorded_at=NOW,
            exit_status=0, artifacts=(summary,), revision="7" * 40, scope="unit_diagnostic",
        )
        observed = context.registry.reconcile(claim, CostObservation(
            source="diagnostic-training", upstream_attempt_id=claim.attempt_id,
            revision=1, receipts=(request_ref, summary), costs=(phase,),
        ))
        context.registry.complete(claim, c.OperationResult(
            operation="train", disposition=c.Disposition.SUCCESS,
            artifacts=(checkpoint_ref, summary), evidence=(outcome,), costs=(phase,),
            reason="labeled selected-checkpoint diagnostic",
        ), observations=(observed.observation_id,))
        return checkpoint_ref, policy, request_ref

    selected = {
        "feature_grpo": selected_checkpoint("feature_grpo", "grpo", factory_task),
    }

    construction_cost = c.CostRecord(
        category="construction", wall_seconds=.1, cpu_seconds=None, gpu_seconds=None,
        input_tokens=None, output_tokens=None, human_minutes=None, usd=None,
        measurement="partial", note="Labeled selected-construction diagnostic",
    )

    def selected_construction(label, task, candidate):
        source = _opaque(context.store, "m6-source-disposition", {"source": label})
        request = _opaque(context.store, "m6-construction-request", {"request": label})
        configuration = _opaque(context.store, "m6-construction-policy", {"policy": label})
        history = _opaque(context.store, "m5-repair-history", {"history": label})
        for ref in (candidate, source, request, configuration, history):
            context.registry.register(ref)
        child_spec = JobSpec(
            operation="construct", inputs=(request,), configuration=configuration,
            implementation="2" * 40, invocation="m2-build-" + label, attempt_limit=1,
        )
        child_job = context.registry.enqueue(child_spec)
        child_claim = context.registry.claim(
            child_job.job_id, owner="diagnostic-builder", claim_key="build-" + label,
        )
        child_evidence = c.EvidenceRecord(
            producer="feature_rl.pipeline.TaskBuilder", command=("diagnostic", label),
            recorded_at=NOW, exit_status=0, artifacts=(task,), revision="2" * 40,
            scope="unit_diagnostic",
        )
        child_result = c.OperationResult(
            operation="construct", disposition=c.Disposition.SUCCESS, artifacts=(task,),
            evidence=(child_evidence,), costs=(construction_cost,), reason="diagnostic build",
        )
        child_observed = context.registry.reconcile(child_claim, CostObservation(
            source="diagnostic-build", upstream_attempt_id=child_claim.attempt_id,
            revision=1, receipts=(request,), costs=(construction_cost,),
        ))
        context.registry.complete(
            child_claim, child_result, observations=(child_observed.observation_id,),
        )
        parent_spec = JobSpec(
            operation="construct", inputs=(candidate, source, request), configuration=configuration,
            implementation="6" * 40, invocation="m6-construct", attempt_limit=3,
        )
        parent_job = context.registry.enqueue(parent_spec)
        parent_claim = context.registry.claim(
            parent_job.job_id, owner="feature_rl.pipeline.Factory", claim_key="factory-" + label,
        )
        receipt = ConstructionResult(
            claim=parent_claim, request=request, build_job=child_job.job_id,
            build_result=child_result, history=history, disposition=c.Disposition.SUCCESS,
            reason=child_result.reason, costs=(construction_cost,), revision="6" * 40,
            recorded_at=NOW,
        )
        receipt_ref = context.store.put_bytes(
            canonical_json(receipt.model_dump(mode="json")),
            "m6-construction-result", c.Visibility.PRIVATE,
        )
        context.registry.register(
            receipt_ref, dependencies=(request, task, history, configuration),
        )
        parent_evidence = c.EvidenceRecord(
            producer="feature_rl.pipeline.Factory", command=("Factory.construct", request.sha256),
            recorded_at=NOW, exit_status=0, artifacts=(receipt_ref,), revision="6" * 40,
            scope="unit_diagnostic",
        )
        parent_result = c.OperationResult(
            operation="construct", disposition=c.Disposition.SUCCESS,
            artifacts=(task, history, receipt_ref), evidence=(parent_evidence,),
            costs=(construction_cost,), reason=child_result.reason,
        )
        parent_observed = context.registry.reconcile(parent_claim, CostObservation(
            source="m6-construction", upstream_attempt_id=parent_claim.attempt_id,
            revision=1, receipts=(request, receipt_ref), costs=(construction_cost,),
        ))
        context.registry.complete(
            parent_claim, parent_result, observations=(parent_observed.observation_id,),
        )
        return receipt_ref, history, source, candidate

    factory_receipt, factory_history, _, _ = selected_construction(
        "factory", factory_built, factory_candidate,
    )
    arms = []
    for protocol in prereg.arms:
        if protocol.arm == "base":
            arms.append(protocol.model_copy(update={
                "policy": a_policy, "checkpoint": initial, "initial_checkpoint": initial,
            }))
        else:
            checkpoint, policy, request = selected[protocol.arm]
            budget = protocol.training_budget.model_copy(update={
                "max_rollouts": 4,
                "max_assistant_tokens": 4 * config.limits.output_tokens,
            })
            arms.append(protocol.model_copy(update={
                "policy": policy, "checkpoint": checkpoint, "training_config": request,
                "initial_checkpoint": initial,
                "training_sources": (factory_receipt,),
                "training_budget": budget,
            }))
    prereg = prereg.model_copy(update={
        "arms": tuple(arms),
        "trials": tuple(trial.model_copy(update={"task": locked_task}) for trial in prereg.trials),
    })
    prereg_ref = _opaque(context.store, "m8-preregistration", prereg.model_dump(mode="json"))
    config = config.model_copy(update={
        "preregistration": prereg_ref,
        "frozen_roster": roster_ref,
        "tasks": (locked_task,),
        "arms": tuple(c.EvaluationArm(
            arm=item.arm, policy=item.policy, checkpoint=item.checkpoint,
            training_config=item.training_config,
        ) for item in arms),
    })
    bootstrap = c.TrainingConfig(
        initial_policy=arms[0].policy, reference_checkpoint=initial, tasks=(factory_task,),
        limits=config.limits, seeds=config.seeds, algorithm="grpo", group_size=4,
        max_updates=1, learning_rate=1e-6, framework="skyrl",
        framework_version=PINNED_SKYRL, backend_version=PINNED_HARBOR,
        budget_usd=None,
    )
    events = []

    class DiagnosticNativeSession:
        def __init__(self, **kwargs):
            events.append("startup")
            self.store, self.registry = kwargs["store"], kwargs["registry"]
            self.backend, self.revision = context.backend, kwargs["revision"]
            self.last_probe = {"scope": "unit_diagnostic", "native_execution": False}

        def activate_checkpoint(self, checkpoint, policy):
            events.append("activate:" + policy.policy_version)
            configuration = _opaque(self.store, "m7-native-configuration", {"diagnostic": True})
            probe = _opaque(self.store, "m7-native-probe", {"diagnostic": policy.policy_version})
            value = ActivationReceipt(
                checkpoint=checkpoint, policy=policy, configuration=configuration, probe=probe,
                workers=("diagnostic-worker",), tokenizer_sha256=self.backend.tokenizer_digest,
                template_sha256=self.backend.template_digest,
                implementation_revision=self.revision, recorded_at=NOW,
            )
            ref = self.store.put_bytes(
                canonical_json(value.model_dump(mode="json")),
                "m7-native-activation", c.Visibility.PRIVATE,
            )
            self.registry.register(ref, dependencies=(checkpoint, configuration, probe))
            return ref

        def validate_activation(self, ref, *, checkpoint, policy):
            value = read_activation(self.store, ref)
            assert value.checkpoint == checkpoint and value.policy == policy
            events.append("validate:" + policy.policy_version)
            return value

        def close(self):
            events.append("close")

    monkeypatch.setattr(factory_module, "NativeSession", DiagnosticNativeSession)
    monkeypatch.setattr(
        AgentRunner, "_messages",
        lambda self, task: [{"role": "user", "content": "DIAGNOSTIC source-only prompt"}],
    )
    native_factory = NativeSessionFactory(
        store=context.store, registry=context.registry, settings=settings,
        configuration=bootstrap, revision="7" * 40,
    )
    service = EvaluationService(
        store=context.store, registry=context.registry, native_factory=native_factory,
        lifecycle=context.runner.lifecycle, builder=context.runner.builder,
        runtime=context.runner.runtime, grader=context.runner.grader,
        revision=REVISION, evidence_scope="real_integration",
    )
    from feature_rl.evaluation import EvaluationRecoveryRequired, EvaluationRejected

    def altered_config(updated_arms):
        altered = prereg.model_copy(update={"arms": tuple(updated_arms)})
        altered_ref = _opaque(
            context.store, "m8-preregistration", altered.model_dump(mode="json"),
        )
        return config.model_copy(update={
            "preregistration": altered_ref,
            "arms": tuple(c.EvaluationArm(
                arm=item.arm, policy=item.policy, checkpoint=item.checkpoint,
                training_config=item.training_config,
            ) for item in updated_arms),
        })

    wrong_tools = _opaque(context.store, "m7-tool-protocol", {"declared": "actions-v1"})
    bad_protocol_arms = tuple(
        arm.model_copy(update={"tools": wrong_tools})
        for arm in prereg.arms
    )
    with pytest.raises(EvaluationRejected, match="actual runner protocol"):
        service.evaluate(altered_config(bad_protocol_arms))

    cleanup_prereg = prereg.model_copy(update={
        "checkpoint_selection_rule": "diagnostic startup cleanup",
    })
    cleanup_prereg_ref = _opaque(
        context.store, "m8-preregistration", cleanup_prereg.model_dump(mode="json"),
    )
    cleanup_config = config.model_copy(update={
        "preregistration": cleanup_prereg_ref,
        "checkpoint_selection_rule": cleanup_prereg.checkpoint_selection_rule,
    })
    finish_startup = native_factory._finish_startup

    def interrupted_startup(*args, **kwargs):
        raise OSError("diagnostic startup publication outage")

    monkeypatch.setattr(native_factory, "_finish_startup", interrupted_startup)
    with pytest.raises(EvaluationRecoveryRequired):
        service.evaluate(cleanup_config)
    monkeypatch.setattr(native_factory, "_finish_startup", finish_startup)
    cleanup_receipts = service.close()
    assert len(cleanup_receipts) == 1
    assert events[-1] == "close"

    declared_locked = roster.sources[1].model_copy(update={"partition": c.Partition.LOCKED_TEST})
    bad_roster = roster.model_copy(update={
        "locked_tasks": (factory_task,), "sources": (declared_locked,),
    })
    bad_roster_ref = _opaque(
        context.store, "m8-frozen-roster", bad_roster.model_dump(mode="json"),
    )
    bad_task_prereg = prereg.model_copy(update={
        "trials": tuple(trial.model_copy(update={"task": factory_task}) for trial in prereg.trials),
    })
    bad_task_prereg_ref = _opaque(
        context.store, "m8-preregistration", bad_task_prereg.model_dump(mode="json"),
    )
    with pytest.raises(EvaluationRejected, match="released task differs"):
        service.evaluate(config.model_copy(update={
            "tasks": (factory_task,), "frozen_roster": bad_roster_ref,
            "preregistration": bad_task_prereg_ref,
        }))

    wrong_arms = tuple(
        arm.model_copy(update={"training_config": initial})
        if arm.arm == "feature_grpo" else arm for arm in prereg.arms
    )
    bad_checkpoint_prereg = prereg.model_copy(update={"arms": wrong_arms})
    bad_checkpoint_prereg_ref = _opaque(
        context.store, "m8-preregistration", bad_checkpoint_prereg.model_dump(mode="json"),
    )
    with pytest.raises(EvaluationRejected, match="selected M7 checkpoint"):
        service.evaluate(config.model_copy(update={
            "preregistration": bad_checkpoint_prereg_ref,
            "arms": tuple(c.EvaluationArm(
                arm=item.arm, policy=item.policy, checkpoint=item.checkpoint,
                training_config=item.training_config,
                ) for item in wrong_arms),
        }))

    primary_prereg = prereg.model_copy(update={
        "checkpoint_selection_rule": "diagnostic primary and cleanup failure",
    })
    primary_prereg_ref = _opaque(
        context.store, "m8-preregistration", primary_prereg.model_dump(mode="json"),
    )
    primary_config = config.model_copy(update={
        "preregistration": primary_prereg_ref,
        "checkpoint_selection_rule": primary_prereg.checkpoint_selection_rule,
    })
    trial = service._trial
    native_close = native_factory.close

    def interrupted_trial(*args, **kwargs):
        raise RuntimeError("diagnostic primary evaluation failure")

    def interrupted_primary_cleanup(*args, **kwargs):
        raise OSError("diagnostic primary cleanup failure")

    monkeypatch.setattr(service, "_trial", interrupted_trial)
    monkeypatch.setattr(native_factory, "close", interrupted_primary_cleanup)
    with pytest.raises(EvaluationRecoveryRequired) as primary_caught:
        service.evaluate(primary_config)
    assert isinstance(primary_caught.value.__cause__, RuntimeError)
    assert isinstance(primary_caught.value.__cause__.native_cleanup_error, OSError)
    monkeypatch.setattr(service, "_trial", trial)
    monkeypatch.setattr(native_factory, "close", native_close)
    assert len(service.close()) == 1
    prior_activations = len([event for event in events if event.startswith("activate:")])
    prior_validations = len([event for event in events if event.startswith("validate:")])

    close = native_factory.close
    close_calls = []

    def interrupted_close(*args, **kwargs):
        close_calls.append("attempt")
        if len(close_calls) == 1:
            raise OSError("diagnostic shutdown publication outage")
        return close(*args, **kwargs)

    monkeypatch.setattr(native_factory, "close", interrupted_close)
    with pytest.raises(EvaluationRecoveryRequired) as caught:
        service.evaluate(config)
    assert len(context.backend.calls) == len(context.grades) == 2
    terminal_receipts = service.close()
    assert len(terminal_receipts) == 1
    result = service.recover(caught.value.claim)
    report = context.store.get_artifact(result.artifacts[0])
    assert result.disposition == c.Disposition.SUCCESS
    assert events[0] == "startup" and events[-1] == "close"
    assert len([event for event in events if event.startswith("activate:")]) - prior_activations == 2
    assert len([event for event in events if event.startswith("validate:")]) - prior_validations == 4
    assert len(context.backend.calls) == len(context.grades) == 2
    assert close_calls == ["attempt", "attempt"]
    assert any("GPU-seconds and USD" in item for item in report.limitations)
    assert all(any(ref.kind == "m7-native-activation" for evidence in trial.evidence
                   for ref in evidence.artifacts) for trial in report.trials)
    job_id = next(job_id for job_id in context.registry.trace(config.preregistration).jobs
                  if context.registry.job(job_id).spec.operation == "evaluate")
    job = context.registry.job(job_id)
    assert any(row.observation.source == "m7-native-startup"
               for row in context.registry.accounting(job.job_id).observations)
    assert any(row.observation.source == "m7-native-shutdown"
               for row in context.registry.accounting(job.job_id).observations)

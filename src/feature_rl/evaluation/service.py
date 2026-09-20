"""Execution of a frozen M8 study through the selected M7 runner."""
from __future__ import annotations

from typing import Literal
import time
import uuid

from feature_rl import contracts as c
from feature_rl.agents import AgentRunner
from feature_rl.agents.runner import aggregate, cost
from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.environments import EnvironmentRuntime
from feature_rl.grading import GradingService
from feature_rl.pipeline import ReleasedTaskResolver, TaskBuilder, TaskLifecycle
from feature_rl.qualification.evidence import unknown_cost
from feature_rl.registry import Claim, CostObservation, JobSpec, Registry
from feature_rl.training.factory import NativeHandle, NativeSessionFactory
from feature_rl.training.checkpoints import validate_selected_checkpoint
from feature_rl.verifiers.language import decode_json
from feature_rl.verifiers.loader import read_local

from .freeze import validate_preregistration
from .models import ArmProtocol, EvaluationPreregistration, FrozenRoster
from .statistics import summarize_trials


class EvaluationRejected(ValueError):
    """The configured study or selected runner outcome does not join exactly."""


class EvaluationRecoveryRequired(Exception):
    """Resume a selected evaluation attempt from its frozen execution receipt."""

    def __init__(self, message, claim):
        super().__init__(message)
        self.claim = claim


class FrozenEvaluationExecution(c.StrictModel):
    version: Literal["m8-evaluation-execution-v1"] = "m8-evaluation-execution-v1"
    claim: Claim
    configuration: c.ArtifactRef
    trials: tuple[c.TrialResult, ...]
    run_results: tuple[c.OperationResult, ...]
    activations: tuple[c.ArtifactRef, ...]


def _refs(evidence):
    return tuple(dict.fromkeys(ref for item in evidence for ref in item.artifacts))


class EvaluationService:
    """Run every frozen assignment and consume its selected M7/M4 receipt."""

    def __init__(
        self, *, store: ArtifactStore, registry: Registry, revision: str,
        evidence_scope: str = "real_integration", runner: AgentRunner | None = None,
        native_factory: NativeSessionFactory | None = None,
        lifecycle: TaskLifecycle | ReleasedTaskResolver | None = None,
        builder: TaskBuilder | None = None, runtime: EnvironmentRuntime | None = None,
        grader: GradingService | None = None,
    ):
        if not isinstance(store, ArtifactStore) or not isinstance(registry, Registry) or registry.store is not store:
            raise TypeError("evaluation requires the actual same-store controller Registry")
        if type(revision) is not str or len(revision) not in (40, 64) or any(
            char not in "0123456789abcdef" for char in revision
        ):
            raise ValueError("implementation revision required")
        if evidence_scope not in {"real_integration", "unit_diagnostic"}:
            raise ValueError("evaluation evidence scope must identify real integration or unit diagnostics")
        if evidence_scope == "unit_diagnostic":
            if (
                not isinstance(runner, AgentRunner) or runner.store is not store
                or runner.registry is not registry or native_factory is not None
                or any(item is not None for item in (lifecycle, builder, runtime, grader))
            ):
                raise TypeError("unit diagnostics require only an actual same-store AgentRunner")
        else:
            if runner is not None or type(native_factory) is not NativeSessionFactory:
                raise TypeError("real evaluation requires the inert M7 NativeSessionFactory")
            if (
                native_factory.store is not store or native_factory.registry is not registry
                or type(lifecycle) not in (TaskLifecycle, ReleasedTaskResolver)
                or lifecycle.store is not store or lifecycle.registry is not registry
                or type(builder) is not TaskBuilder or builder.store is not store or builder.registry is not registry
                or type(runtime) is not EnvironmentRuntime or runtime.store is not store
                or type(grader) is not GradingService or grader.store is not store or grader.runtime is not runtime
            ):
                raise TypeError("real evaluation requires actual same-store M3/M4/M6 services")
        self.store, self.registry, self.runner = store, registry, runner
        self.native_factory = native_factory
        self.lifecycle, self.builder, self.runtime, self.grader = lifecycle, builder, runtime, grader
        self.revision, self.evidence_scope = revision, evidence_scope
        self._handles: dict[str, tuple[NativeHandle, str]] = {}
        self._active_job: str | None = None

    def _execution_identity(self):
        if self.evidence_scope == "unit_diagnostic":
            return {"mode": "unit_diagnostic", "runner": self.runner.configuration.model_dump(mode="json")}
        return {
            "mode": "native", "native_factory": self.native_factory.configuration.model_dump(mode="json"),
            "native_revision": self.native_factory.revision,
            "lifecycle": self.lifecycle.configuration.model_dump(mode="json"),
            "builder_revision": self.builder.revision, "runtime_revision": self.runtime.revision,
            "grader_revision": self.grader.revision,
        }

    def _freeze(self, config):
        config = c.EvaluationConfig.model_validate(config)
        roster = read_local(self.store, config.frozen_roster, FrozenRoster, "m8-frozen-roster")
        preregistration = read_local(
            self.store, config.preregistration, EvaluationPreregistration, "m8-preregistration",
        )
        validate_preregistration(roster, preregistration, config)
        if self.evidence_scope == "real_integration":
            frozen_sources = {item.task: item for item in roster.sources}
            for source in roster.sources:
                task = self.lifecycle.resolve_released(source.task)
                if (
                    task.partition != source.partition
                    or task.repository_family != source.repository_family
                    or task.request_lineage != source.request_lineage
                ):
                    raise EvaluationRejected(
                        "released task differs from its frozen source/partition assignment"
                    )
            protocols = {item.arm: item for item in preregistration.arms}
            bootstrap = self.native_factory.training_configuration.initial_policy
            if (
                protocols["A"].checkpoint != protocols["A"].policy.identity.weights
                or protocols["A"].checkpoint != bootstrap.identity.weights
                or any(
                    item.policy.identity.model != bootstrap.identity.model
                    or item.policy.identity.tokenizer_digest != bootstrap.identity.tokenizer_digest
                    or item.policy.system_prompt.visibility != c.Visibility.PUBLIC
                    for item in preregistration.arms
                )
            ):
                raise EvaluationRejected("frozen arms differ from the native bootstrap model boundary")
            for arm in ("B", "C", "D"):
                protocol = protocols[arm]
                checkpoint = validate_selected_checkpoint(
                    self.store, self.registry, protocol.checkpoint,
                )
                requests = tuple(
                    ref for ref in checkpoint.provenance.inputs
                    if ref.kind == "m7-training-request"
                )
                expected_algorithm = "sft" if arm == "B" else "grpo"
                if (
                    requests != (protocol.training_config,)
                    or checkpoint.weights != protocol.policy.identity.weights
                    or checkpoint.policy_version != protocol.policy.policy_version
                    or checkpoint.configuration.algorithm != expected_algorithm
                    or checkpoint.configuration.initial_policy.identity.weights != protocols["A"].checkpoint
                    or checkpoint.reference_checkpoint != self.native_factory.training_configuration.reference_checkpoint
                    or not checkpoint.consumed_tasks
                ):
                    raise EvaluationRejected(
                        "trained arm differs from its selected M7 checkpoint/configuration"
                    )
                for task_ref in checkpoint.configuration.tasks:
                    source = frozen_sources.get(task_ref)
                    if source is None or source.partition != c.Partition.TRAIN:
                        raise EvaluationRejected(
                            "training configuration task is outside the frozen training source frame"
                        )
                    task = self.lifecycle.resolve_released(task_ref)
                    if (
                        task.partition != c.Partition.TRAIN
                        or task.repository_family != source.repository_family
                        or task.request_lineage != source.request_lineage
                    ):
                        raise EvaluationRejected(
                            "training configuration task differs from its released source assignment"
                        )
                if any(ref not in checkpoint.configuration.tasks for ref in checkpoint.consumed_tasks):
                    raise EvaluationRejected("checkpoint consumed a task outside its frozen training configuration")
        roster_dependencies = tuple(dict.fromkeys((
            *roster.locked_tasks, roster.test_source_frame, roster.exclusions,
            *(item.evidence for item in roster.sources), *(item.evidence for item in roster.relations),
        )))
        self.registry.register(config.frozen_roster, dependencies=roster_dependencies)
        prereg_dependencies = tuple(dict.fromkeys((
            *(item.task for item in preregistration.trials),
            *(item.checkpoint for item in preregistration.arms),
            *(item.training_config for item in preregistration.arms if item.training_config is not None),
            *(item.initial_checkpoint for item in preregistration.arms),
            *(item.tools for item in preregistration.arms),
            *(item.policy.system_prompt for item in preregistration.arms),
        )))
        self.registry.register(config.preregistration, dependencies=prereg_dependencies)
        execution_ref = self.runner.configuration if self.evidence_scope == "unit_diagnostic" else self.native_factory.configuration
        payload = canonical_json({
            "version": "m8-evaluation-request-v1", "evaluation": config.model_dump(mode="json"),
            "execution": self._execution_identity(), "revision": self.revision,
        })
        configuration = self.store.put_bytes(payload, "m8-evaluation-configuration", c.Visibility.PRIVATE)
        self.registry.register(configuration, dependencies=(config.frozen_roster, config.preregistration, execution_ref))
        return config, roster, preregistration, configuration

    def _spec(self, config, configuration):
        return JobSpec(
            operation="evaluate", inputs=(config.frozen_roster, config.preregistration),
            configuration=configuration, implementation=self.revision,
            invocation="m8-evaluate-v1", attempt_limit=1,
        )

    def _trial(self, runner, assignment, protocol, limits, families, activation=None):
        policy = protocol.policy.model_copy(update={"seed": assignment.policy_seed})
        activation_evidence = ()
        if activation is not None:
            activation_ref, activation_value = activation
            handle = self._handles[self._active_job][0]
            current = handle.session.validate_activation(
                activation_ref, checkpoint=protocol.checkpoint, policy=policy,
            )
            if current != activation_value:
                raise EvaluationRejected("live native activation changed before assigned trial")
            activation_evidence = (c.EvidenceRecord(
                producer="feature_rl.training.NativeSession",
                command=("NativeSession.validate_activation", activation_ref.sha256, assignment.trial_id),
                recorded_at=current.recorded_at, exit_status=0,
                artifacts=(activation_ref, current.configuration, current.probe),
                revision=current.implementation_revision, scope="real_integration",
            ),)
        result = runner.run(
            assignment.task, policy, limits, case_seed=assignment.case_seed,
            invocation="m8-eval-" + assignment.trial_id,
        )
        if result.operation != "run":
            raise EvaluationRejected("assigned runner result is not a run operation")
        rollout_refs = tuple(ref for ref in result.artifacts if ref.kind == "RolloutRecord")
        receipt_refs = tuple(ref for ref in result.artifacts if ref.kind == "m7-frozen-run")
        if len(rollout_refs) != 1 or len(receipt_refs) != 1 or len(result.artifacts) != 2:
            raise EvaluationRejected("assigned runner result lacks one rollout and one frozen receipt")
        rollout_ref = rollout_refs[0]
        record = self.store.get_artifact(rollout_ref)
        if type(record) is not c.RolloutRecord:
            raise EvaluationRejected("assigned rollout artifact has the wrong type")
        runner.validate_record(record)
        if (
            record.task != assignment.task or record.policy != policy or record.limits != limits
            or record.seeds.seeds != (assignment.case_seed,) or result.disposition != record.disposition
        ):
            raise EvaluationRejected("selected runner record differs from its frozen assignment")
        if record.disposition in {c.Disposition.SUCCESS, c.Disposition.REJECTED}:
            if record.reward not in {0, 1}:
                raise EvaluationRejected("measured evaluation reward must be binary")
            resolved = bool(record.reward)
        else:
            if record.reward is not None:
                raise EvaluationRejected("unmeasured evaluation disposition retained a reward")
            resolved = None
        return c.TrialResult(
            trial_id=assignment.trial_id, task=assignment.task,
            repository_family=families[assignment.task], arm=assignment.arm,
            policy_seed=assignment.policy_seed, case_seed=assignment.case_seed,
            rollout=rollout_ref, disposition=record.disposition, resolved=resolved,
            evidence=(*activation_evidence, *result.evidence),
        ), result

    def _activation(self, claim, session, protocol: ArmProtocol, policy_seed: int, phase_key: str):
        policy = protocol.policy.model_copy(update={"seed": policy_seed})
        self.registry.reconcile(claim, CostObservation(
            source="m8-native-activation", upstream_attempt_id=phase_key, revision=1,
            receipts=(protocol.checkpoint, self.native_factory.configuration),
            costs=(cost("evaluation", note="Native arm activation dispatched; CPU/GPU/USD and outcome unknown"),),
        ))
        started = time.monotonic()
        reference = session.activate_checkpoint(protocol.checkpoint, policy)
        value = session.validate_activation(reference, checkpoint=protocol.checkpoint, policy=policy)
        observed = self.registry.reconcile(claim, CostObservation(
            source="m8-native-activation", upstream_attempt_id=phase_key, revision=2,
            receipts=(protocol.checkpoint, self.native_factory.configuration, reference),
            costs=(cost("evaluation", wall=time.monotonic() - started,
                        note="Actual arm load, broadcast and worker probe wall; CPU/GPU/USD unknown"),),
        ))
        return reference, value, observed.observation_id

    def _publish_execution(self, claim, configuration, trials, results, activations):
        execution = FrozenEvaluationExecution(
            claim=claim, configuration=configuration, trials=tuple(trials),
            run_results=tuple(results), activations=tuple(dict.fromkeys(activations)),
        )
        reference = self.store.put_bytes(
            canonical_json(execution.model_dump(mode="json")), "m8-evaluation-execution", c.Visibility.PRIVATE,
        )
        dependencies = tuple(dict.fromkeys((
            configuration, *execution.activations,
            *(trial.rollout for trial in execution.trials if trial.rollout is not None),
            *(ref for result in execution.run_results for ref in result.artifacts),
            *_refs(tuple(evidence for result in execution.run_results for evidence in result.evidence)),
        )))
        self.registry.register(reference, dependencies=dependencies)
        self.registry.reconcile(claim, CostObservation(
            source="m8-evaluation-execution", upstream_attempt_id=claim.attempt_id + ":execution",
            revision=1, receipts=(reference,),
            costs=aggregate(tuple(value for result in execution.run_results for value in result.costs)),
        ))
        return reference, execution

    def _existing_execution(self, claim, configuration):
        matches = []
        for row in self.registry.accounting(claim.job_id).observations:
            if row.observation.source != "m8-evaluation-execution":
                continue
            refs = tuple(ref for ref in row.observation.receipts if ref.kind == "m8-evaluation-execution")
            if len(refs) != 1:
                raise EvaluationRejected("evaluation execution accounting is ambiguous")
            value = read_local(self.store, refs[0], FrozenEvaluationExecution, "m8-evaluation-execution")
            if value.claim != claim or value.configuration != configuration:
                raise EvaluationRejected("evaluation execution belongs to another claim or configuration")
            matches.append((refs[0], value))
        if len(matches) > 1:
            raise EvaluationRejected("multiple frozen evaluation executions selected")
        return None if not matches else matches[0]

    def _validate_execution(self, execution, config, roster, preregistration, configuration):
        if execution.configuration != configuration:
            raise EvaluationRejected("frozen execution configuration differs")
        if len(execution.trials) != len(preregistration.trials) or len(execution.run_results) != len(execution.trials):
            raise EvaluationRejected("frozen execution does not account for every assigned trial")
        families = {item.task: item.repository_family for item in roster.sources}
        for assignment, trial, result in zip(
            preregistration.trials, execution.trials, execution.run_results,
        ):
            rollout_refs = tuple(ref for ref in result.artifacts if ref.kind == "RolloutRecord")
            if (
                trial.trial_id != assignment.trial_id or trial.task != assignment.task
                or trial.arm != assignment.arm or trial.policy_seed != assignment.policy_seed
                or trial.case_seed != assignment.case_seed
                or trial.repository_family != families[assignment.task]
                or result.operation != "run" or result.disposition != trial.disposition
                or rollout_refs != (trial.rollout,)
            ):
                raise EvaluationRejected("frozen execution result differs from its assigned trial")
        activation_refs = tuple(dict.fromkeys(
            ref for trial in execution.trials for evidence in trial.evidence
            for ref in evidence.artifacts if ref.kind == "m7-native-activation"
        ))
        expected_groups = len(preregistration.arms) * len(preregistration.seeds.seeds)
        if self.evidence_scope == "real_integration":
            if (
                len(execution.activations) != expected_groups
                or set(activation_refs) != set(execution.activations)
            ):
                raise EvaluationRejected("frozen execution lacks its exact native arm activations")
        elif execution.activations or activation_refs:
            raise EvaluationRejected("unit diagnostic execution cannot claim native activation")
        return execution

    def _diagnostic_execution(self, claim, config, roster, preregistration, configuration):
        protocols = {item.arm: item for item in preregistration.arms}
        families = {item.task: item.repository_family for item in roster.sources}
        trials, results = [], []
        for assignment in preregistration.trials:
            trial, result = self._trial(
                self.runner, assignment, protocols[assignment.arm], config.limits, families,
            )
            trials.append(trial); results.append(result)
        return self._publish_execution(claim, configuration, trials, results, ())

    def _native_execution(self, claim, config, roster, preregistration, configuration):
        startup_index = sum(
            row.observation.source == "m7-native-startup"
            for row in self.registry.accounting(claim.job_id).observations
        )
        startup_key = f"{claim.attempt_id}:m8-native:{startup_index}"
        handle = self.native_factory.create(claim, startup_key=startup_key)
        self._handles[claim.job_id] = (handle, startup_key)
        self._active_job = claim.job_id
        try:
            runner = AgentRunner(
                store=self.store, registry=self.registry, lifecycle=self.lifecycle,
                builder=self.builder, runtime=self.runtime, grader=self.grader,
                backend=handle.session.backend, revision=self.native_factory.revision,
                owner="feature_rl.evaluation", evidence_scope="real_integration",
            )
            protocols = {item.arm: item for item in preregistration.arms}
            families = {item.task: item.repository_family for item in roster.sources}
            by_key = {(trial.arm, trial.policy_seed): [] for trial in preregistration.trials}
            for trial in preregistration.trials:
                by_key[(trial.arm, trial.policy_seed)].append(trial)
            trials_by_id, results_by_id, activations = {}, {}, []
            for arm in (item.arm for item in preregistration.arms):
                for policy_seed in preregistration.seeds.seeds:
                    assigned = by_key.get((arm, policy_seed), ())
                    if not assigned:
                        continue
                    phase_key = f"{startup_key}:activate:{arm}:{policy_seed}"
                    activation_ref, activation, _ = self._activation(
                        claim, handle.session, protocols[arm], policy_seed, phase_key,
                    )
                    activations.append(activation_ref)
                    for assignment in assigned:
                        trial, result = self._trial(
                            runner, assignment, protocols[arm], config.limits, families,
                            (activation_ref, activation),
                        )
                        trials_by_id[trial.trial_id] = trial
                        results_by_id[trial.trial_id] = result
            ordered = tuple(trials_by_id[item.trial_id] for item in preregistration.trials)
            ordered_results = tuple(results_by_id[item.trial_id] for item in preregistration.trials)
            return self._publish_execution(claim, configuration, ordered, ordered_results, activations)
        finally:
            retained = self._handles.get(claim.job_id)
            if retained is not None:
                self.native_factory.close(retained[0], claim, shutdown_key=retained[1] + ":shutdown")
                del self._handles[claim.job_id]

    def _finish_native_cleanup(self, claim):
        retained = self._handles.get(claim.job_id)
        if retained is not None:
            self.native_factory.close(
                retained[0], claim, shutdown_key=retained[1] + ":shutdown",
            )
            del self._handles[claim.job_id]
        observations = self.registry.accounting(claim.job_id).observations
        startups = {
            row.observation.upstream_attempt_id
            for row in observations if row.observation.source == "m7-native-startup"
            and row.observation.revision == 2
        }
        shutdowns = {
            row.observation.upstream_attempt_id.removesuffix(":shutdown")
            for row in observations if row.observation.source == "m7-native-shutdown"
            and row.observation.revision == 2
        }
        if startups != shutdowns:
            raise EvaluationRecoveryRequired(
                "selected native execution has unresolved owned-session cleanup", claim,
            )

    def _result(self, claim, config, roster, preregistration, configuration, execution_ref, execution):
        statistics = summarize_trials(roster, preregistration, execution.trials)
        rollout_refs = tuple(trial.rollout for trial in execution.trials if trial.rollout is not None)
        run_evidence = tuple(item for result in execution.run_results for item in result.evidence)
        evidence = c.EvidenceRecord(
            producer="feature_rl.evaluation", command=("EvaluationService.evaluate", configuration.sha256),
            recorded_at=preregistration.created_at, exit_status=0,
            artifacts=(configuration, execution_ref, *rollout_refs, *execution.activations),
            revision=self.revision, scope=self.evidence_scope,
        )
        overhead = unknown_cost("evaluation", "M8 report aggregation and Registry publication overhead are unmeasured")
        final_key = claim.attempt_id + ":report"
        accounting = self.registry.accounting(claim.job_id).observations
        finals = tuple(
            row for row in accounting
            if row.observation.source == "m8-evaluation"
            and row.observation.upstream_attempt_id == final_key
        )
        if len(finals) > 1:
            raise EvaluationRejected("evaluation report accounting is ambiguous")
        if not finals:
            self.registry.reconcile(claim, CostObservation(
                source="m8-evaluation", upstream_attempt_id=final_key, revision=1,
                receipts=(configuration, execution_ref), costs=(overhead,),
            ))
        rows = sorted(
            self.registry.accounting(claim.job_id).observations,
            key=lambda row: row.observation_id,
        )
        costs = tuple(value for row in rows for value in row.observation.costs)
        disposition = c.Disposition.SUCCESS if any(
            trial.resolved is not None for trial in execution.trials
        ) else c.Disposition.PROVISIONAL
        report = c.EvaluationReport(
            kind="EvaluationReport", schema_version=1, visibility=c.Visibility.PRIVATE,
            provenance=c.Provenance(
                producer="feature_rl.evaluation.EvaluationService", producer_version=self.revision,
                created_at=preregistration.created_at,
                inputs=(configuration, execution_ref, *rollout_refs, *execution.activations),
                evidence=(evidence, *run_evidence),
            ), costs=costs, configuration=config,
            frozen_task_roster=(config.frozen_roster, config.preregistration), trials=execution.trials,
            paired_metrics=statistics.metrics, audits=(), disposition=disposition,
            limitations=statistics.limitations,
        )
        report_ref = self.store.put_artifact(report)
        self.registry.register(
            report_ref,
            dependencies=tuple(dict.fromkeys((
                configuration, execution_ref, config.frozen_roster, config.preregistration,
                *rollout_refs, *execution.activations, *_refs(report.provenance.evidence),
            ))),
        )
        result = c.OperationResult(
            operation="evaluate", disposition=disposition, artifacts=(report_ref,), evidence=(evidence,),
            costs=costs, reason="all frozen trial assignments executed and all-assigned/valid-only metrics recorded",
        )
        observations = tuple(row.observation_id for row in rows)
        return self.registry.complete(claim, result, observations=observations).result

    def _execute(self, claim, config, roster, preregistration, configuration):
        selected = self._existing_execution(claim, configuration)
        if selected is None:
            selected = (
                self._diagnostic_execution(claim, config, roster, preregistration, configuration)
                if self.evidence_scope == "unit_diagnostic"
                else self._native_execution(claim, config, roster, preregistration, configuration)
            )
        if self.evidence_scope == "real_integration":
            self._finish_native_cleanup(claim)
        self._validate_execution(selected[1], config, roster, preregistration, configuration)
        return self._result(claim, config, roster, preregistration, configuration, selected[0], selected[1])

    def evaluate(self, config: c.EvaluationConfig) -> c.OperationResult:
        config, roster, preregistration, configuration = self._freeze(config)
        job = self.registry.enqueue(self._spec(config, configuration))
        if job.state == "completed":
            return job.result
        if job.state != "queued":
            raise EvaluationRecoveryRequired(
                "evaluation already dispatched; recover its frozen execution",
                self.registry.attempts(job.job_id)[-1].claim,
            )
        claim = self.registry.claim(
            job.job_id, owner="feature_rl.evaluation.EvaluationService", claim_key=uuid.uuid4().hex,
        )
        try:
            return self._execute(claim, config, roster, preregistration, configuration)
        except EvaluationRecoveryRequired:
            raise
        except Exception as exc:
            raise EvaluationRecoveryRequired(
                "evaluation attempt selected; recover its frozen execution or assigned invocations", claim,
            ) from exc

    def recover(self, claim) -> c.OperationResult:
        job = self.registry.job(claim.job_id)
        if not any(item.claim == claim for item in self.registry.attempts(job.job_id)):
            raise ValueError("unknown evaluation claim")
        payload = decode_json(
            self.store.get_bytes(job.spec.configuration, max_envelope_bytes=2 * 1024 * 1024,
                                 max_payload_bytes=1024 * 1024), 1024 * 1024,
        )
        if type(payload) is not dict or payload.get("version") != "m8-evaluation-request-v1":
            raise ValueError("evaluation claim has an invalid frozen request")
        config = c.EvaluationConfig.model_validate_json(canonical_json(payload.get("evaluation")))
        frozen, roster, preregistration, configuration = self._freeze(config)
        if job.spec != self._spec(frozen, configuration):
            raise ValueError("evaluation claim belongs to a different frozen service configuration")
        if job.state == "completed":
            return job.result
        return self._execute(claim, frozen, roster, preregistration, configuration)

"""Execution of a frozen M8 study through the selected M7 runner."""
from __future__ import annotations

from typing import Literal
import time
import uuid

from feature_rl import contracts as c
from feature_rl.agents import AgentRunner
from feature_rl.agents.protocol import ACTION_FORMAT, HARNESS, validate_protocol
from feature_rl.agents.runner import aggregate, cost
from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.environments import EnvironmentRuntime
from feature_rl.grading import GradingService
from feature_rl.pipeline import Factory, ReleasedTaskResolver, TaskBuilder, TaskLifecycle
from feature_rl.pipeline.construction import ConstructionResult
from feature_rl.qualification.evidence import unknown_cost
from feature_rl.registry import Claim, CostObservation, JobSpec, Registry
from feature_rl.training.factory import (
    NativeHandle, NativeSessionFactory, NativeStartupRecoveryRequired,
)
from feature_rl.training.checkpoints import validate_selected_checkpoint
from feature_rl.training.native import NativeSettings, OPTIMIZER_FAMILY
from feature_rl.verifiers.language import decode_json
from feature_rl.verifiers.loader import read_local

from .freeze import episode_sampling_seed, validate_preregistration
from .adaptation import AdaptationBatch, ExternalCorpusAdapter, ExternalOriginMapping
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
        factory: Factory | None = None,
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
                or type(factory) is not Factory or factory.store is not store
                or factory.registry is not registry
                or type(lifecycle) not in (TaskLifecycle, ReleasedTaskResolver)
                or lifecycle.store is not store or lifecycle.registry is not registry
                or type(builder) is not TaskBuilder or builder.store is not store or builder.registry is not registry
                or type(runtime) is not EnvironmentRuntime or runtime.store is not store
                or type(grader) is not GradingService or grader.store is not store or grader.runtime is not runtime
            ):
                raise TypeError("real evaluation requires actual same-store M3/M4/M6 services")
        self.store, self.registry, self.runner = store, registry, runner
        self.native_factory = native_factory
        self.factory = factory
        self.lifecycle, self.builder, self.runtime, self.grader = lifecycle, builder, runtime, grader
        self.revision, self.evidence_scope = revision, evidence_scope
        self._handles: dict[str, tuple[NativeHandle, Claim, str]] = {}
        self._opening: dict[str, tuple[Claim, str]] = {}
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

    def _construction_root(self, reference):
        receipt = read_local(
            self.store, reference, ConstructionResult, "m6-construction-result",
        )
        job = self.registry.job(receipt.claim.job_id)
        attempts = self.registry.attempts(job.job_id)
        completed = tuple(
            item for item in attempts
            if item.claim == receipt.claim and item.state == "completed"
        )
        if (
            len(completed) != 1 or job.state != "completed" or job.result is None
            or job.spec.operation != "construct" or job.spec.invocation != "m6-construct"
            or job.spec.implementation != receipt.revision
            or len(job.spec.inputs) != 3 or job.spec.inputs[2] != receipt.request
            or receipt.disposition != c.Disposition.SUCCESS
            or receipt.build_job is None or receipt.build_result is None
            or receipt.history is None
            or receipt.build_result.disposition != c.Disposition.SUCCESS
            or receipt.build_result.operation != "construct"
            or len(receipt.build_result.artifacts) != 1
        ):
            raise EvaluationRejected("training source lacks a selected successful M6 construction")
        task = receipt.build_result.artifacts[0]
        if task.kind != "TaskBundle":
            raise EvaluationRejected("selected construction output is not a TaskBundle")
        bundle = self.store.get_artifact(task)
        if (
            type(bundle) is not c.TaskBundle or bundle.state != c.TaskState.BUILT
            or bundle.qualification is not None
        ):
            raise EvaluationRejected("selected construction output is not a typed TaskBundle")
        pair = self.store.get_artifact(bundle.source_pair)
        if type(pair) is not c.SourcePair or pair.candidate != job.spec.inputs[0]:
            raise EvaluationRejected("selected construction differs from its candidate source pair")
        expected = (task, receipt.history, reference)
        if (
            job.result.operation != "construct"
            or job.result.disposition != receipt.disposition
            or job.result.artifacts != expected
            or job.result.reason != receipt.reason
            or job.result.costs != receipt.costs
        ):
            raise EvaluationRejected("M6 construction receipt differs from its selected result")
        child = self.registry.job(receipt.build_job)
        if (
            child.state != "completed" or child.result != receipt.build_result
            or child.spec.operation != "construct"
        ):
            raise EvaluationRejected("M6 construction lacks its selected builder result")
        return task, receipt.history, pair.candidate, job.spec.inputs[1]

    def _built_roots(self, released_refs):
        roots = []
        for reference in released_refs:
            released = self.lifecycle.resolve_released(reference)
            if released.qualification is None:
                raise EvaluationRejected("released training task lacks qualification")
            report = self.store.get_artifact(released.qualification)
            if type(report) is not c.QualificationReport:
                raise EvaluationRejected("released training task lacks a typed qualification report")
            built = self.store.get_artifact(report.task)
            if type(built) is not c.TaskBundle or built.state != c.TaskState.BUILT:
                raise EvaluationRejected("released training task does not resolve to BUILT T0")
            roots.append(report.task)
        return tuple(roots)

    def _training_origins(self, protocols, actual_training):
        if protocols["A"].training_sources or protocols["B"].training_sources:
            raise EvaluationRejected("starting and SFT arms cannot claim RL construction origins")
        external = protocols["C"].training_sources
        if len(external) != 1 or external[0].kind != "m8-external-adaptation-batch":
            raise EvaluationRejected("external RL arm requires one frozen external adaptation batch")
        self.registry.assert_usable(external[0])
        batch = read_local(
            self.store, external[0], AdaptationBatch, "m8-external-adaptation-batch",
        )
        if (
            batch.funnel.disposition != c.Disposition.SUCCESS
            or batch.funnel.local_partition != c.Partition.TRAIN
            or batch.funnel.source_frame != batch.source_frame
            or any(
                stage.rejected or stage.invalid or stage.accepted != len(batch.items)
                for stage in batch.funnel.stages
            )
        ):
            raise EvaluationRejected("external adaptation funnel is incomplete or not training-assigned")
        adapter = ExternalCorpusAdapter(
            store=self.store, factory=self.factory,
            configuration=batch.configuration, revision=self.revision,
        )
        if (
            batch.configuration != adapter.configuration_ref
            or batch.source_frame != adapter.configuration.source_frame
            or batch.funnel.corpus_id != adapter.configuration.dataset_id
            or batch.funnel.release_revision != adapter.configuration.release_revision
            or batch.funnel.upstream_split != adapter.configuration.upstream_split
            or batch.funnel.license_constraint != adapter.configuration.dataset_license
            or tuple(item.row for item in batch.items) != adapter.configuration.rows
            or tuple(item.origin_mapping for item in batch.items)
            != adapter.configuration.origin_mappings
        ):
            raise EvaluationRejected("external adaptation batch changed its frozen configuration/frame")
        assignments = {item.row: item for item in adapter.frame.assignments}
        external_tasks, external_receipts = [], []
        for item, mapping_ref, build_inputs in zip(
            batch.items, adapter.configuration.origin_mappings,
            adapter.configuration.construction_inputs,
        ):
            try:
                raw, row = adapter._row(item.row)
                mapping = read_local(
                    self.store, mapping_ref, ExternalOriginMapping, "m8-external-origin",
                )
                candidate, pair = adapter._validated(
                    item.row, raw, row, mapping_ref, mapping,
                    assignments[item.row], build_inputs,
                )
            except (KeyError, ValueError, OSError) as exc:
                raise EvaluationRejected("external adaptation origin failed inert revalidation") from exc
            if (
                item.candidate is None or item.source_pair is None
                or item.candidate != mapping.candidate
                or item.source_pair != mapping.source_pair
                or candidate != self.store.get_artifact(item.candidate)
                or pair != self.store.get_artifact(item.source_pair)
                or item.source_only_allowlist != (
                    mapping.authoring_request, mapping.authoring_baseline,
                    mapping.authoring_license,
                )
            ):
                raise EvaluationRejected("external adaptation item differs from its authenticated M1 origin")
            receipts = tuple(
                ref for ref in item.construction_result
                if ref.kind == "m6-construction-result"
            )
            if item.disposition != c.Disposition.SUCCESS or len(receipts) != 1:
                raise EvaluationRejected("external adaptation item lacks one successful construction")
            task, history, construction_candidate, construction_source = self._construction_root(
                receipts[0]
            )
            if (
                construction_candidate != mapping.candidate
                or item.source_result != (construction_source,)
                or item.construction_result != (task, history, receipts[0])
            ):
                raise EvaluationRejected("external adaptation item changed its selected construction roots")
            external_tasks.append(task)
            external_receipts.append(receipts[0])
        factory_receipts = protocols["D"].training_sources
        if not factory_receipts or any(
            ref.kind != "m6-construction-result" for ref in factory_receipts
        ):
            raise EvaluationRejected("factory RL arm requires selected M6 construction roots")
        factory_tasks = tuple(self._construction_root(ref)[0] for ref in factory_receipts)
        external_tasks = tuple(external_tasks)
        if (
            external_tasks != self._built_roots(actual_training["C"][0].tasks)
            or factory_tasks != self._built_roots(actual_training["D"][0].tasks)
        ):
            raise EvaluationRejected("RL training tasks differ from their frozen dataset origins")
        if set(external_tasks) & set(factory_tasks) or set(external_receipts) & set(factory_receipts):
            raise EvaluationRejected("external and factory RL origins overlap")

    def _freeze(self, config):
        if isinstance(config, c.EvaluationConfig):
            config = c.EvaluationConfig.model_validate_json(config.model_dump_json())
        else:
            config = c.EvaluationConfig.model_validate_json(canonical_json(config))
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
            try:
                for protocol in protocols.values():
                    validate_protocol(
                        self.store, protocol.tools, action_format=protocol.action_format,
                    )
            except ValueError as exc:
                raise EvaluationRejected(
                    "frozen tools/actions differ from the actual runner protocol"
                ) from exc
            if (
                preregistration.harness_version != HARNESS
                or any(
                    protocol.harness_version != HARNESS
                    or protocol.policy.harness_version != HARNESS
                    or protocol.action_format != ACTION_FORMAT
                    or protocol.optimizer_family != OPTIMIZER_FAMILY
                    for protocol in protocols.values()
                )
            ):
                raise EvaluationRejected(
                    "frozen declarations differ from the actual runner protocol"
                )
            bootstrap = self.native_factory.training_configuration.initial_policy
            if (
                protocols["A"].checkpoint != protocols["A"].policy.identity.weights
                or protocols["A"].policy != bootstrap
                or protocols["A"].checkpoint != bootstrap.identity.weights
                or any(
                    item.policy.identity.model != bootstrap.identity.model
                    or item.policy.identity.tokenizer_digest != bootstrap.identity.tokenizer_digest
                    or item.policy.system_prompt.visibility != c.Visibility.PUBLIC
                    for item in preregistration.arms
                )
            ):
                raise EvaluationRejected("frozen arms differ from the native bootstrap model boundary")
            actual_training = {}
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
                    or checkpoint.configuration.initial_policy != protocols["A"].policy
                    or checkpoint.reference_checkpoint != self.native_factory.training_configuration.reference_checkpoint
                    or not checkpoint.consumed_tasks
                ):
                    raise EvaluationRejected(
                        "trained arm differs from its selected M7 checkpoint/configuration"
                    )
                request = decode_json(
                    self.store.get_bytes(
                        requests[0], max_envelope_bytes=8 * 1024 * 1024,
                        max_payload_bytes=4 * 1024 * 1024,
                    ),
                    4 * 1024 * 1024,
                )
                if type(request) is not dict:
                    raise EvaluationRejected("selected M7 training request is not an object")
                try:
                    settings = NativeSettings.model_validate_json(
                        canonical_json(request.get("settings"))
                    )
                except ValueError as exc:
                    raise EvaluationRejected("selected M7 request has invalid native settings") from exc
                actual_training[arm] = (checkpoint.configuration, settings)
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
            def shared_config(value):
                controls = value.model_dump(mode="json")
                return {
                    key: item for key, item in controls.items()
                    if key not in {"algorithm", "tasks"}
                }

            if len({
                canonical_json(shared_config(actual_training[arm][0]))
                for arm in ("B", "C", "D")
            }) != 1:
                raise EvaluationRejected("trained arms differ in shared TrainingConfig controls")
            shared_settings = (
                "tokenizer_sha256", "num_gpus", "max_seq_len", "groups_per_update",
                "mini_batch_groups", "probe_tokens", "probe_output_tokens",
                "probe_timeout_seconds", "max_groups", "max_wall_seconds",
            )
            if any(
                len({getattr(actual_training[arm][1], field) for arm in ("B", "C", "D")}) != 1
                for field in shared_settings
            ):
                raise EvaluationRejected("trained arms differ in shared native resource controls")
            evaluator_lora = canonical_json(
                self.native_factory.settings.lora.model_dump(mode="json")
            )
            learned_lora = tuple(
                canonical_json(actual_training[arm][1].lora.model_dump(mode="json"))
                for arm in ("B", "C", "D")
            )
            if any(value != evaluator_lora for value in learned_lora):
                raise EvaluationRejected(
                    "trained arms and evaluator differ in frozen LoRA controls"
                )
            if any(
                len({getattr(actual_training[arm][1], field) for arm in ("C", "D")}) != 1
                for field in ("clip_epsilon", "kl_coefficient")
            ):
                raise EvaluationRejected("RL arms differ in native clipping or KL controls")
            for arm in ("B", "C", "D"):
                training, settings = actual_training[arm]
                budget = protocols[arm].training_budget
                if budget.max_updates != training.max_updates:
                    raise EvaluationRejected(
                        "declared update budget differs from selected training run"
                    )
                if arm in ("C", "D"):
                    actual_rollouts = settings.max_groups * training.group_size
                    if (
                        budget.max_rollouts != actual_rollouts
                        or budget.max_assistant_tokens
                        != actual_rollouts * training.limits.output_tokens
                    ):
                        raise EvaluationRejected(
                            "declared RL rollout/token budget differs from native bounds"
                        )
            self._training_origins(protocols, actual_training)
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
            *(ref for item in preregistration.arms for ref in item.training_sources),
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

    def _trial(
        self, runner, assignment, protocol, limits, families, sampling_seed,
        activation=None,
    ):
        policy = protocol.policy.model_copy(update={"seed": sampling_seed})
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

    def _activation(self, claim, session, protocol: ArmProtocol, sampling_seed: int, phase_key: str):
        policy = protocol.policy.model_copy(update={"seed": sampling_seed})
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
        protocols = {item.arm: item for item in preregistration.arms}
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
            record = self.store.get_artifact(trial.rollout)
            expected_policy = protocols[assignment.arm].policy.model_copy(update={
                "seed": episode_sampling_seed(preregistration, assignment),
            })
            if (
                type(record) is not c.RolloutRecord
                or record.task != assignment.task
                or record.policy != expected_policy
                or record.seeds.seeds != (assignment.case_seed,)
            ):
                raise EvaluationRejected(
                    "frozen rollout differs from its paired episode sampling/case seeds"
                )
        activation_refs = tuple(dict.fromkeys(
            ref for trial in execution.trials for evidence in trial.evidence
            for ref in evidence.artifacts if ref.kind == "m7-native-activation"
        ))
        expected_groups = len({
            (assignment.arm, episode_sampling_seed(preregistration, assignment))
            for assignment in preregistration.trials
        })
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
                episode_sampling_seed(preregistration, assignment),
            )
            trials.append(trial); results.append(result)
        return self._publish_execution(claim, configuration, trials, results, ())

    def _native_execution(self, claim, config, roster, preregistration, configuration):
        if claim.job_id in self._opening:
            raise EvaluationRecoveryRequired(
                "retained unpublished native startup requires cleanup before recovery", claim,
            )
        startup_index = sum(
            row.observation.source == "m7-native-startup"
            for row in self.registry.accounting(claim.job_id).observations
        )
        startup_key = f"{claim.attempt_id}:m8-native:{startup_index}"
        self._opening[claim.job_id] = (claim, startup_key)
        handle = self.native_factory.create(claim, startup_key=startup_key)
        del self._opening[claim.job_id]
        self._handles[claim.job_id] = (handle, claim, startup_key)
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
            by_key = {}
            for trial in preregistration.trials:
                key = (trial.arm, episode_sampling_seed(preregistration, trial))
                by_key.setdefault(key, []).append(trial)
            trials_by_id, results_by_id, activations = {}, {}, []
            for arm in (item.arm for item in preregistration.arms):
                for (group_arm, sampling_seed), assigned in by_key.items():
                    if group_arm != arm:
                        continue
                    phase_key = f"{startup_key}:activate:{arm}:{sampling_seed}"
                    activation_ref, activation, _ = self._activation(
                        claim, handle.session, protocols[arm], sampling_seed, phase_key,
                    )
                    activations.append(activation_ref)
                    for assignment in assigned:
                        trial, result = self._trial(
                            runner, assignment, protocols[arm], config.limits, families,
                            sampling_seed,
                            (activation_ref, activation),
                        )
                        trials_by_id[trial.trial_id] = trial
                        results_by_id[trial.trial_id] = result
            ordered = tuple(trials_by_id[item.trial_id] for item in preregistration.trials)
            ordered_results = tuple(results_by_id[item.trial_id] for item in preregistration.trials)
            execution = self._publish_execution(
                claim, configuration, ordered, ordered_results, activations,
            )
        except BaseException as error:
            try:
                self._close_handle(claim.job_id)
            except BaseException as cleanup:
                error.native_cleanup_error = cleanup
            raise
        self._close_handle(claim.job_id)
        return execution

    def _close_handle(self, job_id):
        retained = self._handles.get(job_id)
        if retained is None:
            return None
        handle, claim, startup_key = retained
        receipt, _ = self.native_factory.close(
            handle, claim, shutdown_key=startup_key + ":shutdown",
        )
        del self._handles[job_id]
        return receipt

    def close(self):
        """Close every retained native startup/session without initializing anything."""
        if self.native_factory is None:
            return ()
        receipts, errors = [], []
        for job_id in tuple(self._handles):
            try:
                receipt = self._close_handle(job_id)
            except BaseException as exc:
                errors.append(exc)
                continue
            if receipt is not None:
                receipts.append(receipt)
        for job_id, (claim, startup_key) in tuple(self._opening.items()):
            try:
                receipt, _ = self.native_factory.close_startup(
                    claim, startup_key=startup_key,
                    shutdown_key=startup_key + ":startup-abort",
                )
            except NativeStartupRecoveryRequired:
                del self._opening[job_id]
                continue
            except BaseException as exc:
                errors.append(exc)
                continue
            del self._opening[job_id]
            receipts.append(receipt)
        if errors:
            if len(errors) > 1:
                errors[0].native_cleanup_errors = tuple(errors[1:])
            raise errors[0]
        return tuple(receipts)

    def _finish_native_cleanup(self, claim):
        self._close_handle(claim.job_id)
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
            limitations=(
                *statistics.limitations,
                *(("Native training GPU-seconds and USD remain unverified because those cost dimensions are unmetered.",)
                  if self.evidence_scope == "real_integration" else ()),
            ),
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

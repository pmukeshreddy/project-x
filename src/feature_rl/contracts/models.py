"""Strict versioned contracts. Execution and attestation gates belong to services.

Python inputs use exact enum/tuple/datetime types; JSON inputs use their JSON forms.
All nullable measurements are required: absence never silently means zero.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, AfterValidator, model_validator

Text = Annotated[str, Field(min_length=1, pattern=r'\S')]
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Revision = Annotated[str, Field(pattern=r'^(?:[0-9a-f]{40}|[0-9a-f]{64})$')]
Identifier = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')]
NonnegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(gt=0)]
NonnegativeFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError('timestamp must explicitly use UTC')
    return value


UTCDateTime = Annotated[datetime, AfterValidator(utc)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True,
                              allow_inf_nan=False, revalidate_instances='always')


class Visibility(str, Enum):
    PUBLIC = 'public'
    AUTHORING = 'authoring'
    PRIVATE = 'private'
    TRAINING = 'training'
    EVALUATION = 'evaluation'
    INTERNAL = 'internal'


class ActorRole(str, Enum):
    SOLVER = 'solver'
    AUTHOR = 'author'
    CONTROLLER = 'controller'
    TRAINER = 'trainer'
    EVALUATOR = 'evaluator'
    REVIEWER = 'reviewer'


class ActorType(str, Enum):
    HUMAN = 'human'
    MODEL = 'model'
    SERVICE = 'service'


class Partition(str, Enum):
    TRAIN = 'train'
    DEVELOPMENT = 'development'
    LOCKED_TEST = 'locked_test'
    UNASSIGNED = 'unassigned'


class Disposition(str, Enum):
    SUCCESS = 'success'
    REJECTED = 'candidate_rejection'
    UNSUPPORTED = 'unsupported_semantics'
    INVALID = 'invalid_measurement'
    INFRASTRUCTURE = 'infrastructure_failure'
    BLOCKED = 'blocked_dependency'
    PROVISIONAL = 'provisional'


class TaskState(str, Enum):
    DISCOVERED = 'discovered'
    SCREENED = 'screened'
    RECONSTRUCTED = 'reconstructed'
    BUILT = 'built'
    QUALIFIED = 'qualified'
    CALIBRATED = 'calibrated'
    RELEASED = 'released'
    QUARANTINED = 'quarantined'
    REJECTED = 'rejected'


class StopReason(str, Enum):
    SUBMITTED = 'submitted'
    TOKEN_LIMIT = 'token_limit'
    TOOL_LIMIT = 'tool_limit'
    TIME_LIMIT = 'time_limit'
    MALFORMED_ACTION = 'malformed_action'
    CANDIDATE_FAILURE = 'candidate_failure'
    INFRASTRUCTURE_FAILURE = 'infrastructure_failure'
    INVALID_TRAJECTORY = 'invalid_trajectory'


class ArtifactRef(StrictModel):
    sha256: Digest
    kind: Identifier
    schema_version: Annotated[int, Field(ge=1, le=2)]
    visibility: Visibility
    encoding: Literal['json', 'bytes']



EvidenceRefs = Annotated[tuple[ArtifactRef, ...], Field(min_length=1)]


class EvidenceRecord(StrictModel):
    producer: Text
    command: Annotated[tuple[Text, ...], Field(min_length=1)]
    recorded_at: UTCDateTime
    exit_status: int
    artifacts: EvidenceRefs
    revision: Revision
    scope: Literal['real_integration', 'unit_diagnostic', 'source_inspection', 'human_review']


Evidence = Annotated[tuple[EvidenceRecord, ...], Field(min_length=1)]


class CostRecord(StrictModel):
    category: Literal['discovery', 'authoring', 'construction', 'rejection', 'repair', 'reference', 'alternative', 'verifier', 'human_review', 'rollout', 'training', 'execution', 'storage', 'evaluation']
    wall_seconds: NonnegativeFloat | None
    cpu_seconds: NonnegativeFloat | None
    gpu_seconds: NonnegativeFloat | None
    input_tokens: NonnegativeInt | None
    output_tokens: NonnegativeInt | None
    human_minutes: NonnegativeFloat | None
    usd: NonnegativeFloat | None
    measurement: Literal['measured', 'partial', 'unknown']
    note: Text

    @model_validator(mode='after')
    def measurement_consistency(self):
        values = [getattr(self, name) for name in ('wall_seconds','cpu_seconds','gpu_seconds','input_tokens','output_tokens','human_minutes','usd')]
        if self.measurement == 'unknown' and any(v is not None for v in values):
            raise ValueError('unknown cost must contain null measurements')
        if self.measurement == 'measured' and any(v is None for v in values):
            raise ValueError('measured cost requires all measurements; use partial otherwise')
        if self.measurement == 'partial' and not any(v is not None for v in values):
            raise ValueError('partial cost requires at least one measurement')
        return self


Costs = Annotated[tuple[CostRecord, ...], Field(min_length=1)]


class Provenance(StrictModel):
    producer: Text
    producer_version: Text
    created_at: UTCDateTime
    inputs: tuple[ArtifactRef, ...]
    evidence: Evidence


class HumanReview(StrictModel):
    actor_type: Literal['human']
    human_identity: Text
    subject_sha256: Digest
    decision: Literal['approved', 'rejected', 'unresolved']
    evidence: Evidence
    attestation: ArtifactRef
    # This record is a claim. M5 must verify attestation through its external trust gate.


class ResourceLimits(StrictModel):
    wall_seconds: Annotated[float, Field(gt=0)]
    cpu_seconds: Annotated[float, Field(gt=0)]
    memory_bytes: PositiveInt
    pids: PositiveInt
    output_bytes: PositiveInt
    disk_bytes: PositiveInt
    tool_calls: PositiveInt
    input_tokens: PositiveInt
    output_tokens: PositiveInt


class SeedPolicy(StrictModel):
    algorithm: Text
    seeds: Annotated[tuple[NonnegativeInt, ...], Field(min_length=1)]
    same_cases_within_group: bool


class ModelIdentity(StrictModel):
    provider: Text
    model: Text
    revision: Text
    weights: ArtifactRef | None
    tokenizer_digest: Digest | None


class PolicyConfig(StrictModel):
    identity: ModelIdentity
    policy_version: Identifier
    temperature: NonnegativeFloat
    top_p: Probability
    seed: NonnegativeInt
    system_prompt: ArtifactRef
    harness_version: Text
    require_token_probabilities: bool


class TrainingConfig(StrictModel):
    initial_policy: PolicyConfig
    reference_checkpoint: ArtifactRef
    tasks: EvidenceRefs
    limits: ResourceLimits
    seeds: SeedPolicy
    algorithm: Literal['grpo', 'sft']
    group_size: Annotated[int, Field(ge=2)]
    max_updates: PositiveInt
    learning_rate: Annotated[float, Field(gt=0)]
    framework: Text
    framework_version: Text
    backend_version: Text
    budget_usd: NonnegativeFloat | None


class EvaluationArm(StrictModel):
    arm: Literal['A', 'B', 'C', 'D', 'E']
    policy: PolicyConfig
    checkpoint: ArtifactRef
    training_config: ArtifactRef | None


class EvaluationConfig(StrictModel):
    tasks: EvidenceRefs
    arms: Annotated[tuple[EvaluationArm, ...], Field(min_length=1)]
    limits: ResourceLimits
    seeds: SeedPolicy
    partition: Partition
    harness_version: Text
    checkpoint_selection_rule: Text
    invalid_trial_rule: Text
    metric: Literal['pass_at_1', 'best_of_k']
    episodes_per_trial: PositiveInt
    frozen_roster: ArtifactRef
    preregistration: ArtifactRef

    @model_validator(mode='after')
    def metric_contract(self):
        if self.metric == 'pass_at_1' and self.episodes_per_trial != 1:
            raise ValueError('pass_at_1 permits exactly one episode')
        if len({arm.arm for arm in self.arms}) != len(self.arms):
            raise ValueError('duplicate evaluation arm')
        return self


class OperationResult(StrictModel):
    operation: Literal['construct', 'qualify', 'release', 'run', 'grade', 'audit', 'train', 'evaluate']
    disposition: Disposition
    artifacts: tuple[ArtifactRef, ...]
    evidence: tuple[EvidenceRecord, ...]
    costs: Costs
    reason: Text

    @model_validator(mode='after')
    def successful_evidence(self):
        if self.disposition == Disposition.SUCCESS and (not self.evidence or not self.artifacts):
            raise ValueError('success requires output artifacts and evidence')
        if self.disposition == Disposition.SUCCESS and not any(e.exit_status == 0 for e in self.evidence):
            raise ValueError('success requires a successful producer receipt')
        return self


class ArtifactModel(StrictModel):
    kind: Text
    schema_version: Annotated[int, Field(ge=1, le=1)]
    visibility: Visibility
    provenance: Provenance
    costs: Costs



class SourceSnapshot(StrictModel):
    url: Text
    content: ArtifactRef
    retrieved_at: UTCDateTime
    published_at: UTCDateTime | None
    edited_at: UTCDateTime | None
    edit_history: Literal['available', 'unavailable', 'not_applicable']
    media_type: Text
    redirect_chain: Annotated[tuple[Text, ...], Field(min_length=1)] | None

    @model_validator(mode='after')
    def redirect_origin(self):
        if self.redirect_chain is not None and self.redirect_chain[0] != self.url:
            raise ValueError('known redirect chain must begin with the requested URL')
        return self


class LicenseRecord(StrictModel):
    spdx_id: Text | None
    license_text: ArtifactRef | None
    status: Literal['verified', 'unknown', 'ineligible']
    evidence: Evidence


class CommitRelationship(StrictModel):
    integration: Literal['merge', 'squash', 'rebase', 'linear', 'unknown']
    target_before: Revision | None
    integrated_after: Revision | None
    implementation_commits: tuple[Revision, ...]
    parents: tuple[Revision, ...]
    evidence: Evidence


class ScreeningDecision(StrictModel):
    disposition: Disposition
    reason: Text
    evidence: Evidence


class CandidateRecord(ArtifactModel):
    kind: Literal['CandidateRecord']
    schema_version: Annotated[int, Field(ge=2, le=2)]
    provenance_label: Literal['historical_request','reconstructed_specification']
    visibility: Literal[Visibility.AUTHORING, Visibility.EVALUATION, Visibility.PRIVATE]
    repository_url: Text
    repository_family: Identifier
    request_lineage: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    partition: Partition
    sources: Annotated[tuple[SourceSnapshot, ...], Field(min_length=1)]
    license: LicenseRecord
    commits: CommitRelationship
    screening: ScreeningDecision


class ChangedFile(StrictModel):
    path: Text
    category: Literal['implementation','documentation','tests','dependency_build','unrelated','mixed']
    rationale: Text


class SourcePair(ArtifactModel):
    kind: Literal['SourcePair']
    schema_version: Annotated[int, Field(ge=2, le=2)]
    provenance_label: Literal['historical_request','reconstructed_specification']
    visibility: Literal[Visibility.PRIVATE, Visibility.EVALUATION]
    candidate: ArtifactRef
    baseline_commit: Revision
    reference_commit: Revision
    baseline: ArtifactRef
    reference: ArtifactRef
    relationship: CommitRelationship
    changed_files: Annotated[tuple[ChangedFile, ...], Field(min_length=1)]
    admissible_cutoff: UTCDateTime
    verification: Evidence

    @model_validator(mode='after')
    def references_match(self):
        require_ref(self.candidate, 'CandidateRecord')
        if self.reference.visibility not in {Visibility.PRIVATE, Visibility.EVALUATION}:
            raise ValueError('reference implementation requires private or evaluation visibility')
        if self.baseline_commit == self.reference_commit:
            raise ValueError('B and H must differ')
        return self


class EvidenceLink(StrictModel):
    source: ArtifactRef
    locator: Text
    quote: Text
    provenance_label: Literal['historical_request','reconstructed_specification','existing_obligation']


class Requirement(StrictModel):
    requirement_id: Identifier
    statement: Text
    mandatory: bool
    evidence: Annotated[tuple[EvidenceLink, ...], Field(min_length=1)]
    observable: Text


class AmbiguityDecision(StrictModel):
    question: Text
    resolution: Text | None
    disposition: Literal['clarified','excluded','unresolved']
    evidence: Annotated[tuple[EvidenceLink, ...], Field(min_length=1)]


class AllowedChanges(StrictModel):
    source_roots: Annotated[tuple[Text, ...], Field(min_length=1)]
    forbidden_paths: tuple[Text, ...]
    dependencies: Literal['forbidden','pinned_allowlist']
    dependency_artifacts: tuple[ArtifactRef, ...]
    additional_artifact_types: tuple[Text, ...]


class RequirementContract(ArtifactModel):
    kind: Literal['RequirementContract']
    visible_request: Text
    capability: Text
    entry_points: Annotated[tuple[Text, ...], Field(min_length=1)]
    requirements: Annotated[tuple[Requirement, ...], Field(min_length=1)]
    compatibility_obligations: tuple[Requirement, ...]
    ambiguities: tuple[AmbiguityDecision, ...]
    allowed_changes: AllowedChanges
    public_checks: tuple[ArtifactRef, ...]
    episode_limits: ResourceLimits
    provenance_label: Literal['historical_request','reconstructed_specification']

    @model_validator(mode='after')
    def unique_requirements(self):
        unique([r.requirement_id for r in self.requirements+self.compatibility_obligations], 'requirement IDs')
        if not any(r.mandatory for r in self.requirements):
            raise ValueError('contract must have a mandatory feature requirement')
        return self


class Scenario(StrictModel):
    scenario_id: Identifier
    requirement_ids: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    preconditions: Annotated[tuple[Text, ...], Field(min_length=1)]
    actions: Annotated[tuple[Text, ...], Field(min_length=1)]
    observations: Annotated[tuple[Text, ...], Field(min_length=1)]
    expected_relation: Text
    input_domain: Text
    oracle_origin: EvidenceLink
    reset_needs: tuple[Text, ...]


class ScenarioPlan(ArtifactModel):
    kind: Literal['ScenarioPlan']
    visibility: Literal[Visibility.PRIVATE, Visibility.EVALUATION]
    contract: ArtifactRef
    mandatory_requirement_ids: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    scenarios: Annotated[tuple[Scenario, ...], Field(min_length=1)]
    seed_policy: SeedPolicy

    @model_validator(mode='after')
    def coverage(self):
        require_ref(self.contract,'RequirementContract')
        unique([s.scenario_id for s in self.scenarios], 'scenario IDs')
        covered={r for s in self.scenarios for r in s.requirement_ids}
        if not set(self.mandatory_requirement_ids) <= covered:
            raise ValueError('mandatory requirements lack scenarios')
        return self


class CommandSpec(StrictModel):
    argv: Annotated[tuple[Text, ...], Field(min_length=1)]
    working_directory: Text
    timeout_seconds: Annotated[float, Field(gt=0)]


class DependencyPin(StrictModel):
    name: Text
    version: Text
    artifact: ArtifactRef
    sha256: Digest


class EnvironmentVariable(StrictModel):
    name: Identifier
    value: str


class ServiceRecipe(StrictModel):
    name: Identifier
    image_digest: Annotated[str, Field(pattern=r'^.+@sha256:[0-9a-f]{64}$')]
    readiness: CommandSpec
    reset: CommandSpec
    isolated_state: Text


class NeutralRepair(StrictModel):
    description: Text
    patch: ArtifactRef
    neutrality_evidence: Evidence


class EnvironmentRecipe(ArtifactModel):
    kind: Literal['EnvironmentRecipe']
    image_digest: Annotated[str, Field(pattern=r'^.+@sha256:[0-9a-f]{64}$')]
    interpreter_version: Text
    dependencies: tuple[DependencyPin, ...]
    setup: Annotated[tuple[CommandSpec, ...], Field(min_length=1)]
    reset: Annotated[tuple[CommandSpec, ...], Field(min_length=1)]
    services: tuple[ServiceRecipe, ...]
    limits: ResourceLimits
    neutral_repairs: tuple[NeutralRepair, ...]
    locale: Text
    timezone: Text
    environment: tuple[EnvironmentVariable, ...]
    randomness: SeedPolicy
    network_policy: Literal['none','declared_local_services']
    baseline: ArtifactRef


class CaseDefinition(StrictModel):
    case_id: Identifier
    requirement_ids: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    inputs: ArtifactRef
    comparison: ArtifactRef
    mandatory: bool


class ControlPatch(StrictModel):
    control_id: Identifier
    category: Literal['noop','omission','plausible_wrong','hardcoded','regression','adversarial','alternative_positive']
    patch: ArtifactRef
    requirement_ids: tuple[Identifier, ...]
    expected_valid: bool
    expected_reason: Text
    author_provenance: Provenance


class WorkerAdapter(StrictModel):
    code: ArtifactRef
    version: Text
    supported_observables: Annotated[tuple[Text, ...], Field(min_length=1)]
    limitations: tuple[Text, ...]


class VerifierPermissions(StrictModel):
    controller_role: Literal[ActorRole.CONTROLLER, ActorRole.EVALUATOR]
    worker_inputs: tuple[ArtifactRef, ...]
    output_limit_bytes: PositiveInt
    submission_policy: AllowedChanges


class VerifierBundle(ArtifactModel):
    kind: Literal['VerifierBundle']
    visibility: Literal[Visibility.PRIVATE, Visibility.EVALUATION]
    contract: ArtifactRef
    scenario_plan: ArtifactRef
    cases: Annotated[tuple[CaseDefinition, ...], Field(min_length=1)]
    completion_manifest: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    worker_adapter: WorkerAdapter
    public_examples: tuple[ArtifactRef, ...]
    controls: tuple[ControlPatch, ...]
    permissions: VerifierPermissions

    @model_validator(mode='after')
    def manifest_matches(self):
        require_ref(self.contract,'RequirementContract'); require_ref(self.scenario_plan,'ScenarioPlan')
        unique([c.case_id for c in self.cases], 'case IDs')
        unique(self.completion_manifest,'completion IDs')
        if set(self.completion_manifest) != {c.case_id for c in self.cases}:
            raise ValueError('completion manifest must list every case exactly once')
        return self


class SolverView(StrictModel):
    instruction: ArtifactRef
    workspace: ArtifactRef
    public_checks: tuple[ArtifactRef, ...]
    runtime_manifest: ArtifactRef
    inventory: ArtifactRef

    @model_validator(mode='after')
    def only_public(self):
        if any(r.visibility != Visibility.PUBLIC for r in (self.instruction,self.workspace,self.runtime_manifest,self.inventory)+self.public_checks):
            raise ValueError('solver view requires public artifacts exclusively')
        return self


class TaskBundle(ArtifactModel):
    kind: Literal['TaskBundle']
    visibility: Literal[Visibility.PRIVATE, Visibility.EVALUATION]
    state: TaskState
    partition: Partition
    repository_family: Identifier
    request_lineage: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    source_pair: ArtifactRef
    baseline: ArtifactRef
    solver_view: SolverView
    contract: ArtifactRef
    environment: ArtifactRef
    adapter_version: Text
    private_oracle: ArtifactRef
    reference_solution: ArtifactRef
    qualification: ArtifactRef | None

    @model_validator(mode='after')
    def task_references(self):
        for ref,kind in ((self.source_pair,'SourcePair'),(self.contract,'RequirementContract'),(self.environment,'EnvironmentRecipe'),(self.private_oracle,'VerifierBundle')):
            require_ref(ref,kind)
        if any(ref.visibility not in {Visibility.PRIVATE, Visibility.EVALUATION}
               for ref in (self.private_oracle, self.reference_solution)):
            raise ValueError('oracle/reference requires private or evaluation visibility')
        if self.qualification is not None: require_ref(self.qualification,'QualificationReport')
        if self.state in {TaskState.QUALIFIED,TaskState.RELEASED,TaskState.CALIBRATED} and self.qualification is None:
            raise ValueError('admitted states require qualification reference')
        return self


class RunAssessment(StrictModel):
    name: Identifier
    subject: ArtifactRef
    disposition: Disposition
    passed: bool | None
    requirement_ids: tuple[Identifier, ...]
    reason: Text
    evidence: Evidence


class QualificationReport(ArtifactModel):
    kind: Literal['QualificationReport']
    visibility: Literal[Visibility.PRIVATE, Visibility.EVALUATION]
    task: ArtifactRef
    disposition: Disposition
    baseline_health: RunAssessment | None
    baseline_absence: RunAssessment | None
    reference_run: RunAssessment | None
    controls: tuple[RunAssessment, ...]
    fresh_runs: tuple[RunAssessment, ...]
    interrupted_reset_runs: tuple[RunAssessment, ...]
    human_reviews: tuple[HumanReview, ...]
    rejection_reasons: tuple[Text, ...]
    repair_attempts: NonnegativeInt
    policy_version: Text

    @model_validator(mode='after')
    def successful_report(self):
        require_ref(self.task,'TaskBundle')
        if self.repair_attempts > 4: raise ValueError('pilot repair budget exceeded')
        if self.disposition == Disposition.SUCCESS:
            if any(x is None for x in (self.baseline_health,self.baseline_absence,self.reference_run)) or not self.controls or len(self.fresh_runs)<3 or len(self.interrupted_reset_runs)<3 or not self.human_reviews:
                raise ValueError('successful qualification requires all gate records')
            gates = (self.baseline_health,self.baseline_absence,self.reference_run)+self.controls+self.fresh_runs+self.interrupted_reset_runs
            if any(g.disposition != Disposition.SUCCESS or g.passed is not True for g in gates):
                raise ValueError('successful qualification cannot contain failed gates')
            if any(not any(e.scope == 'real_integration' for e in g.evidence) for g in gates):
                raise ValueError('qualification gates require real integration evidence')
            if any(r.decision != 'approved' or r.subject_sha256 != self.task.sha256 for r in self.human_reviews):
                raise ValueError('human review must approve the exact task version')
            if self.rejection_reasons:
                raise ValueError('successful qualification cannot contain rejection reasons')
        return self


class TokenTrace(StrictModel):
    context_token_ids: tuple[NonnegativeInt, ...]
    sampled_token_ids: Annotated[tuple[NonnegativeInt, ...], Field(min_length=1)]
    behavior_log_probabilities: tuple[float, ...]
    assistant_loss_mask: tuple[bool, ...]
    policy_version: Identifier

    @model_validator(mode='after')
    def aligned(self):
        if not (len(self.sampled_token_ids)==len(self.behavior_log_probabilities)==len(self.assistant_loss_mask)):
            raise ValueError('token/log-probability/mask lengths differ')
        if any(p>0 for p in self.behavior_log_probabilities):
            raise ValueError('log probabilities must be <= 0')
        return self


class EpisodeStep(StrictModel):
    index: NonnegativeInt
    action: ArtifactRef
    observation: ArtifactRef
    token_trace: TokenTrace | None
    evidence: Evidence


class RolloutRecord(ArtifactModel):
    kind: Literal['RolloutRecord']
    visibility: Literal[Visibility.TRAINING, Visibility.EVALUATION, Visibility.PRIVATE]
    run_id: Identifier
    task: ArtifactRef
    policy: PolicyConfig
    limits: ResourceLimits
    seeds: SeedPolicy
    steps: tuple[EpisodeStep, ...]
    submission: ArtifactRef | None
    stopping_reason: StopReason
    disposition: Disposition
    reward: Annotated[int, Field(ge=0, le=1)] | None
    grading_evidence: tuple[EvidenceRecord, ...]
    training_eligible: bool

    @model_validator(mode='after')
    def honest_rollout(self):
        require_ref(self.task,'TaskBundle')
        stop_dispositions = {
            StopReason.INVALID_TRAJECTORY: Disposition.INVALID,
            StopReason.INFRASTRUCTURE_FAILURE: Disposition.INFRASTRUCTURE,
        }
        expected = stop_dispositions.get(self.stopping_reason)
        if expected is not None:
            if self.disposition != expected:
                raise ValueError(f'{self.stopping_reason.value} stop requires {expected.value} disposition')
            if self.training_eligible:
                raise ValueError('invalid or infrastructure stop cannot be training eligible')
        # This stop denotes a terminal candidate build/import/worker grading failure.
        # Ordinary agent limits and invalid commands still grade the saved source.
        if self.stopping_reason == StopReason.CANDIDATE_FAILURE and self.reward not in (None, 0):
            raise ValueError('a measured terminal candidate failure requires zero reward')
        if self.reward is not None and self.disposition not in {Disposition.SUCCESS, Disposition.REJECTED}:
            raise ValueError('only valid measured outcomes can carry reward')
        if self.reward is not None and type(self.reward) is not int:
            raise ValueError('reward must be integer 0 or 1')
        if self.reward is not None and not self.grading_evidence:
            raise ValueError('measured reward requires grading evidence')
        if self.disposition in {Disposition.INFRASTRUCTURE,Disposition.INVALID,Disposition.BLOCKED} and self.reward is not None:
            raise ValueError('invalid measurement cannot carry reward')
        if self.training_eligible and (self.reward is None or not self.steps or any(s.token_trace is None for s in self.steps)):
            raise ValueError('training requires measured reward and exact token traces')
        if self.training_eligible:
            if self.policy.identity.tokenizer_digest is None:
                raise ValueError('training requires an identified tokenizer')
            if any(s.token_trace.policy_version != self.policy.policy_version for s in self.steps):
                raise ValueError('token traces must use the episode behavior policy')
        return self


class TrainingCheckpoint(ArtifactModel):
    kind: Literal['TrainingCheckpoint']
    visibility: Literal[Visibility.TRAINING, Visibility.PRIVATE]
    weights: ArtifactRef
    optimizer_state: ArtifactRef
    reference_checkpoint: ArtifactRef
    data_position: NonnegativeInt
    policy_version: Identifier
    configuration: TrainingConfig
    consumed_tasks: EvidenceRefs
    optimizer_steps: NonnegativeInt
    update_evidence: Evidence
    reload_evidence: Evidence


class TrialResult(StrictModel):
    trial_id: Identifier
    task: ArtifactRef
    repository_family: Identifier
    arm: Literal['A','B','C','D','E']
    policy_seed: NonnegativeInt
    case_seed: NonnegativeInt
    rollout: ArtifactRef | None
    disposition: Disposition
    resolved: bool | None
    evidence: Evidence

    @model_validator(mode='after')
    def coherent_outcome(self):
        require_ref(self.task, 'TaskBundle')
        if self.rollout is not None:
            require_ref(self.rollout, 'RolloutRecord')
        if self.disposition in {Disposition.SUCCESS, Disposition.REJECTED}:
            if self.resolved is None or self.rollout is None:
                raise ValueError('measured trial requires resolved outcome and rollout')
            if self.disposition == Disposition.REJECTED and self.resolved:
                raise ValueError('rejected trial cannot be resolved')
        elif self.resolved is not None:
            raise ValueError('unmeasured or invalid trial must have null resolved outcome')
        return self


class MetricEstimate(StrictModel):
    name: Text
    estimate: float | None
    lower: float | None
    upper: float | None
    sample_size: NonnegativeInt
    method: Text
    limitations: tuple[Text, ...]

    @model_validator(mode='after')
    def bounds(self):
        if self.sample_size == 0 and any(x is not None for x in (self.estimate,self.lower,self.upper)):
            raise ValueError('zero samples cannot yield measured estimate')
        if self.lower is not None and self.upper is not None and self.lower>self.upper:
            raise ValueError('reversed uncertainty interval')
        return self


class AuditRecord(StrictModel):
    submission: ArtifactRef
    task: ArtifactRef
    verifier: ArtifactRef
    sampling_probability: Annotated[float, Field(gt=0,le=1)]
    sampling_kind: Literal['random','targeted']
    verdict: Literal['valid','invalid','unresolved']
    human_review: HumanReview | None
    evidence: Evidence


class EvaluationReport(ArtifactModel):
    kind: Literal['EvaluationReport']
    visibility: Literal[Visibility.EVALUATION, Visibility.PRIVATE]
    configuration: EvaluationConfig
    frozen_task_roster: EvidenceRefs
    trials: tuple[TrialResult, ...]
    paired_metrics: tuple[MetricEstimate, ...]
    audits: tuple[AuditRecord, ...]
    disposition: Disposition
    limitations: tuple[Text, ...]

    @model_validator(mode='after')
    def successful_report(self):
        if self.disposition == Disposition.SUCCESS:
            measured_trials = any(t.resolved is not None for t in self.trials)
            measured_metrics = any(m.sample_size > 0 and m.estimate is not None
                                   for m in self.paired_metrics)
            if not measured_trials or not measured_metrics:
                raise ValueError('evaluation success needs measured trials and measured metrics')
        unique([t.trial_id for t in self.trials], 'trial IDs')
        return self


def unique(values, label):
    if len(values) != len(set(values)): raise ValueError(f'duplicate {label}')


def require_ref(ref: ArtifactRef, kind: str):
    if ref.kind != kind or ref.encoding != 'json':
        raise ValueError(f'expected JSON {kind} reference')
    if ref.schema_version != ARTIFACT_SCHEMA_VERSIONS[kind]:
        raise ValueError(f'unsupported {kind} schema version; revalidate and republish')


ARTIFACT_TYPES = {cls.__name__: cls for cls in (
    CandidateRecord, SourcePair, RequirementContract, ScenarioPlan, EnvironmentRecipe,
    VerifierBundle, TaskBundle, QualificationReport, RolloutRecord, TrainingCheckpoint,
    EvaluationReport,
)}

ARTIFACT_SCHEMA_VERSIONS = {kind: 2 if kind in {'CandidateRecord', 'SourcePair'} else 1
                            for kind in ARTIFACT_TYPES}


class ConstructRequest(StrictModel):
    candidate: ArtifactRef

    @model_validator(mode='after')
    def kind_check(self):
        require_ref(self.candidate, 'CandidateRecord')
        return self


class TaskRequest(StrictModel):
    task_version: ArtifactRef

    @model_validator(mode='after')
    def kind_check(self):
        require_ref(self.task_version, 'TaskBundle')
        return self


class QualifyRequest(TaskRequest):
    """Request for qualification of an exact immutable task version."""


class ReleaseRequest(TaskRequest):
    """Request for release of an exact immutable task version."""


class RunRequest(TaskRequest):
    policy: PolicyConfig
    limits: ResourceLimits
    # None delegates ordinary case selection to policy.seed in AgentRunner.run.
    case_seed: NonnegativeInt | None = None


class GradeRequest(TaskRequest):
    submission: ArtifactRef
    case_seed: NonnegativeInt


class AuditRequest(StrictModel):
    run_ids: Annotated[tuple[Identifier, ...], Field(min_length=1)]


class TrainRequest(StrictModel):
    config: TrainingConfig


class EvaluateRequest(StrictModel):
    config: EvaluationConfig

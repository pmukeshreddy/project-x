"""Frozen study and adaptation records for M8 evaluation."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from feature_rl import contracts as c


ArmName = Literal["A", "B", "C", "D", "E"]
RelationKind = Literal[
    "fork", "backport", "copied_code", "monorepo", "descendant",
    "same_request", "dependency",
]


class SourceAssignment(c.StrictModel):
    source_id: c.Identifier
    task: c.ArtifactRef
    repository_family: c.Identifier
    request_lineage: Annotated[tuple[c.Identifier, ...], Field(min_length=1)]
    partition: c.Partition
    evidence: c.ArtifactRef

    @model_validator(mode="after")
    def kinds(self):
        if self.task.kind != "TaskBundle":
            raise ValueError("source task must reference TaskBundle")
        return self


class LineageRelation(c.StrictModel):
    left: c.Identifier
    right: c.Identifier
    kind: RelationKind
    evidence: c.ArtifactRef

    @model_validator(mode="after")
    def distinct_endpoints(self):
        if self.left == self.right:
            raise ValueError("lineage relation endpoints must differ")
        return self


class FrozenRoster(c.StrictModel):
    version: Literal["m8-frozen-roster-v1"]
    locked_tasks: Annotated[tuple[c.ArtifactRef, ...], Field(min_length=1)]
    sources: Annotated[tuple[SourceAssignment, ...], Field(min_length=1)]
    relations: tuple[LineageRelation, ...]
    test_source_frame: c.ArtifactRef
    exclusions: c.ArtifactRef
    created_at: c.UTCDateTime

    @model_validator(mode="after")
    def uniqueness(self):
        if any(item.kind != "TaskBundle" for item in self.locked_tasks):
            raise ValueError("locked tasks must reference TaskBundle")
        source_ids = [item.source_id for item in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("duplicate roster source ID")
        task_ids = [item.task.sha256 for item in self.sources]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("duplicate roster task")
        return self


class BudgetLimit(c.StrictModel):
    max_updates: c.NonnegativeInt
    max_rollouts: c.NonnegativeInt
    max_assistant_tokens: c.NonnegativeInt
    gpu_seconds: c.NonnegativeFloat
    usd: c.NonnegativeFloat | None


class ArmProtocol(c.StrictModel):
    arm: ArmName
    method: Literal["starting", "sft", "external_rl", "factory_rl", "ablation"]
    policy: c.PolicyConfig
    checkpoint: c.ArtifactRef
    training_config: c.ArtifactRef | None
    initial_checkpoint: c.ArtifactRef
    tools: c.ArtifactRef
    action_format: c.Identifier
    optimizer_family: c.Identifier
    harness_version: c.Text
    training_budget: BudgetLimit | None
    development_budget: BudgetLimit


class TrialAssignment(c.StrictModel):
    trial_id: c.Identifier
    task: c.ArtifactRef
    arm: ArmName
    policy_seed: c.NonnegativeInt
    case_seed: Annotated[int, Field(ge=0, le=2**63 - 1)]
    episode_index: c.NonnegativeInt

    @model_validator(mode="after")
    def task_kind(self):
        if self.task.kind != "TaskBundle":
            raise ValueError("trial task must reference TaskBundle")
        return self


class EvaluationPreregistration(c.StrictModel):
    version: Literal["m8-preregistration-v1"]
    arms: Annotated[tuple[ArmProtocol, ...], Field(min_length=1)]
    trials: Annotated[tuple[TrialAssignment, ...], Field(min_length=1)]
    limits: c.ResourceLimits
    seeds: c.SeedPolicy
    metric: Literal["pass_at_1", "best_of_k"]
    episodes_per_trial: c.PositiveInt
    harness_version: c.Text
    checkpoint_selection_rule: c.Text
    invalid_trial_rule: c.Text
    locked_test_access_rule: c.Text
    comparisons: Annotated[tuple[tuple[ArmName, ArmName], ...], Field(min_length=1)]
    created_at: c.UTCDateTime

    @model_validator(mode="after")
    def basic_uniqueness(self):
        if len({arm.arm for arm in self.arms}) != len(self.arms):
            raise ValueError("duplicate preregistered arm")
        if len({trial.trial_id for trial in self.trials}) != len(self.trials):
            raise ValueError("duplicate preregistered trial ID")
        if self.metric == "pass_at_1" and self.episodes_per_trial != 1:
            raise ValueError("pass_at_1 permits exactly one episode")
        return self


class AdaptationStage(c.StrictModel):
    name: c.Identifier
    entered: c.NonnegativeInt
    accepted: c.NonnegativeInt
    rejected: c.NonnegativeInt
    invalid: c.NonnegativeInt
    reasons: tuple[c.Text, ...]

    @model_validator(mode="after")
    def accounts_for_entries(self):
        if self.entered != self.accepted + self.rejected + self.invalid:
            raise ValueError("adaptation stage must account for every entered item")
        if (self.rejected or self.invalid) and not self.reasons:
            raise ValueError("adaptation losses require recorded reasons")
        return self


class AdaptationFunnel(c.StrictModel):
    version: Literal["m8-adaptation-funnel-v1"]
    corpus_id: c.Text
    release_revision: c.Revision
    upstream_split: c.Text
    local_partition: c.Partition
    license_constraint: c.Text
    source_frame: c.ArtifactRef
    stages: Annotated[tuple[AdaptationStage, ...], Field(min_length=1)]
    selection_bias: tuple[c.Text, ...]
    costs: c.Costs
    disposition: c.Disposition

    @model_validator(mode="after")
    def stage_continuity(self):
        for before, after in zip(self.stages, self.stages[1:]):
            if after.entered != before.accepted:
                raise ValueError("adaptation stages must account continuously for accepted items")
        return self

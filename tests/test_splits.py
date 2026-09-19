"""Partition closure tests guard against related-source leakage."""
from __future__ import annotations

import pytest


def test_fork_backport_copy_and_descendant_relations_close_transitively():
    """Catches assigning only the named request while related sources remain unassigned."""
    from feature_rl.contracts import Partition
    from feature_rl.splits import Relation, SplitPlanner

    relations = (
        Relation("upstream", "fork", "fork", "fork metadata"),
        Relation("fork", "pr-3228", "descendant", "PR head repository"),
        Relation("pr-3228", "issue-3107", "same_request", "closing issue"),
        Relation("issue-3107", "backport", "backport", "backport evidence"),
        Relation("backport", "copied-feature", "copied_code", "matching code proof"),
        Relation("copied-feature", "monorepo-package", "monorepo", "shared tree"),
    )

    manifest = SplitPlanner(relations).assign({"pr-3228": Partition.TRAIN})

    assert {item.source_id: item.partition for item in manifest.assignments} == {
        "backport": Partition.TRAIN,
        "copied-feature": Partition.TRAIN,
        "fork": Partition.TRAIN,
        "issue-3107": Partition.TRAIN,
        "monorepo-package": Partition.TRAIN,
        "pr-3228": Partition.TRAIN,
        "upstream": Partition.TRAIN,
    }
    assert len({item.component_id for item in manifest.assignments}) == 1


def test_conflicting_partition_assignments_are_rejected():
    """Catches silently selecting one partition after a shared descendant joins groups."""
    from feature_rl.contracts import Partition
    from feature_rl.splits import Relation, SplitConflict, SplitPlanner

    planner = SplitPlanner(
        (
            Relation("train-source", "shared", "descendant", "derived request"),
            Relation("shared", "test-source", "copied_code", "matching patch"),
        )
    )
    with pytest.raises(SplitConflict, match="locked_test.*train|train.*locked_test"):
        planner.assign(
            {
                "train-source": Partition.TRAIN,
                "test-source": Partition.LOCKED_TEST,
            }
        )


def test_partition_closure_is_deterministic_and_idempotent():
    """Catches order-dependent component IDs or duplicate-assignment drift."""
    from feature_rl.contracts import Partition
    from feature_rl.splits import Relation, SplitPlanner

    forward = (
        Relation("a", "b", "fork", "fork proof"),
        Relation("b", "c", "backport", "backport proof"),
    )
    reverse = tuple(reversed(forward))
    requested = {"a": Partition.DEVELOPMENT}

    first = SplitPlanner(forward).assign(requested)
    second = SplitPlanner(forward).assign(requested)
    reordered = SplitPlanner(reverse).assign(requested)

    assert first == second == reordered
    assert first.relations == tuple(sorted(forward))


def test_declared_dependency_requires_review_without_claiming_independence():
    """Catches treating a dependency as proof either of sameness or independence."""
    from feature_rl.contracts import Partition
    from feature_rl.splits import Relation, SplitPlanner

    dependency = Relation("rq", "click", "dependency", "RQ declares Click dependency")
    manifest = SplitPlanner((dependency,)).assign(
        {"rq": Partition.LOCKED_TEST, "click": Partition.TRAIN}
    )

    assert manifest.review_required == (dependency,)
    assert len({item.component_id for item in manifest.assignments}) == 2

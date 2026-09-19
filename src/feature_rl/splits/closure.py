"""Deterministic relation closure for leakage-safe source partitions."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Literal, Mapping

from feature_rl.contracts import Partition


RelationKind = Literal[
    "fork",
    "backport",
    "copied_code",
    "monorepo",
    "descendant",
    "same_request",
    "dependency",
]

_SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_CLOSING_RELATIONS = frozenset(
    {"fork", "backport", "copied_code", "monorepo", "descendant", "same_request"}
)


class SplitConflict(ValueError):
    """One related component was explicitly assigned to multiple partitions."""


@dataclass(frozen=True, order=True)
class Relation:
    left: str
    right: str
    kind: RelationKind
    proof: str

    def __post_init__(self):
        if not _SOURCE_ID.fullmatch(self.left) or not _SOURCE_ID.fullmatch(self.right):
            raise ValueError("relation endpoints must be safe source identifiers")
        if self.left == self.right:
            raise ValueError("relation endpoints must differ")
        if self.kind not in _CLOSING_RELATIONS | {"dependency"}:
            raise ValueError("unsupported source relation")
        if not self.proof or not self.proof.strip():
            raise ValueError("relation requires recorded proof")


@dataclass(frozen=True, order=True)
class SplitAssignment:
    source_id: str
    partition: Partition
    component_id: str


@dataclass(frozen=True)
class PartitionManifest:
    assignments: tuple[SplitAssignment, ...]
    relations: tuple[Relation, ...]
    review_required: tuple[Relation, ...]


class SplitPlanner:
    """Pure closure planner; identical inputs produce identical manifests."""

    def __init__(self, relations: tuple[Relation, ...]):
        if any(not isinstance(item, Relation) for item in relations):
            raise TypeError("relations must contain Relation values")
        if len(set(relations)) != len(relations):
            raise ValueError("duplicate source relation")
        self.relations = tuple(relations)

    @staticmethod
    def _component_id(members: tuple[str, ...]) -> str:
        digest = hashlib.sha256("\0".join(members).encode("utf-8")).hexdigest()
        return f"component-{digest}"

    def assign(self, requested: Mapping[str, Partition]) -> PartitionManifest:
        for source_id, partition in requested.items():
            if not _SOURCE_ID.fullmatch(source_id):
                raise ValueError("assignment source must be a safe identifier")
            if not isinstance(partition, Partition):
                raise TypeError("partition assignments must use Partition values")

        nodes = set(requested)
        for relation in self.relations:
            nodes.update((relation.left, relation.right))
        parent = {node: node for node in nodes}

        def find(node: str) -> str:
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def union(left: str, right: str) -> None:
            left_root, right_root = find(left), find(right)
            if left_root == right_root:
                return
            lower, higher = sorted((left_root, right_root))
            parent[higher] = lower

        for relation in sorted(self.relations):
            if relation.kind in _CLOSING_RELATIONS:
                union(relation.left, relation.right)

        components: dict[str, list[str]] = {}
        for node in sorted(nodes):
            components.setdefault(find(node), []).append(node)

        assignments: list[SplitAssignment] = []
        for members_list in components.values():
            members = tuple(sorted(members_list))
            explicit = {requested[item] for item in members if item in requested}
            if len(explicit) > 1:
                labels = ", ".join(sorted(item.value for item in explicit))
                raise SplitConflict(
                    f"related component {members!r} has conflicting partitions: {labels}"
                )
            partition = next(iter(explicit), Partition.UNASSIGNED)
            component_id = self._component_id(members)
            assignments.extend(
                SplitAssignment(item, partition, component_id) for item in members
            )

        review = tuple(sorted(item for item in self.relations if item.kind == "dependency"))
        return PartitionManifest(
            tuple(sorted(assignments)), tuple(sorted(self.relations)), review
        )

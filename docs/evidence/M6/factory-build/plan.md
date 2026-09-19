# Frozen BUILT task implementation plan

Goal: implement the approved M6 packaging boundary over reviewed M0/M3 and registry
APIs, with a complete frozen solver package before M5 qualification. Root approved
this boundary on 2026-09-19; no additional approval or service API is inferred.
Authority: `docs/briefs/M6.md`, `docs/lifecycle-admission.md`, specification §10,
and the current scoped dispatch. Execution is inline in exclusive pipeline paths;
root owns independent review. No subagents, CLI placeholders, service substitutes,
Docker/model calls, shared configuration edits, or broad suite runs.

Architecture: `pipeline/models.py` defines the closed build input/receipt/inventory
records. `pipeline/packaging.py` resolves actual M0 artifacts and reviewed M3 source
archives, validates joins and the pinned runtime policy, and builds an explicit
public projection. `pipeline/build.py` records a registry attempt before assembly,
reconciles a frozen receipt before T0 publication, and resumes exact publication.
No new durable outbox is introduced. Public import surface is `pipeline/__init__.py`.

Build inputs name exact source-pair, contract, scenario, verifier and prepared
environment references, an explicit complete B file allowlist, and an invocation
identity. Unknown costs are recorded before assembly. A failed prerequisite yields
a typed failed OperationResult and durable accounting rather than a BUILT artifact.
If storage itself fails, preserve pending receipt bytes/claim for publication retry;
an interruption before the freeze remains unknown until explicit reconciliation.

The public archive contains only instruction.md, workspace/B files, declared public
checks and runtime_manifest.json. Its separately frozen inventory binds every path,
byte digest, size and executable bit plus the archive/component refs. T0 binds the
complete SolverView and archive. Private source joins, H, oracle, adapter, expected
values and provenance are never copied into these public objects.

- [x] Write dedicated `tests/test_factory.py` diagnostic fixtures and failing
  packaging, exact-lineage/role/policy, leakage/path, missing-prerequisite and
  pre/post-freeze publication recovery tests. Run only this file; expect missing
  production API failures and preserve the actual red receipt.
- [x] Implement the four owned pipeline files using bounded CAS reads, M3 inert
  archive types and registry APIs. No source or public-check code executes here.
- [x] Run focused factory plus registry tests. Verify deterministic frozen-root
  replay, separate cost-bearing attempts, preserved unknown costs, corrupt/missing
  artifact rejection and downstream quarantine tracing.
- [x] Record exact source hashes/commands/statuses and limitations, publish the
  usable interface, commit explicit owned paths and return FINAL for review.

Review focus: fields accidentally copied from private manifests; unsafe source
members even when explicitly allowlisted; cross-source/runtime/policy drift;
publication failure after a committed registry observation; stale/unknown claims
and duplicate completion; missing real authoring inputs remaining a failure.
Tests are diagnostic only and cannot substitute for the exhausted real Click
authoring run, absent contract/scenario, qualification, release or an RL result.

Execution: 14 initial failures on the absent API; 14 passes after implementation.
Added actual process-crash and changed-root/H-lineage checks; the final focused
factory/registry run passed 54 tests. Exact source hashes, command/status and raw
output are preserved in this directory. Root coordinates independent review.

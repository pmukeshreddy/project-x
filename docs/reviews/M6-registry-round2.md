# M6 registry core — independent round 2 review

**Specification compliance: PASS. Implementation quality: PASS.**

Both round 1 findings are closed at
`fa6a1b884365a8f89d37beaf6bc180b945d088fa` relative to base
`9497b636548545d9fd54a1f06f1432134dc14cd6`. Open scoped findings:
**P0: 0, P1: 0, P2: 0**. The registry core is ready for coordinator integration.
This verdict does not mark full M6 orchestration/CLI or feature execution complete.

## Finding closure

**P1 — operation evidence quarantine: closed.**
[state.py:260](../../src/feature_rl/registry/state.py#L260) now checks all selected
`OperationResult.evidence[*].artifacts` references before initial completion.
[state.py:112](../../src/feature_rl/registry/state.py#L112) includes those immutable
references in completed-job dependency traversal, propagating later quarantine
through outputs, consuming jobs and typed RolloutRecord/TrainingCheckpoint
dependencies. No artifact declaration is rewritten.

The two focused regressions verify rejection without a new event or selected
result, preservation of incurred accounting and abandonment, and later quarantine
of actual typed task/run/checkpoint descendants. They also verify unchanged
historical completion replay after quarantine and a later cost revision. Replay
uses the existing exact semantic event match; late reconciliation retains its
separate bookkeeping path. The correction adds no task-admission shortcut.

**P2 — unreadable accepted materialized growth: closed.**
[storage.py:180](../../src/feature_rl/registry/storage.py#L180) centralizes the same
record count, total-byte and per-record bounds for reads and writes.
[storage.py:250](../../src/feature_rl/registry/storage.py#L250) serializes and checks
the entire prospective record set before its first INSERT/DELETE. A rejected
mutation therefore cannot commit its event or materialized changes; the surrounding
session rolls back its pending transaction.

The growth regression reaches the original 2,200-byte bound with small individual
events, then compares exact SQLite events, records, acknowledgement and JSONL bytes
with the prior state. It verifies recovery, job/attempt/accounting readback, enqueue
replay and late reconciliation of an earlier abandoned attempt remain available.
The limits and public signatures were not widened.

## Source-bound verification

Review stayed within the two fixes, dedicated tests, interface/report delta and
their recorded evidence. The supplied aggregate diff contains unrelated M2/root
changes; those were excluded. No product/index/commit edits, subagents, suite
reruns, additional diagnostics, Docker, native execution or network work occurred.

Fresh read-only verification confirmed all three changed source/test files match
the exact target and final test receipt. The receipt's ten before/after source
hashes agree. The RED hashes for the two changed product files match the original
base, and the RED test source hash equals the final GREEN test source hash. Raw
RED/GREEN output hashes and all five fix-evidence files match the committed target.
See [review verification](../evidence/M6/review-registry-round2/verification.json).

The owner evidence records **3 expected failures** against the original code,
an intermediate **2 evidence-regression passes**, and final **36 focused tests
passed, exit 0**, including the existing process-crash/concurrency checks. Final
execution was **2026-09-19 21:34:09.279751–21:34:15.979648 UTC**, pytest **6.51 s**,
outer **6.6997825 s**. These results were inspected and source-bound, not rerun or
represented as independent fresh execution. The owner's
[verification receipt](../evidence/M6/registry-fix1/verification.json) has SHA-256
`4685155b5f31de8d19bb60c2b8387a5d392fbf5dd42ca4a2cb85e3966e1fa07e`.

Existing histories that already contain the invalid completion or oversized index
remain fail-closed; automatic migration or repair is neither implemented nor
required for this correction. Historical completion before later quarantine stays
readable. Local synthetic/process-crash evidence does not establish physical
power-loss durability, external exactly-once execution or real feature success.
Root owns the next stable integration check; actual orchestration, lifecycle
admission and deferred experimental execution retain their separate status.

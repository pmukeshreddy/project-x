# M6 registry round 1 corrections

Status: both scoped review findings corrected; independent re-review pending.
Reviewed product base: `9497b636548545d9fd54a1f06f1432134dc14cd6`.
Input: [round 1 review](../reviews/M6-registry-round1.md) and its actual
[diagnostic receipt](../evidence/M6/review-registry-round1/receipt.json),
[stdout](../evidence/M6/review-registry-round1/stdout.txt) and
[reproducer](../evidence/M6/review-registry-round1/reproduce.py).

## Corrections

- **P1 — selected result evidence.** Initial completion now checks every
  `OperationResult.evidence[*].artifacts` reference against quarantine along with
  job inputs, configuration and outputs. The selected evidence also contributes
  job dependency edges, so later quarantine reaches outputs, consuming jobs and
  actual typed RolloutRecord/TrainingCheckpoint descendants. Completion replay
  still returns the immutable historical result after later quarantine and cost
  revisions. Abandonment and late accounting remain available; rejected completion
  does not erase incurred observations or append an event.
- **P2 — materialized bounds.** Persistence validates the entire prospective
  materialized index before its first SQL write, using the same count, total-byte
  and largest-record checks as recovery. Oversized growth raises `RegistryLimit`
  without adding an event or changing stored rows. The existing 2,200-byte event
  setting also bounds each record; no limit or schema was changed.

Public signatures remain unchanged. The [interface](../interfaces-M6-registry.md)
now states these dependency and rejection semantics. Only `state.py`, `storage.py`
and dedicated registry tests changed in product/test code.

## Actual verification

Three regressions first reproduced the defects against the unchanged product:
quarantined evidence was accepted, its later descendants were absent, and a
2,250-byte materialized record was accepted with a 2,200-byte limit. The
[red output](../evidence/M6/registry-fix1/red.txt) and
[red receipt](../evidence/M6/registry-fix1/red.json) preserve exit 1 and source hashes.
The two evidence regressions then passed after the P1 correction; the intermediate
[output](../evidence/M6/registry-fix1/evidence-green.txt) is retained (exit 0).

Final command:

```text
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_registry.py tests/test_registry_durability.py
```

**36 passed, exit 0**, pytest 6.51 seconds; observer elapsed 6.6997825 seconds.
Actual execution was 2026-09-19 21:34:09.279751 through 21:34:15.979648 UTC.
The [raw output](../evidence/M6/registry-fix1/focused-green.txt) and
[verification receipt](../evidence/M6/registry-fix1/verification.json) bind the
command, environment, observed HEAD `0cb1d293907da0325081abaaea4006ffad0a1b5e`
and before/after SHA-256 values for all five registry modules, both registry test
files, the reused typed fixture source and the actual M0 store/models. All captured
source hashes matched before and after this run. The M6 fixes were working-tree
changes at execution; the observed HEAD alone does not identify their bytes or
claim that unrelated live paths remained unchanged.

The regressions cover no-event completion rejection, real typed task/run/checkpoint
consumers, historical completion replay after late costs/quarantine, and a bounded
30-iteration reproduction of record growth. On rejected growth they compare exact
SQLite event/record/acknowledgement values and JSONL bytes with the prior state,
then recover, read jobs/attempts/accounting, replay enqueue and reconcile an earlier
abandoned attempt. Existing real process-crash, projection corruption, filesystem
and competing-claim checks also passed. All data was trusted synthetic diagnostic
data in separate pytest temporary directories; nothing represents a real task,
approval or reward. No full repository suite, Docker, network or inference ran.

## Limits

This prevents newly admitted invalid state; it does not repair databases already
made invalid by the reviewed bugs. An old history that completed using evidence
already quarantined at that event, or an existing over-limit index, fails closed
and requires explicit reconciliation. Valid completion preceding later quarantine
retains historical readback and idempotency. No automatic migration, truncation or
limit increase was added. Other hard capacity limits can still prevent a new late
receipt, while the prior attempt/history remains visible.

Tests used CPython 3.13.7 and SQLite 3.50.4 on macOS 26.3 arm64. The existing trusted
local POSIX filesystem assumptions remain. SQLite is authoritative and JSONL is a
verified separate projection; process-crash tests do not establish power-loss or
cross-file atomicity guarantees. Full factory/CLI orchestration remains outside
this correction. Raw pytest output is preserved verbatim, including pytest's
whitespace on blank traceback lines.

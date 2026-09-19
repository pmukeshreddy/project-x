# M6 registry core checkpoint

Accountable owner: `/root/m6_factory`. Base: `f4819c9b11ae39862e894fa09a2f4e6b83aecfae`.
**Scoped core implemented; independent review pending. Full M6 orchestration/CLI
is not implemented by this checkpoint.** Latest steering kept this slice to the
already-started persistence/recovery core needed for real construction attempts.
No downstream service, fake factory operation, admission, feature execution,
human approval or reward was added.

Exact usable APIs and reconciliation examples are in
[interfaces-M6-registry.md](../interfaces-M6-registry.md). Implementation is confined
to `src/feature_rl/registry/**`; dedicated tests and scoped docs/evidence are the
only other edits. M0 bounded storage reads and strict ArtifactRef, OperationResult
and CostRecord values are used directly. No M2/provider imports, shared configuration,
dependencies, Docker, history, network, models, keys or GPU work occurred.

Implemented behavior:

- Actual bounded M0 reference validation and typed dependency closure, immutable
  dependency declarations, cycle/drift rejection, content-derived jobs and explicit
  capacity/backpressure limits.
- Private POSIX directory/file validation, process writer locking and SQLite
  transactions. Immutable ordered/hash-linked events are authoritative; the bounded
  materialized index is replayed and checked against them on every call. JSONL is
  verified and appended only after the database commit, with explicit acknowledgement.
- Fenced owner/claim identities, active idempotent claim replay, explicit abandonment
  and diagnosed retry, persistent attempt limits, immutable result selection, and
  preserved unobserved/interrupted attempts.
- Idempotent reconciliation of already-published upstream references and cumulative
  cost snapshots. External attempt identity has one registry owner; numbered updates
  retain known incurred measurements and old receipts. Late observations remain
  possible after abandonment/completion without changing the selected result.
- Exact-reference quarantine and descendant/job/run/checkpoint tracing. Explicit
  lifts retain historical notices and affected consumption paths. Storage/quarantine
  checks never substitute for the real M5/M6 task admission chain.

## Verification

Final command: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_registry.py tests/test_registry_durability.py`.

**33 passed, exit 0**, at **2026-09-19 20:59:05.818211–20:59:10.337932 UTC**.
Pytest reported 4.29 s; supervising subprocess wall measurement was 4.518163 s.
The seven registry/test source files were hashed before and after execution and
matched exactly. Observed concurrent integration HEAD was
`e1c52ec15c3fc374aa314c8e9845aea4f38b3735`; tested registry bytes were then uncommitted
and are identified by the receipt's explicit source hashes, not falsely attributed
to that HEAD. Interpreter/library/platform details and current M0/brief/scheduling
input hashes are recorded in the same receipt.

Meaningful coverage includes malformed/tampered references, bounded artifact reads,
file/root links and unexpected members, changed registry configuration, immutable
dependencies, queue/log/attempt limits, stale/conflicting completion and claim keys,
global upstream-cost attribution, unknown versus measured-zero costs, late receipts,
and real typed M0 dependency closure to RolloutRecord/TrainingCheckpoint artifacts.
All inputs are explicitly synthetic and inert; no historical or generated task
code is imported or run.

Seven child-process exits (`os._exit(51)`) cover uncommitted event/index updates,
committed events before JSONL, a 37-byte partial append, append before fsync, fsync
before acknowledgement, acknowledgement before commit, and a lost reply. Recovery
preserves existing bytes, completes only matching pending content and avoids a
duplicate job/event. Two simultaneous real child processes compete for one job;
exactly one claims it. A real JSONL write followed by an injected fsync error raises
an explicit I/O error; retrying the same job after recovery creates no duplicate.
Corrupt event/index state, mismatched tails, unknown lines and missing acknowledged
history block without projection rewriting.

Test-first history is preserved: 15 missing-core failures; initial core 15 passed;
expanded risk run 3 failed/30 passed; corrections 33 passed; final source-bound run
33 passed. The three reproduced gaps were active replay returning an abandoned
claim, active claim replay bypassing new quarantine, and recreating a missing lock
beside an existing database. Replay now checks current ownership/quarantine before
returning the capability; existing databases require their existing lock file.

The first red-run shell observer accidentally assigned zsh's reserved `status`
variable after pytest finished. Its log and observer failure are retained; the
corrected second red-run observer records pytest exit 1. No result was erased or
silently relabeled. No full project suite was run, per the explicit concurrent-owner
instruction; root owns that check after review and stable integration.

The complete staged whitespace scan returned exit 2 for pytest's retained trailing
spaces in the two raw missing-core failure logs. Those captured bytes were preserved.
The separate source/test/prose/JSON whitespace check passes; no production whitespace
defect is hidden by reformatting the evidence.

## Evidence and remaining integration

- [One consolidated verification receipt](../evidence/M6/registry/verification.json):
  SHA-256 `1d15d106cd3a3fdf00cb82e71771ec93d3c95de5ddabfc0dadf868fd87a7ff9d`;
  exact command, UTC/status, source/upstream/log hashes and earlier outcomes.
- [Final focused output](../evidence/M6/registry/final-focused.txt):
  SHA-256 `24afa18229c007db164bc57d8b9cbb238bfb85d8ddb20baa4e7613e0f294fc96`.
- Prior red/green logs remain adjacent; the original preflight is unchanged.

These are process-crash and local concurrency checks, not power-loss or external
exactly-once guarantees. SQL/filesystem failures may happen after authoritative
commit; callers must recover/query stable identities before re-executing work.
Unrecoverable history, hard capacity exhaustion and unknown upstream costs fail
explicitly. The minimal core deliberately has no automatic pruning/reindexing,
corruption repair, remote workers, lease stealing or accounting refunds. Runtime
budgets across candidates/stages and trustworthy upstream receipt discovery remain
the actual factory wrapper's responsibilities. The private controller filesystem
and its ancestors must exclude untrusted mutation; same-user hostile filesystem
replacement/ACL administration is not an application-level security boundary.

Next M6 slice: wire this core to the actual reviewed Click authoring/grading/
qualification interfaces supplied by root. Preserve upstream publication order and
receipt identities, then implement concrete orchestration and its real failure
paths. Independent review of this scoped core is required before that integration.

# M6 registry core — independent round 1 review

**Specification compliance: FAIL. Implementation quality: FAIL.**

Two findings block integration of the scoped registry core: **P1: 1, P2: 1**.
No P0 finding. The same M6 owner should fix these paths and add focused regressions.
This review does not assess or fail the still-unimplemented orchestration/CLI slice.

## Scope and evidence

Frozen base `f4819c9b11ae39862e894fa09a2f4e6b83aecfae`; target
`9497b636548545d9fd54a1f06f1432134dc14cd6`. The supplied aggregate diff also includes
unrelated M2/coordinator changes; review covered the target's 17 M6-owned paths,
especially all five registry source files and both dedicated test files. No product,
index, branch, commit, dependency, other owner's working state or historical source
was changed. No subagent, network, Docker, model or GPU operation was used.

Binding inputs: progress/review policy, M6 registry and full M6 briefs, approved
implementation/global constraints, code-completion and independent-slice decisions,
the execution prompt, relevant specification sections 3/6.4/9–10/13–14/17–18,
actual M0 storage/models, and the preserved durability preflight. The implementation
report and interface were checked against code, rather than treated as proof.

The seven registry/test source hashes match the target commit and the owner's
[verification receipt](../evidence/M6/registry/verification.json), SHA-256
`1d15d106cd3a3fdf00cb82e71771ec93d3c95de5ddabfc0dadf868fd87a7ff9d`.
The final raw log also matches its recorded hash. That existing evidence reports
**33 passed, exit 0**, 2026-09-19 20:59:05.818211–20:59:10.337932 UTC,
4.29 s pytest / 4.518163 s outer wall. I did not rerun that suite.

One additional narrow two-case diagnostic ran against real local SQLite/M0 stores
with inert synthetic inputs: exit 0, tool-reported wall 2.547437208 s. Both concerns
below reproduced; exit 0 is diagnostic completion, not acceptance. Exact diagnostic
UTC was not instrumented and is not invented. Post-diagnostic source hashes match
the frozen target. See the [receipt](../evidence/M6/review-registry-round1/receipt.json),
[captured output](../evidence/M6/review-registry-round1/stdout.txt), and
[retained reproducer](../evidence/M6/review-registry-round1/reproduce.py).
The latter retains the executed stdin logic plus a descriptive module docstring;
its file form has not been rerun. Temporary diagnostic state was isolated and cleaned
by its context managers. No feature, qualification, reward, training or power-loss
success is established by these checks.

## Findings

### P1 — Operation evidence is omitted from quarantine dependencies

Locations: [state.py:253](../../src/feature_rl/registry/state.py#L253),
[state.py:107](../../src/feature_rl/registry/state.py#L107);
[core.py:177](../../src/feature_rl/registry/core.py#L177) resolves the omitted references.

`complete` resolves every result/evidence reference, but its quarantine check only
includes the job inputs, configuration and `result.artifacts`. The descendant walk
likewise connects job inputs/configuration to outputs while omitting the immutable
`OperationResult.evidence` references. Those references are required evidence used
to select the result; they are not merely unrelated registered artifacts.

Concrete reproduction: register an independent proof artifact, quarantine it with
separate intact evidence, then complete a running job with a SUCCESS result whose
sole `EvidenceRecord.artifacts` reference is that proof. Completion succeeds,
`assert_usable(output)` succeeds, and `trace(proof)` returns no job or output.
All references resolve through real M0 storage. This is a missing dependency check,
not a request for human-attestation or future task-admission logic in the registry.

Consequently a known-defective proof can support a newly selected output without
its quarantine reaching that output or subsequent consumers. The same absent edge
also prevents later evidence quarantine from tracing already consumed outputs.
This violates the scoped dependency invalidation/affected-descendant requirement
and the specification's quarantine-and-trace requirement.

Required fix: retain the result's evidence dependencies in the job/output graph,
apply their quarantine status before first completion, and include them in later
descendant/run/checkpoint tracing. Preserve historical completion replay and allow
late cost/abandonment evidence to be recorded; bookkeeping reconciliation must not
be turned into new execution or approval. Add focused cases for evidence already
quarantined at completion and evidence quarantined after an output is consumed.

### P2 — A successful mutation can persist records that recovery rejects

Locations: [storage.py:269](../../src/feature_rl/registry/storage.py#L269),
[storage.py:271](../../src/feature_rl/registry/storage.py#L271),
[storage.py:203](../../src/feature_rl/registry/storage.py#L203).

Mutation validates event size, applies the change, writes all materialized rows and
commits without checking the record bounds used by `load`. In particular, a
`JobRecord.attempts` tuple grows across separate small claim events, so the record
can exceed `max_event_bytes` even when every accepted event is below that limit.

Concrete reproduction with valid declared limits `max_event_bytes=2200`,
`max_attempts_per_job=100`, and `JobSpec.attempt_limit=100`: perform explicit
claim/abandon/diagnosed-retry cycles. Claim 24 returns successfully after committing
a **2223-byte** JobRecord, while the largest event is only **1176 bytes**.
The immediate `recover()` then raises `RegistryLimit: materialized index exceeds
configured bounds`. Every public read or mutation runs the same `load`, so normal
queries, reconciliation and recovery are blocked. Existing configuration must
match exactly on reopen, so changing the limit is not a supported recovery path.

This is not ordinary capacity backpressure: accepted work has made previously
readable durable history inaccessible through the public API. It occurs with a
permitted nondefault configuration; the default three-attempt policy was not
shown to trigger this case.

Required fix: validate the entire prospective materialized state against the same
row/count/byte invariants before authoritative commit, or use a separately defined
record bound consistently in both write and replay. Rejection must leave prior
history readable and the last successful attempt state recoverable. Add a small
growth-boundary regression that checks the rejected mutation creates no event and
that `recover`, job lookup and accounting still work.

## Remaining assessment

The inspected implementation correctly uses actual canonical M0 references and
bounded closure reads, immutable artifact declarations and content-derived job
identity. Claim keys/tokens fence attempts; active replay rechecks ownership and
quarantine. Upstream source/attempt/revision identities are globally attributed,
cumulative snapshots preserve known numeric costs and receipts, and late snapshots
do not rewrite the selected result. Abandoned/unobserved attempts remain visible.
These mechanisms do not establish external exactly-once execution or complete
provider billing, and the documentation says so.

The SQLite event/hash-chain/index replay and JSONL prefix/acknowledgement ordering
are coherent on the inspected paths. Existing dedicated tests exercise seven real
child-process interruption positions, competing claim processes, a post-commit
fsync error and corruption rejection. These are meaningful local diagnostics;
the two omissions above remain despite that passing evidence.

After owner fixes, independent review should cover the changed dependency and
precommit-bound paths plus their focused regressions. Root still owns stable
integration checks. Actual upstream receipt discovery/attribution, stage/candidate
budgets, M5 lifecycle admission, orchestration, CLI and non-GPU cross-module checks
remain later integration responsibilities, not additional defects in this slice.
GPU training and experiments remain explicitly unverified, as authorized by the
current code-completion scope.

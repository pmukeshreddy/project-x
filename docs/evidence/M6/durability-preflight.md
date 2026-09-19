# M6 durability preflight — evidence and later implementation requirements

Owner: `/root/m6_factory`. **Bounded mechanism preflight complete; M6 product remains unimplemented and unreviewed.** No M4/M5 interfaces, actual tasks, approvals, rewards, inference, historical/candidate execution, dependencies or product changes were introduced. The probe is explicitly trusted synthetic diagnostic code, using only Python's standard library.

The proposed minimum is one SQLite database containing an authoritative immutable event/outbox sequence and materialized job/attempt indexes, plus a verified append-only JSONL projection. This avoids claiming an atomic transaction across SQLite, JSONL and the artifact store. A database commit selects an operation result; journal acknowledgement and real admission checks determine when it can be returned or consumed.

## Inputs and boundaries

Read the specification, execution instructions, M6 brief, lifecycle decision, actual M0 `ArtifactStore`, `OperationResult`/`CostRecord`, interfaces and M0 reports/reviews. Initial inspection observed HEAD `9cea0bd0d02f123bb91eb828e7e0b7ad2763a7ff`. The experiment captured each input's bytes at HEAD `3f859ca991c8965fda48e9d95588e9be12b0fdab`, with UTC time, SHA-256, Git blob identity, before/after HEAD and an ignored byte snapshot. All 12 captured files matched that committed revision. The raw receipt is the exact binding, not a claim that shared live files remained frozen.

The intervening relevant diff changed `docs/progress.md`: M2 scheduling, M6 ownership/preflight status and exact M1 reference kinds. I inspected it separately. The specification, M6 brief, lifecycle rules, M0 contracts/store and interfaces were unchanged across those revisions. No experiment was repeated for coordinator-document churn. Subsequent live changes must be evaluated against these captured inputs.

Important existing constraints:

- M0 publishes canonical content-addressed envelopes with an exclusive temporary file, file `fsync`, create-if-absent hardlink, exact duplicate comparison and directory `fsync`. A published object is independent of any M6 database decision. Interrupted `.pending-*` bytes and published orphans must be preserved, attributed where possible, and never admitted just because a filename exists.
- `OperationResult` is a validated **non-artifact** model, with disposition, output references, evidence, nonempty costs and reason. This preflight does not invent a registered operation-result artifact kind. M6 must retain the exact validated result serialization and its relationship to the selected output references using the approved storage vocabulary, with any final representation settled during implementation.
- `CostRecord` permits measured, partial and unknown values; `None` is not zero. Task transitions cannot mutate T0 costs/provenance. Transition and recovery evidence/costs belong in operation results and the ledger. Release consumers still resolve Tn→Q→T0, check full payload equality except state/qualification, authenticate the reviewed evidence/policy and consult quarantine/revocation. This probe establishes none of those admission gates.

## Authoritative history and recovery invariant

The following is a proposed invariant for later implementation, not a production API or schema:

1. **Authority:** committed SQLite event rows `E1…En` contain exact canonical event bytes with stable event identities and job/attempt attribution. They are never edited or removed by ordinary execution/recovery. The job/attempt index and new events change in the same SQLite transaction. The index is a projection of that history, not an independent truth. Every logical job selects at most one immutable result; a conflicting completion becomes evidence and cannot replace it.
2. **JSONL projection:** after recovery, the journal is byte-for-byte `E1 || … || En`, with one newline per event. While interrupted it may contain a verified complete prefix, followed only by an exact prefix of the next **committed** event. Sequence gaps, duplicate/unknown complete lines, mismatched bytes or missing acknowledged bytes block the writer. Stable event identities allow downstream consumers to deduplicate delivery, without deduplicating distinct attempts.
3. **Acknowledgement:** SQLite's acknowledgement watermark `a` may lag the verified complete journal prefix. It must never exceed it. Before advancing `a`, synchronize the file even when a preceding process already wrote every complete line. The watermark is a recovery aid, not a substitute for verifying the journal or artifact bytes.
4. **Attribution before work:** durably register an attempt and budget reservation before launching cost-bearing work. For this minimum, also flush its start event to JSONL before launch. An interrupted start remains visible with unresolved outcome/unknown cost even if dispatch never happened. Known durable observations survive failures; missing measurements remain explicitly unknown.
5. **Visibility:** a completion can be selected in SQLite before its JSONL projection is acknowledged. No consumer may interpret that intermediate index row as an admitted task or delivered reward. Before returning/consuming it, complete projection, resolve/validate referenced immutable artifacts and apply the actual lifecycle/dependency gates. Recovery of storage does not infer semantic success from artifact existence.

Suggested order:

`attempt registration + outbox COMMIT → start JSONL fsync/ack → work → attributable observation/cost COMMIT → immutable artifact publication/verification → result selection + terminal outbox COMMIT → JSONL append/fsync → acknowledgement COMMIT → caller reply`

There are several independent commit points. Holding a SQLite `BEGIN IMMEDIATE` transaction while the projector verifies/appends/synchronizes/acknowledges serializes compliant local writers; it does **not** put the separate file inside SQLite's transaction. No other process may append directly. A production implementation must bound work held under that lock and reject/backpressure before new launches when publication is unhealthy.

On restart, let SQLite recover its own transaction, validate database integrity and index/event relationships, then verify the journal against committed outbox bytes under the writer lock. Append missing complete events. For an incomplete final line, append its missing suffix **only** if the existing bytes exactly match the beginning of the next outbox event and lie beyond the acknowledged prefix. No truncation, newline insertion that relabels garbage as an event, deletion or silent skip is permitted. Repeated interruption during suffix completion remains an exact prefix. Record recovery actions and their costs as new evidence in the later product.

Unknown/corrupt tails and missing acknowledged history are preserved and block automatic recovery. Any later operator-controlled repair must retain original bytes and record the decision in a separately specified recovery procedure; this preflight implements no such procedure. A missing/corrupt referenced artifact likewise blocks consumption. An orphaned object does not justify re-running work or manufacturing its missing completion receipt.

## Idempotent results and incurred costs

The job key must derive from the complete validated operation/dependency/configuration manifest, including versions and the intended logical invocation/seed identity. Retries of that logical operation share the key; intentionally independent episodes/trials must not collapse because their task/policy references happen to match. Changed semantic inputs create a new key while remaining inside the original candidate's repair/budget accounting. The specification's two repairs per failed stage/four total per pilot candidate cannot be reset by generating new keys.

An **attempt** has its own durable identity each time work is actually dispatched again. Replaying a saved result/receipt or finishing its interrupted publication creates no new execution attempt. A distinct attempt returning the same output retains its own costs and evidence while the unique result remains unchanged. A distinct conflicting result retains its evidence/costs and raises a conflict; first arrival is not evidence of semantic validity. Exactly-once result selection does not establish exactly-once external execution.

Completion replay must return the exact selected `OperationResult` snapshot. Later costs from another attempt or recovery append to the ledger; they cannot silently change that result's identity or T0's construction-cost snapshot. Current cumulative accounting therefore needs the ledger's explicitly linked history, not just the first result's cost fields.

Cost observations need stable receipt/meter/interval identities. Re-delivery of the same observation must not be summed twice; cumulative snapshots cannot be summed as independent deltas. Start-time unknown coverage and later observations are linked history, not two additive cost records. Operation-result cost summaries and ledger observations must remain traceably linked so aggregation does not charge both representations. New observations or corrections append history, with unresolved coverage shown explicitly.

The gap between external work and durable receipt capture is unavoidable without an upstream transactional/idempotent protocol. This probe intentionally loses the hashing interval's measurement at that gap: the durable attempt remains unknown. The supervisor's measured process wall time is retained separately and cannot reconstruct missing provider billing, stage CPU time or other unknown measurements. Later M6 must reconcile actual M2/M3 receipts/supervisor state before redispatch, keep unresolved attempts in reports, and count both attempts when a retry incurs work. Publication retries must not cause another model call. Unknown incurred cost cannot authorize extra spend or be reported as free work.

## Executed diagnostic

Command: `.venv/bin/python docs/evidence/M6/durability-preflight-probe.py`.

Execution: **2026-09-19 11:21:29.680610–11:21:32.450658 UTC**, driver exit **0**, **169/169 local assertions**. These are protocol diagnostics, not product tests or a factory acceptance result. One run covered 16 publication positions plus three deliberately corrupted cases, using 19 short child processes. Abrupt children call `os._exit(91)` at named points; the kernel and storage remain running. One otherwise identical clean case exits 0. The three corruption setups add one exit-91 and two exit-0 children: **16 abrupt exits and 3 clean exits total**.

| Process exit point | Observed state and recovery |
| --- | --- |
| Before attempt COMMIT | No registered attempt/event/result; no work launched. |
| After attempt COMMIT, before start projection | One durable unknown start; journal appended from outbox; no result. |
| After start projection, before work | Start acknowledged; no result; no duplicate append. |
| After work, before observation COMMIT | Start retained with unknown stage cost; no invented measurement/result. |
| After observation COMMIT | Measured diagnostic wall/CPU interval retained; no published object/result. |
| After artifact temporary-file fsync | Temporary bytes retained; no published object/result. |
| After hardlink, before directory fsync | Both temporary and published names visible to the surviving kernel; no result. This is not power-loss evidence. |
| After artifact publication | Immutable object retained; no terminal result selected. |
| Inside completion transaction, before COMMIT | Completion/index update rolled back; durable observations and object retained. |
| After completion COMMIT | One selected result; pending observation/completion events appended; ack 1→3. |
| During partial JSONL append | Exact **317-byte** prefix retained; **724** remaining bytes appended; ack 1→3. |
| After complete append, before fsync | Already visible complete lines re-synchronized; zero duplicate bytes; ack 1→3. |
| After fsync, before acknowledgement | Zero duplicate bytes; acknowledgement recovered 1→3. |
| Inside acknowledgement transaction | Journal retained; acknowledgement rolled back then recovered 1→3. |
| After acknowledgement, before reply | Same durable selected result; no new event or journal bytes. |
| Clean completion | Three events acknowledged; exact repeat writes zero bytes. |

Every recovery preserved the committed event payloads, job/attempt projection and all object/temp bytes; SQLite integrity/foreign-key checks passed. Cases without a committed terminal event **remained incomplete**. The probe did not redispatch work or infer a missing semantic completion. Full snapshots and exact journal bytes accompany every case.

Additional checks: same-attempt completion replay changed no event, cost or selected result. A second measured attempt with identical output produced its own start/observation/completion while preserving the original selection. A third measured attempt with conflicting bytes retained all three cost observations, recorded the conflict and left the selection intact. No real `OperationResult`, task or reward was created.

Three negative cases—mismatched incomplete tail, unknown complete suffix, and loss of part of an acknowledged tail—raised explicit errors and left the injected journal, events, acknowledgement, costs and objects unchanged. Original pre-injection bytes are also retained in the receipt. No automatic repair was attempted for them.

Measured driver wall time: **2.769631 s**; sum of 19 child subprocess wall times: **2.217954 s**. The outer command receipt measures **2.817228 s**, including driver startup/receipt overhead. These overlapping measurements are not additive. Per-attempt hashing wall/CPU intervals are real diagnostic measurements; total CPU, GPU, tokens, human time and currency were not measured and remain null/unavailable. No paid compute or inference was invoked.

## Local durability assumptions and primary documentation

Observed CPython **3.13.7**, SQLite **3.50.4**, source ID `2025-07-30 19:33:53 4d8adfb30e03f9cf27f800a2c1ba3c48fb4ca1b08b0f5ed59a4d5ecbf45e20a3`, macOS **26.3 arm64**. New state resides on local **APFS**, internal solid-state device `disk3s5`, mount `/System/Volumes/Data`, `st_dev=16777233`. The receipt includes the actual `df`, `diskutil`, SQLite build options and filesystem allocation observations. No network filesystem, remote/object store or separate volume was exercised.

Every diagnostic connection read back `journal_mode=delete`, `synchronous=3` (EXTRA), `fullfsync=1`, `foreign_keys=1`, `busy_timeout=1000`. EXTRA adds directory synchronization after unlinking the rollback journal in DELETE mode; SQLite recommends it over FULL for that mode's durability. `fullfsync=ON` requests the macOS full-sync method where supported. Readback is not syscall or hardware instrumentation. [SQLite PRAGMA documentation](https://www.sqlite.org/pragma.html#pragma_synchronous), [fullfsync](https://www.sqlite.org/pragma.html#pragma_fullfsync).

SQLite's atomic-commit mechanism concerns its database and journals. Correct locking and storage flush behavior remain assumptions; it does not encompass an independently appended application JSONL file or M0 object. [SQLite atomic commit and failure assumptions](https://www.sqlite.org/atomiccommit.html).

The probe uses explicit SQL `BEGIN IMMEDIATE`/`COMMIT`/`ROLLBACK`, `isolation_level=None`, and explicitly selects legacy transaction control on Python ≥3.12. Python documents that this mode leaves transaction opening to explicit SQL. Only the actual 3.13.7 execution is qualified here, not every supported Python version. [Python sqlite3 transaction control](https://docs.python.org/3.13/library/sqlite3.html#transaction-control-via-the-isolation-level-attribute).

The Python artifact/journal writes use unbuffered `os.write` and `os.fsync`; directory creation/publication is also synchronized. Python documents that `os.fsync` calls native `fsync` on Unix and buffered file objects must be flushed first. M0 also uses `fsync`; this preflight does not add or verify a stronger hardware flush policy for it. [Python os.fsync](https://docs.python.org/3.13/library/os.html#os.fsync).

**Unsupported/unverified:** power loss, kernel panic, device write-cache guarantees, torn sectors, lying/broken flush or locking, filesystem/SQLite corruption recovery, disk-full/I/O/fsync-error injection, read-only media, lock contention/multiple writers, remote workers, external billing reconciliation, production shutdown/leases/fencing, backup/restore, outbox pruning/log rotation, automatic orphan cleanup, index reconstruction and production path/permission attacks. The trusted probe uses a private directory; it does not reproduce M0's security traversal or replace its implementation. Durable application event retention and restricted controller access are requirements, not protection against an administrator modifying SQLite rows. The experiment's matching incomplete tail was deliberately written by a short write, not a simulated power-loss sector tear.

## Later M6 implementation gates

Keep this decision small: local SQLite/outbox plus one serialized projector is sufficient to investigate further; no distributed broker or WAL migration is justified by this result. Final product code must validate all real operation inputs/dependencies, preserve typed failures, provide a committed-result lookup for acknowledgement retries, verify all artifact references after restart, and reconcile incomplete attempts without false success or lost cost. A real error while committing/appending/synchronizing must become attributable infrastructure failure, not success or automatic repeated execution.

Require bounded queue/outbox backlog handling, durable repair/spend reservations across changed job keys, strict duplicate/conflicting receipt rules, production index/history reconciliation, path/permission controls, and meaningful fault tests using actual reviewed M0-M5 operations. Real admission/quarantine and the first request-to-construction/grading path remain future integration work. This evidence does not approve any production table definition, downstream service signature, release, qualification or CLI implementation.

Evidence files:

- [Probe source](durability-preflight-probe.py) — exact source bytes are embedded and hashed in the raw receipt; script refuses to overwrite its receipt/state. Re-execution requires explicitly new names in a newly reviewed script; this is not a command to run on production state.
- [Raw observations](durability-preflight-receipts.json) — SHA-256 `68871473cbf6010e4c080639860f9ae6d558b48021f0ea9350cce27f0b76f7b4`, 338,213 bytes; exact commands/exits, input bindings, snapshots, recovery/corruption bytes and all 169 checks.
- [Outer command receipt](durability-preflight-command.json) — invocation, UTC time, elapsed wall time, exit status and exact stdout/stderr streams.

All task-private databases, JSONL files, temporary/published inert objects and captured input snapshots remain under ignored `.feature-rl/research/M6/durability-preflight-20260919/`. They were not cleaned up, copied into production, or relabeled as real task evidence.

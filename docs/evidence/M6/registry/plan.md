# M6 registry core implementation plan

Base: `f4819c9b11ae39862e894fa09a2f4e6b83aecfae`. Accountable owner `/root/m6_factory`.
The registry-core brief and preserved durability design are the authorized specification.
The scheduling decision permits this owner to implement concurrently with M2 in disjoint paths.
No additional approval, worktree, shared configuration change or subagent is needed.

1. Write focused behavioral tests for bounded M0 artifact/dependency verification,
   immutable registration, typed content-derived jobs, competing/fenced claims and
   explicit retry limits. Observe failure before adding implementation.
2. Implement strict registry models, safe private POSIX state directory handling,
   SQLite event authority and bounded materialized index verification. Every operation
   validates history/index and the append-only projection under a local process lock
   and SQLite transaction. Events have sequence/hash-chain/semantic identities.
3. Implement distinct attempts and idempotent upstream reconciliation. An external
   source/attempt key can belong to only one registry attempt; numbered immutable
   accounting snapshots may add known measurements but cannot erase incurred cost.
   Operation results bind exact existing snapshots and remain immutable when later
   observations arrive. Interrupted/abandoned attempts stay visible.
4. Add artifact dependency closure, explicit immutable dependency declarations,
   job input/output propagation and quarantine tracing of descendants, jobs, runs
   and checkpoints. Lifting a quarantine preserves the historical notice and trace.
   This does not authorize any task lifecycle state.
5. Add real process/filesystem cases for concurrency, uncommitted/committed index
   changes, partial journal append, lost acknowledgement, fsync errors, corrupted
   history/index/projection and unsafe directory shapes. Tests use only trusted
   synthetic M0 bytes/models in private temporary directories; no task execution.
6. Run focused `test_registry*.py` checks, inspect failures, document exact public APIs
   and reconciliation/recovery obligations, bind source/log hashes and commit only
   owned paths. Root handles independent review and the later integrated suite.

Files: `src/feature_rl/registry/{models,storage,core,__init__}.py`,
`tests/test_registry*.py`, `docs/interfaces-M6-registry.md`,
`docs/reports/M6-registry.md`, `docs/evidence/M6/registry/**`.

Review focus: state-directory links/replacement and bounded reads; every event/index
transaction gap; stale completion and global observation replay; preservation of
unknown/late costs; exact dependency metadata and historical quarantine traces.

Explicit limits: local trusted controller POSIX filesystem; no power-loss guarantee,
no external exactly-once execution, no model/provider imports, no task admission,
no automatic redispatch of an interrupted attempt. The outer factory remains
responsible for authentic upstream attribution, budgets spanning construction stages,
receipt discovery and the real operation's semantic gates.

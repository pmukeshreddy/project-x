# M1 round 1 Git descendant-cleanup re-review

Scope: the sole remaining inert-Git boundary finding after `5587c6b`, using the complete `M1-round1-cleanup.diff`, actual `src/feature_rl/history/git.py` and descendant fixtures in `tests/test_history.py` at `dec8118`, plus one narrow output-limit reproduction. This does not re-review or approve the other M1 findings.

**Scoped specification compliance: PASS.** The owned Git process group is now terminated and checked even after the direct process leader exits. The bounded real cached Click intake is cleared to run under this corrected boundary.

**Scoped implementation quality: PASS.** The previous parent-only success path is removed. Cleanup is bounded, fail-closed, shared by success/failure/timeout/output-limit paths, and covered by real descendant-process fixtures. No remaining blocker was found in this narrow correction.

## Finding disposition

### P1 — Descendants survive when the direct subprocess exits first: ADDRESSED

Locations: `src/feature_rl/history/git.py:100-153` and `:155-214`; tests at `tests/test_history.py:343-413`.

`_kill` no longer returns when `process.poll()` reports leader exit. It always targets the session's process group, separately reaps the direct child, queries the group for remaining members, ignores non-running zombie states, retries within a one-second cleanup deadline, and raises `HistoryError` if a live member remains. If group signaling raises `PermissionError`, it signals the observed non-zombie members individually and still verifies the group afterward. The `/bin/ps` inspection uses an absolute executable, a fixed environment and field set, and a 0.5-second timeout; failure to inspect becomes failure of the Git operation.

The focused fixtures exercise a leader that exits 0 after spawning a background child, a leader that exits 7, and a leader plus child surviving until the Git deadline. They verify that no live non-zombie descendant remains and do not accept parent exit as sufficient cleanup.

I additionally exercised the shared output-limit path with a helper that backgrounded `sleep 30`, closed the child's inherited descriptors, and emitted 100,000 bytes under a 64-byte cap. `HistoryOutputLimit` was raised and `/bin/ps` reported no live non-zombie descendant afterward. This confirms the `finally` cleanup covers the remaining branch without requiring a redundant full-suite run.

The owner reports the three focused descendant cases passed and the full suite passed **149 tests**. Those reported runs were not repeated. No historical repository code or cached Click intake was executed during this re-review.

The earlier boundary report's dispositions remain unchanged: external diff/textconv helpers are disabled for the reviewed Git commands, paging/hooks/replacement refs/system/global config are neutralized, promisor lazy fetching is disabled, missing objects fail explicitly without cache mutation, and stdout/stderr/deadline bounds fail closed. These properties are sufficient for the coordinator's bounded cached Click rerun. Its results remain evidence for the later full seven-finding M1 re-review, not automatic approval of M1.

Only this review report was written; product code, tests, index, branch, and commits were not changed.

# M1 round 1 inert-Git boundary re-review

Scope: the Git-boundary subset of `52aaf16..5587c6b`, using the complete supplied boundary diff, actual `src/feature_rl/history/git.py`, new real-Git fixtures in `tests/test_history.py`, the original P0/P1 reproductions, and the current-cache configuration audit. This is not a re-review of the other M1 findings and does not approve chronology/provenance, graph reconstruction, licensing, HTTP intake, or the full module.

**Scoped specification compliance: CHANGES REQUIRED.** External diff/textconv execution is disabled and promisor lazy fetching is blocked, but the required descendant-process cleanup boundary remains incomplete.

**Scoped implementation quality: CHANGES REQUIRED.** Output caps, monotonic deadlines, simultaneous stdout/stderr draining, a new process session, and explicit missing-object failure are meaningful improvements. The common cleanup helper can still leave a descendant alive after the direct process exits, so the cached Click rerun is not cleared by this review.

## Finding

### P1 — Descendants survive when the direct subprocess exits first

Location: `src/feature_rl/history/git.py:101-110`; call sites and cleanup path at `:116-165`.

`_run_bytes` correctly launches Git with `start_new_session=True`, but `_kill` returns immediately when `process.poll()` reports that the direct process has exited. It therefore never sends `SIGKILL` to the still-existing process group in that state. The `finally` call has the same short-circuit. A configuration-driven helper or other descendant that backgrounds itself, closes inherited output descriptors, and lets Git exit can remain alive after `_run_bytes` reports success; a descendant retaining the pipes can also survive the timeout path after its parent exits.

A focused reproduction replaced `_argv` with a command that backgrounded `sleep 30` with stdin/stdout/stderr closed, wrote its PID, and exited 0. `_run_bytes` returned `b''`; `os.kill(child_pid, 0)` then confirmed `descendant_alive_after_success=True`. The reproduction killed that child afterward. The new tests cover a hostile `diff.external` and an incomplete promisor repository, but no test asserts descendant cleanup after the group leader exits.

Required correction: attempt to terminate the process group even when the direct process has already exited, while separately waiting/reaping the direct child when necessary. Add success, timeout, and output-limit regressions in which the leader exits before a descendant, and verify the descendant PID/process group no longer exists.

## Addressed boundary items

- **Original P0 helper execution:** addressed for the reviewed command set. Both diff paths now pass `--no-ext-diff` and `--no-textconv`; paging, hooks, replacement refs, system/global config, and prompts are disabled. The hostile `diff.external` fixture exercises reconstruction, changed paths, object lookup, blob reads, and archive creation without running its helper. No additional configuration-driven executable helper was found in the reviewed fixed command set.
- **Original P1 lazy fetch/mutation:** addressed in the reviewed code and focused fixture. `GIT_NO_LAZY_FETCH=1` is applied to every subprocess. A real blob-filtered promisor clone with a missing object now fails explicitly, and its pack-directory inventory remains unchanged. This keeps object acquisition outside inspection and its source manifest.
- **Bounds:** stdout and stderr are drained concurrently; stdout uses each call's bound, stderr is capped at 1,000,000 bytes, and one monotonic deadline covers draining and wait. The remaining failure is cleanup after the direct process has already exited, not the byte/deadline accounting itself.
- **Current Click cache:** the coordinator's audit, with system/global config excluded, found no repository-local `diff.*`, `filter.*`, `core.fsmonitor`, or `include.*` keys at audit time. That reduces the known cache's immediate helper risk but does not prove historical state and does not repair the generic child-lifecycle defect.

The owner-reported 146-test pass was not repeated; this review used the code, fixtures, recorded result, and one narrow cleanup reproduction. No actual cached Click intake was run or cleared. Only this review report was written.

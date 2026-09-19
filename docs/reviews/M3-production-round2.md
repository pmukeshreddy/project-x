# M3 production scoped re-review, round 2

Independent reviewer: `/root/review_m3_production`, 2026-09-19. Product base `a9fec98ce2fec8cd8bc30c245a1100156532e847`; fix target `ad154f88013200d9c0134b808c9211032ed67a2b`. Reviewed the complete supplied fix diff, owner fix report, actual changed runtime/tests/API, RED/GREEN/focused logs, diagnostic driver/results, and source/evidence bindings.

**Specification-compliance verdict: FAIL.** The original stale-source P2 is closed. One connected P2 remains: close/reset can admit a successful terminal transition after an intervening operation leaves owned cleanup pending.

**Implementation-quality verdict: FAIL.** The source/generation locking fix and its regressions are sound, but terminal recovery and admission are still separated by a race. No P0/P1 was identified. Root's fresh integrated full-suite gate remains pending; no broad suite, historical or native/provider gate was repeated by this reviewer.

## Original finding: closed

[runtime.py:277](../../src/feature_rl/environments/runtime.py#L277) and [runtime.py:283](../../src/feature_rl/environments/runtime.py#L283) now acquire the existing operation lock before loading workspace state and hold it through the durable write. Close additionally increments the latest generation. This closes the reproduced stale-read overwrite and prevents overlapping resets from reusing a generation when source bytes are identical. The fix does not change image, dependencies, source/wheel validation or command execution.

The three new regressions fail against the old runtime for the intended reasons: old source returned by close, stale generation after reset/save, and generation reuse across resets. The RED receipt binds the old runtime hash `a0984d07d21483861e6e023eae5ac2059acef692d6be25cbd44c852a1daa04b5` and the same new test hash used by GREEN. The complete fix runtime hash is `cd0eb49c91738c4aaac11d4848ad5a5bb0c264e48a6aa034827a52c59c1f4d9d`; Docker test hash is `b1d8c3ac1d41ff9a93e9b98cb8e0d14a99a78d429bcdee33a09d320ea477dfa8`.

I independently inspected the five retained real synthetic schedules and their states:

| Schedule | Actual retained outcome |
| --- | --- |
| Close delayed before lock; successful save | Latest saved source returned/persisted, generation 1 → 2, closed true. |
| Reset delayed before lock; successful save | Initial source restored, generation 1 → 2, closed false. |
| Close paused after workspace read | Competing execution rejected with `EnvironmentError: runtime operation already active`; generation 0 → 1. |
| Reset paused after workspace read | Same explicit overlap rejection; generation 0 → 1. |
| Two resets, unchanged source bytes | Generation 1 → 2; older bound session rejected before create, with no container ID and specific absence verification. |

The synchronization wrappers select schedules while retaining real locks, state I/O, save operations and Docker cleanup. Existing evidence records **3 failed in 10.66s → 3 passed in 10.17s**, and **41 focused tests passed in 60.91s**. Decoded CAS execution receipts equal the diagnostic's embedded receipts and have exact successful removal/absence evidence. The source/test/evidence hashes and 20559-byte diff hash `f688f315b3f4fd41e1f20b6589afd741e718538bac755ac12d276a9fc4ddbb4b` independently match the supplied package. The five-schedule driver retains its own exact hash and all four owned operation records as removed. Its literal revision is the earlier product identity, so the recorded per-file hashes, rather than that literal alone, bind the uncommitted fix execution.

## Unresolved finding

**P2 — terminal admission can miss newly pending cleanup and return normally with a live owned container.** `reset` calls recovery at [runtime.py:276](../../src/feature_rl/environments/runtime.py#L276), then separately takes its transition lock at line 277. Close does the same at [runtime.py:282](../../src/feature_rl/environments/runtime.py#L282) and line 283. `DockerEngine.recover_owned()` releases its lock before either terminal method takes the next lock. Neither method rechecks pending ownership under its transition lock. The corrected source read therefore cannot establish the terminal cleanup guarantee.

The new bounded [diagnostic](../evidence/M3/review-round2/terminal_cleanup.py) reproduced this exact schedule on the supplied fix:

1. Close finishes recovery and pauses immediately before taking its transition lock.
2. A real `execute_development` runs synthetic `python -c pass`. Its cleanup encounters an intentionally unavailable daemon endpoint through the existing real cleanup path, yielding `infrastructure_failure`, `cleanup_verified=false` and a durable `cleanup_pending` record. The controller's real daemon route is then restored.
3. Close resumes, acquires the lock, returns a normal `SavedSource`, and writes `closed=true`, generation 1. It never notices the new pending operation.
4. A bounded read-only Docker HTTP inspect of the exact owned container returns **200**, `Status=running`, `Running=true`. The ownership record still says **cleanup_pending**.

Exact operation: `cc713cb6276f4ab9ab1bdf631344663a`. Exact live container: `ffd960a3caf629813e835bd874b9f8da0a21a6ffc3aa1b842f054d8b55293510`. Complete failed-cleanup receipt, normal close result, durable workspace/ownership records and live inspection are in [terminal-cleanup-result.json](../evidence/M3/review-round2/terminal-cleanup-result.json). The demonstrated live process is the container's runtime PID 1; this diagnostic does not claim that its completed `pass` command remained running. Arbitrary development commands can also leave descendants, so terminal cleanup cannot rely on the command having returned.

This violates the promised successful terminal cleanup boundary and permits a closed/reset workspace while its owned runtime remains alive. It is a concrete lifecycle failure, not a speculative request for additional stress tests. The previously fixed stale-source P2 is not being reopened: this is the adjacent recovery/admission race exposed by reviewing that transition.

Close/reset must either recover and verify relevant pending ownership within the same admission critical section, or fail explicitly when a pending operation appeared since the earlier recovery. They must not publish a normal terminal result while cleanup remains unverified. Reuse the existing lock/recovery mechanisms with a clear non-nested-lock path or admission check; no architecture rewrite is required. Add focused terminal/cleanup-fault interleaving coverage for both methods.

After recording the failure, the reviewer explicitly recovered the pending operation: `rm --force` exited 0, then inspect exited 1 with `No such object:` naming that exact container. All owned records are removed and subsequent recovery is empty. The diagnostic ran from `2026-09-19T19:58:26.918474+00:00` to `2026-09-19T19:58:31.289842+00:00`, approximately 4.37 seconds, and exited 0 with `reproduced=true`. It created only a trusted qualification container and one synthetic development container. No owner runtime/evidence was mutated.

## Reviewer actions and remaining scope

Exact new runtime diagnostic command:

```sh
PYTHONPATH=src .venv/bin/python docs/evidence/M3/review-round2/terminal_cleanup.py > docs/evidence/M3/review-round2/terminal-cleanup.log 2>&1
```

Other actions were read-only source/diff/log inspection and an inline standard-library SHA256/JSON/base64 audit of the complete supplied fix/source/test/evidence hashes, five schedule outcomes, CAS receipt equality and container-absence identities; that audit exited 0. No existing tests were rerun by this reviewer. The only writes are this report and assigned `review-round2` diagnostics. Private synthetic CAS/state are ignored within the assigned diagnostic directory. Product files, tests, Git index and commits are untouched.

Earlier B/H runtime evidence and the failed initial B run remain unchanged. Click repair counts remain environment 1/2 and candidate 1/4. Prior M3 scope limits and separate M2/M4/M5/M7 qualification/training requirements still apply. Root retains the final integrated verification gate after owner fixes and independent re-review.

**Unresolved inventory: P0 0; P1 0; P2 1 (terminal cleanup/admission race, close reproduced; reset shares the same path).**

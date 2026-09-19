# M3 production scoped re-review, round 3

Independent reviewer: `/root/review_m3_production`, 2026-09-19. Product base `ad154f88013200d9c0134b808c9211032ed67a2b`; reviewed target `2190af04ed10dccd65249963a727f19c0cdedaab`. Applied the current review policy and [code-completion scope](../decisions/code-completion-scope.md), which distinguish completed code from deferred GPU/experimental execution.

**Specification-compliance verdict: PASS for the assigned local no-service M3 production runtime as fixed.** The connected terminal-cleanup P2 is closed, and the earlier source/generation P2 remains closed. No unresolved P0/P1/P2 was identified in the reviewed M3 scope. Root's fresh integrated non-GPU suite is a subsequent integration gate, not an execution claimed by this review.

**Implementation-quality verdict: PASS.** The fix is small, uses the existing operation lock and ownership records, preserves real cleanup checks, and adds meaningful failure/recovery regressions for both close and reset. It does not change application semantics, image/dependencies, archive policy, build/import behavior or resource enforcement. No additional runtime diagnostic was necessary.

## Findings closed

**Round2 cleanup/admission P2: closed.** [runtime.py:277](../../src/feature_rl/environments/runtime.py#L277) and [runtime.py:284](../../src/feature_rl/environments/runtime.py#L284) hold the operation lock across the new ownership admission check, workspace read, and durable terminal write. [docker.py:189](../../src/feature_rl/environments/docker.py#L189) reads and strictly validates owned operation records and raises `CleanupUnverified` for any phase other than `removed`. It neither changes pending records nor infers absence from daemon failure. Both calls occur under the same lock used by session creation, save and cleanup, so an intervening operation cannot create a new pending record between the check and terminal commit.

The initial recovery remains outside that critical section, but it is no longer the sole admission proof. If a command leaves cleanup pending in the gap, terminal admission explicitly fails before reading or modifying workspace state. Recovery and retry use the existing exact daemon/owner checks, removal and specific absence verification. The private helper's lock precondition is documented and obeyed by both product callers; it does not acquire a nested nonblocking lock.

**Round1 stale-source/generation P2: remains closed.** Workspace reads still occur under the terminal lock; close/reset advance the latest generation on success. The new guard runs before those reads and writes. A successful save before terminal admission is preserved; a failed terminal admission leaves the entire saved-source/generation/closed record unchanged. The previous three concurrency regressions remain in the unchanged portion of `tests/test_environments_docker.py` and were included in the final 43-test focused run.

## Actual evidence assessed

Read the round2 fix brief, current review policy/scope ruling, complete owned product/test/report/API diff, changed code in context, parameterized tests, diagnostic driver, RED receipt/failure log, GREEN/focused logs, verification manifest and actual diagnostic/CAS evidence. The full range also includes coordinator scope updates and the already reviewed round2 report/evidence; those unrelated module changes are not approved by this scoped M3 verdict.

The complete owned diff is **16418 bytes**, SHA256 `c73410f8139800153406ca7a2e065ec8d50bb4372c9eb1a84339037491853a97`. The independent bounded audit matched all 18 listed product/test/evidence file hashes. Key target identities:

- `docker.py`: `25be92992a5214442ef1b95cf0f499d260460072df68b564decd2da024e7e7a6`
- `runtime.py`: `4994c5801464cc0fb7b676f1ad2247d534720db253f45b99933aab2278443b68`
- `test_environments_docker.py`: `6c0bc4bec0bfbef85b9211c678a87c34f97409c0d154d951c5f26e3d0af032da`

The RED receipt binds the old product and the same new test bytes as GREEN. Its two failures are the intended defect: close/reset returned a saved source instead of `CleanupUnverified` while ownership was pending. The scheduling and endpoint-fault wrappers leave real locks, source saving, state storage, Docker workers, cleanup and recovery in use; they do not replace these operations with successful doubles.

The independent audit decoded and hash-verified both private CAS execution receipts and compared them to the embedded diagnostic evidence. For each case it checked exact live-container identity before/after rejection, unchanged full workspace state, pending ownership, infrastructure classification, actual successful `rm --force` and exact inspect absence, HTTP404 after recovery, and correct terminal retry:

| Transition / operation | Rejection and recovery | Successful retry |
| --- | --- | --- |
| Close / `26d3e48a498142aabbc057316c901741` | Exact container `ea59ccabbfc06a25096a41141186f5fd615e23aa7cacd07cd693464c62b6b3a3` remained HTTP200/running and pending; `CleanupUnverified` left source v1, generation 1 and closed=false unchanged. Recovery actually removed it and verified absence. | Returned the saved v1 source; generation 2; closed=true. |
| Reset / `3536d42a86d641ce8b512369747fe968` | Exact container `c93680b2cc59bbe684dc74830d9b3ff31b9303b02a87207106ea4ad6babac519` had the same explicitly failed admission and verified recovery sequence; complete workspace state stayed unchanged. | Restored initial v0 only after recovery; generation 2; closed=false. |

Both recoveries have `rm --force` exit 0 followed by inspect exit 1 with `No such object:` naming the exact container. All three diagnostic ownership records, including trusted qualification, end in `removed`; recovery is empty. This supports the cleanup outcome independently of an empty inventory log. The diagnostic's literal revision names its product base; its recorded source hashes and driver hash bind the actual uncommitted fix bytes to the reviewed target.

Recorded executed checks, read and audited rather than repeated:

| Command | Result |
| --- | --- |
| `PYTHONPATH=src .venv/bin/pytest -q tests/test_environments_docker.py -k terminal_admission_rejects_new_pending` | RED: 2 failed, 24 deselected, 9.49s; GREEN: 2 passed, 24 deselected, 8.38s. |
| `PYTHONPATH=src .venv/bin/python docs/evidence/M3/fix-round2/terminal_cleanup.py` | Exit 0; both real synthetic schedules passed in 8.701s; all owned operations removed. |
| `PYTHONPATH=src .venv/bin/pytest -q tests/test_environments.py tests/test_environments_docker.py` | Exit 0; 43 passed in 68.29s, including retained source/generation regressions. |

## Review actions and scope limits

The sole new executable diagnostic was read-only evidence verification:

```sh
PYTHONPATH= .venv/bin/python docs/evidence/M3/review-round3/audit_fix.py > docs/evidence/M3/review-round3/audit.log 2>&1
```

It exited 0. Reproducible audit code, [results](../evidence/M3/review-round3/audit-result.json) and log are under `docs/evidence/M3/review-round3/`. It uses bounded standard-library file/JSON/base64/hash reads and imports no application or product module. Other review actions were source/log/diff/status reads. No new container, historical B/H execution, broad suite, native/provider/model call, download, GPU/remote/paid compute, product/test mutation, index mutation or commit occurred.

Prior M3 runtime evidence and limitations remain as assessed in rounds1–2: local Linux arm64/offline/no-service support, explicit last-confirmed non-atomic capture, no claim of public pytest as tamper-resistant grading, and no automatic controller retention policy. The failed initial B health run stays failed; the pinned less repair counts stay environment 1/2 and candidate 1/4. Feature authoring/grading/qualification and the rest of the M0–M8 code are outside this scoped verdict. GPU training and experimental outcomes remain deferred/unverified and are not treated as blockers to this code review or replaced with invented results.

**Unresolved scoped findings: P0 0; P1 0; P2 0.** Root may proceed to its one fresh integrated non-GPU verification gate.

# M3 fix round 1: atomic close/reset

The confirmed P2 in [M3-production-round1.md](../reviews/M3-production-round1.md) is fixed in the existing workspace transition code. Product base is `a9fec98ce2fec8cd8bc30c245a1100156532e847`; the review/brief dispatch followed at `ac16b49`. Only `runtime.py`, the corresponding Docker tests, the API description and new fix evidence/report are changed. Independent scoped re-review remains pending.

Previously, close/reset loaded the complete workspace record before acquiring the operation lock. A successful save between that read and the terminal write could be overwritten. Close could return the older submission; two resets could also reuse one generation, allowing an older pending execution with identical source bytes to pass admission.

Both terminal methods now acquire the existing lock before loading/validating workspace state, then retain it through the write. Reset increments the latest generation; close now increments it too. Recovery remains before the transition, existing session admission still checks closed/source/generation under the same operation lock, and no new lifecycle architecture or lock mechanism was introduced. A save completed before terminal admission is observed; an operation overlapping a lock-protected terminal read is explicitly rejected.

Three added regression cases failed against the original product, then passed after the fix:

- Pause close immediately before acquiring its transition lock; complete an actual Docker save; close must return and persist the acknowledged source and advance the current generation.
- Use the same schedule with reset; it must restore initial source while advancing from the saved generation, never the stale one.
- Overlap two resets with identical source bytes; both transitions must advance the generation. A session bound between them must fail stale-generation admission before container creation and verify absence during cleanup.

The synchronization wrappers only select scheduling points. Production state reads/writes, locking, saving, admission, Docker execution and cleanup remain real. `red-interleaving.log` records 3 failures in 10.66s, and `green-interleaving.log` records 3 passes in 10.17s. Exact command for each was `PYTHONPATH=src .venv/bin/pytest -q tests/test_environments_docker.py -k 'terminal_transition or overlapping_resets'`. The original reviewer’s failed reproduction and all earlier production evidence remain immutable.

A separate bounded [synthetic diagnostic](../evidence/M3/fix-round1/interleaving.py) passed five schedules in 7.733s: close/save and reset/save delayed before the terminal lock; close and reset paused after the actual workspace read as in the review schedule; and overlapping resets with stale admission. The first two run actual source edits through `execute_development`. In the exact pause-after-read schedule, the transition already holds the lock and the competing command fails with `EnvironmentError` rather than producing a save that could be lost. Close/reset final source, returned submission, generation and closed state are checked in every case. The stale session has no container ID, a typed `PolicyRejected`, and specific verified absence receipts.

The diagnostic ran trusted machine qualification with the existing image, and every owned operation ended in `removed` with empty recovery. Its [result](../evidence/M3/fix-round1/interleaving-result.json) retains complete qualification and execution/cleanup receipts, source/recipe/policy bindings, partial wall/CPU/memory costs, final workspace states, UTC timestamps and source/driver hashes. Private CAS/state remain under ignored `.feature-rl/research/M3/fix-round1/interleaving-1`. Exact command: `PYTHONPATH=src .venv/bin/python docs/evidence/M3/fix-round1/interleaving.py` (exit0).

Final focused verification passed **41 tests in 60.91s** using `PYTHONPATH=src .venv/bin/pytest -q tests/test_environments.py tests/test_environments_docker.py`; results and exact captured UTC/source/test/log bindings are in [verification.json](../evidence/M3/fix-round1/verification.json). Root owns the fresh integrated full-suite run after scoped re-review. No historical B/H test, build/import, provider/native/model call, acquisition or remote/paid compute was repeated for this state-only change.

The pinned image/dependencies, source policies, neutral setup and application semantics are unchanged. This is an implementation repair; Click counters remain **environment1/2, candidate1/4**. No feature, service, training or independent-review PASS is claimed.

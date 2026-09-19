# M2 authoring re-review — round 5

**Specification compliance: PASS. Implementation quality: PASS.** Residual C1 is closed. No new findings; the scoped M2 authoring review is ready for root's integration checkpoint.

Reviewed only the residual C1 product delta from `2493f090fe88924094b28b3ce7ae6ea8182026ab` to `6568a11e0fd85a9f2f4864acc4025030ff55901f`, with handoff documentation `bc249a9cbaac7a7ddad1a81bce673c541dca4cb5`. The preceding finding is recorded in [round 4](M2-authoring-round4.md).

The shared validator at `src/feature_rl/requirements/service.py:193` now requires the exact authoritative registration cost note produced by `src/feature_rl/generation/provider.py:990`: “Execution did not start because attempt registration failed; costs are unknown.” The remaining cost, disposition, request/schema, and status checks are unchanged.

The added `test_actual_registration_publication_replay_journals_without_execution` injects a one-time attempt-publication failure into the actual `LocalGenerationProvider`, passes its authentic error through `replay_error`, and resumes authoring with that recovered error. It verifies one rejected journal, zero backend verifications, zero runner calls, and zero recovery-provider calls. It therefore covers the producer/validator boundary that the previous hand-built fixture missed.

Read the source-bound [receipt](../evidence/M2/authoring-round4/receipt.json), [exit record](../evidence/M2/authoring-round4/focused-tests-exit.json), and retained stdout/stderr: **4 focused tests passed in 0.17s, exit 0**, with empty stderr. All three source-inventory byte counts/hashes match the assigned product/documentation commits; all three evidence-inventory byte counts/hashes match their retained files. This is inspected owner evidence, not a reviewer rerun.

C2, preflight recovery, and the B1/B2 bindings remain as previously reviewed; this one-line product correction does not alter them or any execution limits. No new tests, broad audit, suite, native/model/tokenizer/Docker/network/H/private check, product/index/commit mutation, or subagent was used.

**Execution status remains separate:** the three prior contract model calls exhausted the stage without a valid frozen contract or scenario. No H feasibility review is authorized. This scoped code-review pass does not change or qualify that construction outcome.

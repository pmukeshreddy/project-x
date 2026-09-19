# M2 authoring re-review — round 4

**Specification compliance: FAIL. Implementation quality: FAIL.** C2 is closed; C1 remains open for registration recovery. One P2 finding, no P0/P1 findings. This is the complete scoped batch.

Reviewed only the C1/C2 product delta from `d973d19801881b7eb1222973398abea1da4034eb` to `2493f090fe88924094b28b3ce7ae6ea8182026ab`, with handoff documentation `45f934e295051ff8f23a8644f6f738f4b6fa659d`. Prior findings and scope are recorded in [round 3](M2-authoring-round3.md).

## C1 remains open — P2: registration cost-note mismatch rejects the actual provider outcome

Locations: `src/feature_rl/requirements/service.py:187`–`:208`, `src/feature_rl/generation/provider.py:980`–`:1005`, and the new registration fixture in `tests/test_authoring.py:716`–`:725`.

The validator now admits the provider's complete `{attempt,status}` registration archive set, but requires exact equality with a hard-coded cost record whose note begins **“Generation did not start because attempt registration failed”**. The actual provider's registration failure records **“Execution did not start because attempt registration failed”**. `replay_error` preserves that cost. Therefore a genuine fully replayed registration failure still raises `ValueError: recovered registration archive disposition is invalid` before authoring writes its rejected journal.

The new fixture independently constructs a cost using the validator's “Generation” wording, so its passing test does not cover the actual provider result. This is the remaining part of C1, not a new requirement. Align validation with the authoritative provider cost and cover the real registration/replay path using the existing inert fixture pattern. Preserve unknown costs and avoid repeating generation.

The exact pinned source establishes this mismatch; no additional execution was needed. The original real-controller registration fixture and retained failure evidence are available in [round-three diagnostics](../evidence/M2/review-authoring-round3/diagnostics.json).

## Confirmed closures and retained behavior

- **C1 preflight variant:** the shared validator now accepts the complete `{attempt,preflight,cost,status}` shape and checks request/schema hashes, identities, rejection disposition, archived status/cost, response, input cap, and observed oversized components. These fields and the provider's cost wording agree.
- **C2:** decoded response limits now include base64 expansion of the declared raw-output bound plus bounded metadata; the outer CAS cap includes the second base64 envelope. The added 1,262,039-byte receipt fixture exercises the former 1-MiB failure. Native token, memory, wall/CPU, stdin, and raw-output execution limits are unchanged.
- **B1/B2 remain intact:** common attempt/schema-hash and status bindings precede variant-specific validation. Full outcomes retain exact request, schema, context, content, usage, and cost checks. Pending provider publication still propagates before journaling, and failed-generation/rejected-journal replay retains its no-repeat path. Both authoring services use the shared validator.
- Frozen public-check support and the `m4-sha256-v1` default are unchanged from the preceding reviewed product.

## Evidence and execution status

Read the retained [receipt](../evidence/M2/authoring-round3/receipt.json), [exit record](../evidence/M2/authoring-round3/focused-tests-exit.json), and stdout/stderr: the owner's focused command `PYTHONPATH=src ./.venv/bin/pytest -q tests/test_authoring.py tests/test_generation.py` reports **149 passed in 1.05s, exit 0**, with empty stderr. Mechanically verified all four source-inventory hashes against the assigned product/documentation commits and all three evidence hashes against the retained files; every byte count and hash matches. These passing tests are retained evidence, not a reviewer rerun, and do not override the C1 source mismatch.

No new tests, broad suites, native generation, tokenizer, Docker, network, production/private-store, H/history, or executable-application checks ran. Product, index, and commits were read only; no subagent was used. Cross-module candidate-budget enforcement remains M6's responsibility.

Actual construction remains unchanged: three contract model calls were consumed, the final response failed strict duplicate-key parsing, and no contract or scenario was frozen. No H feasibility review is authorized. That construction outcome is separate from this code verdict. The existing owner should finish the single C1 compatibility correction before root's integration checkpoint.

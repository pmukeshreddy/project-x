# M2 provider fix-round 4 re-review

Reviewed exact product `44e9dcf1079a186a8a0432fa51333d1d07f6422f` against assigned base `c9bbd8f2b55cd8ae363104bba19e209f62515b98` (product unchanged from previously reviewed `20af743`). Read the round4 brief first, the original provider brief, previous report/reproductions, current owned report/interfaces and complete 49,178-byte review package. Reviewer: `/root/review_m2_provider`, 2026-09-19. The current accountable implementation owner remains `/root/m2_authoring_recovery`.

**Specification compliance: FAIL pending two P2 findings below.** R1 scalar-union coercion is closed, R2 remains closed, and the original R3 examples now receive attributable pre-execution refusals. The admitted after-callback subset still changes validation semantics, and the new direct-return path accepts a model that violates its own set constraint.

**Implementation quality: CHANGES REQUIRED.** No P0/P1 finding was identified in this scoped review. Current-version native verification remains an unexecuted coordinator gate after code clearance.

## Findings

### R4 — P2: Restoring frozen models deduplicates sets after their cardinality constraints have already passed

Locations: `src/feature_rl/generation/provider.py:579`–`:580`, `:588`–`:603`, `:617`–`:621`, and the new direct return at `:637`. `set`/`frozenset` are admitted core forms at `:470` and `:486`.

The inert classes use object-identity hashing/equality, while restored frozen Pydantic models compare by their values. For a field `values: Annotated[set[Leaf], Field(min_length=2)]`, two equal Leaf objects therefore count as two during inert validation and collapse to one during restoration. The direct-return path runs no constraint check after that collapse. The same defect affects `frozenset`.

The independent full-provider diagnostic uses `Leaf(flag: Literal[True], label: str)`. Two distinct labels form a genuinely valid baseline and return two items. Two equal `{"flag":true,"label":"same"}` objects produce these results for both container forms:

| Check | Result |
|---|---|
| Ordinary caller JSON validation | Rejects: deduplicated size 1 is below minimum 2 |
| Inert validation | Accepts two distinct dummy objects |
| Full provider result | Returns size 1 with `record.success=true` |
| Archived usage | `accepted_response_usage=true` |
| Ordinary revalidation of the returned model | Rejects the same minimum-size violation |

This is a regression in the new pure-result path: restoration changes the value after validation and the result is now returned without the former final validation. Preserve actual equality/hash semantics before container constraints are evaluated, or refuse the affected structural form before backend verification. Apply the correction to both set forms and nested models; preserve the exact scalar-union behavior fixed in this round. Returning to an unrestricted second pass would reopen R1.

Evidence: `set_restoration` in [round4-restoration-diagnostics.json](../evidence/M2/review-round4/round4-restoration-diagnostics.json), produced by [reproduce-round4-restoration.py](../evidence/M2/review-round4/reproduce-round4-restoration.py). All successful and failed-call references resolve through the real ArtifactStore. This independently extends the coordinator's concrete set reproduction with full-provider and frozenset checks.

### R3 — P2: Admitted after callbacks can affect later validation, so stripping them still falsely rejects valid responses

Locations: `src/feature_rl/generation/provider.py:460`, `:549`–`:550`, `:633`–`:636`, `:672`–`:689`. The no-union after-callback capability is described in `docs/interfaces-M2.md:39`–`:41`.

The new admission logic refuses unions combined with lifecycle behavior, but it permits after callbacks whose output determines a later non-union check. The inert pass removes those callbacks before performing that dependent check. Two concrete caller-valid schemas still pass preflight and reach one synthetic backend/runner call before a `ValueError` labels their valid response as a Literal-type violation:

1. A supported `chain` consists of a string schema with an after validator `int`, followed by an integer schema. The model also has `flag: Literal[True]`. Its generated JSON schema requests a string for `number`, and ordinary strict JSON validation accepts `{"flag":true,"number":"3"}` as integer `3`. The inert graph removes the conversion and rejects the string against the subsequent integer node before ordinary JSON revalidation can run.
2. A list item has an after validator that raises `PydanticOmit` for `"skip"`; the enclosing list has `max_length=1`. Ordinary JSON validation of `{"flag":true,"values":["skip","keep"]}` returns `["keep"]`. The inert graph removes the item callback, counts two items and rejects before invoking the actual callback. This demonstrates that a chain-only refusal would leave the same dependency defect in collection constraints.

Both failure archives correctly record nonacceptance. The problem is the schema's admission and changed validation semantics after the call has started. An attributable pre-execution refusal is sufficient under the round4 brief; arbitrary callback support is not required. Correct the capability analysis for callbacks whose results affect later checks, or preserve their semantics. Keep the designated supported M0/proposal forms, JSON callback mode and single lifecycle execution intact.

Evidence: `remaining_R3_after_chain` in [round4-diagnostics.json](../evidence/M2/review-round4/round4-diagnostics.json), and `remaining_R3_omit_callback` in [round4-restoration-diagnostics.json](../evidence/M2/review-round4/round4-restoration-diagnostics.json). Both include accepted ordinary-JSON baselines, preflight admission, provider execution counts and the resulting failure. The collection case independently confirms the coordinator's concrete reproduction.

## Disposition and checked behavior

| Prior finding or contract | Round-4 result |
|---|---|
| R1 scalar-union coercion | **Closed.** Raw `1` returns integer `1` in `Literal[True] \| int`; raw `3.0` remains float `3.0` in `Literal[3] \| float`. Correct Literal alternatives also pass. Exact types and serialized output remain correct in nested tuples and the mixed model union. |
| R2 identifier/recovery bounds | **Remains closed.** The request/event/attempt/call-record models and request revalidation were unchanged. The previous maximum-ID/replay evidence remains applicable; unchanged ID diagnostics were not rerun. |
| R3 original before/post-init examples | **Original reproductions closed.** Before, wrap, referenced-before, plain, decorated callback-union and post-init-union schemas refuse as `GenerationSchemaUnsupportedError` before backend verification or runner invocation. Their ordinary-JSON baselines were verified first. The related after-callback dependency remains open above. |
| Unsupported-schema publication recovery | **Checked.** An injected schema-publication failure replays all eleven real-store references without execution, preserves `GenerationSchemaUnsupportedError`, and leaves generation unsuccessful. |
| Previously valid content forms | **Preserved in checked cases.** Boolean/model unions, patterned mappings, a nested RootModel, string-enum Literal, tuple and UTC transport pass. Numeric Boolean substitutes without a valid numeric alternative and non-UTC timestamps reject. |
| Supported lifecycle behavior | **Checked.** A default factory, post-init hook and after validator each run once; the after validator sees JSON mode. The specific coordinator JSON-mode regression is closed. |
| Actual M0 transport | **Checked with existing synthetic fixtures.** RequirementContract and ScenarioPlan pass through the provider. This establishes schema transport, not generated tasks, source/provenance resolution or runtime-backed qualification. |

The selected backend, worker, runner, resource enforcement and native event protocol were unchanged in this diff. The new findings concern content validation. No fallback model/backend, weakened execution mode, candidate execution or training claim was introduced. Sampled memory remains subject to its disclosed transient-overshoot limitation.

## Evidence and pending gate

The owner receipt records **79 focused / 234 full tests passed**, exit 0, zero native calls. The reviewer matched all eight source/test files, both owned documents and both stdout logs against the receipt and exact reviewed commit. The owner declares empty stderr without a separate stderr artifact path. These are retained owner runs; no whole suite was rerun by the reviewer.

The reviewer ran two narrow diagnostics and preserved their separate raw outputs:

```sh
PYTHONPATH=src:tests .venv/bin/python -B docs/evidence/M2/review-round4/reproduce-round4.py
PYTHONPATH=src:tests .venv/bin/python -B docs/evidence/M2/review-round4/reproduce-round4-restoration.py
```

Both exited 0 with empty stderr files. Exit 0 means their assertions reproduced the recorded outcomes, including these defects; it does not mean provider approval. They use trusted schema definitions, explicitly identified backend/runner doubles and temporary real ArtifactStore publication/resolution. [review-verification.json](../evidence/M2/review-round4/review-verification.json) binds commands, observed exits, source/package/output hashes, owner-evidence checks and protected-path preservation.

No native/MLX inference, model load, download, install, remote compute, historical/candidate execution or product/index/branch/commit edit occurred. Only this report and new reviewer evidence under `docs/evidence/M2/review-round4/` were written. Earlier reports and all raw receipts remain preserved. The same current M2 owner should fix R3/R4, followed by scoped re-review. Current-source native verification remains pending after code clearance; immutable v1 smokes cannot substitute for it. Larger envelopes remain unqualified, and full M2 remains partial until actual runtime-backed contract/scenario authoring is implemented and reviewed.

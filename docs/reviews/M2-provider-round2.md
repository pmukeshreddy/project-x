# M2 provider fix-round 2 re-review

Reviewed exact product commit `3f859ca991c8965fda48e9d95588e9be12b0fdab` against previously reviewed `7239379ebf6921fd816b1bded9bb07a685b84c1a`. Reviewed the prior R1/R2 report, actual protocol/provider/test changes, interfaces, owner report and fix-round 2 receipt. The tighter supplied `review-9cea0bd..3f859ca.diff` contains the same complete product correction scope; the intervening product baseline is unchanged. Reviewer: `/root/review_m2_provider`, 2026-09-19.

**Specification compliance: FAIL pending the two P2 corrections below.** Exact raw event Literals and the original oversized-instruction failure are corrected. The new generic content guard still admits numeric Literal coercion, and rejection metadata is not bounded for every validated request.

**Implementation quality: CHANGES REQUIRED.** No P0/P1 defect was identified in this scoped correction review. Current-version native execution remains an unexecuted coordinator gate after review clearance.

## Remaining findings

### R1 — P2: The new content guard can validate the wrong union branch and skips patterned dictionary values

Locations: `src/feature_rl/generation/provider.py:478`, `:487`, `:495`, `:519`; final Pydantic conversion at `:567`. The exact-content claim is in `docs/interfaces-M2.md:39`.

For `mode: Enabled | Disabled`, where `Enabled.flag` is `Literal[True]` and `Disabled.flag` is `Literal[False]`, the provider accepts raw `{"mode":{"flag":1}}` and returns `Enabled(flag=True)`. Both union branches have JSON type `object`. The guard rejects the Enabled branch's numeric Boolean, then accepts the Disabled branch because `1 != False` causes no literal-type error. That branch is not semantically valid for the supplied value. Pydantic subsequently selects Enabled and converts the number to `True`. Branch selection in the guard and actual content validation therefore disagree.

A second supported Pydantic shape exposes another omission: `flags: dict[Annotated[str, Field(pattern="^k_")], Literal[True]]` generates `patternProperties`, which the object walker does not inspect. Raw `{"flags":{"k_flag":1}}` is likewise accepted and converted to `True`.

Both reproductions ran through the full provider with clearly identified test doubles and the real ArtifactStore. Each returned `record.success=true` and published `accepted_response_usage=true`; every archive reference resolved. Correct Enabled/Disabled alternatives, correct patterned values and flat Literal values also passed. The previously reported flat `1`/`3.0` violations now reject. These are synthetic boundary diagnostics, not real-worker output claims.

Make raw-type enforcement agree with the branch that actually validates, and cover generated patterned dictionary values. If a schema form cannot be checked soundly, explicitly reject it before execution instead of silently skipping its raw-type constraint. Preserve valid alternatives and strict JSON tuple/enum/time transport. Add regressions with valid baselines before mutating fields; the bare `worker_events()` identity fixture contains `AUTO` digests, so decoding that unchanged fixture must first be made valid when testing identity-field guards. This is a local M2 boundary correction; no shared M0 schema or dependency change is required.

Reproduction: `structured_content.union_numeric_boolean_STILL_ACCEPTED` and `structured_content.patterned_mapping_numeric_boolean_STILL_ACCEPTED` in [round2-diagnostics.json](../evidence/M2/review-round2/round2-diagnostics.json), produced by [reproduce-round2.py](../evidence/M2/review-round2/reproduce-round2.py). The union result independently confirms the coordinator's separately preserved reproduction.

### R2 — P2: Oversized identifiers are copied into the supposedly bounded rejection and recovery payloads

Locations: `src/feature_rl/generation/provider.py:624`–`:631`, `:658`–`:669`, `:695`, `:813`; retained rejection payloads at `:723` and `:829`. The bounded-rejection promise is in `docs/interfaces-M2.md:78`.

`GenerationRequest` accepts an unbounded `Identifier` string. A request with `request_id="R" * 1_048_577` passes validation and exceeds the maximum admitted 1 MiB stdin budget. The provider correctly rejects it without backend verification or execution, but copies the entire oversized identifier into several archives:

| Archive | Actual bytes |
|---|---:|
| attempt | 1,050,670 |
| preflight | 1,049,215 |
| status | 1,049,499 |

These objects have no independent fixed bound: increasing the identifier increases the copies without limit. Injecting a preflight-publication failure retains the same oversized attempt/preflight byte payloads in `GenerationPublicationRecovery`; replay publishes them and another oversized status. The error record also retains the complete identifier. This defeats the new path's specific guarantee that resource rejection does not retain oversized input under another metadata field.

Bound M2 identifiers or use a bounded attributable representation for rejected oversized identifiers, including the error record, attempt/preflight/status and recovery. Keep ordinary IDs, source/request identities, sizes and the typed failure useful; avoid changing shared M0 contracts merely to repair this local limit. Verify the maximum-sized identifier and the recovery path as well as oversized instruction/schema content.

Reproduction: both `preflight.oversized_identifier_*` cases in [round2-diagnostics.json](../evidence/M2/review-round2/round2-diagnostics.json). The diagnostic resolves the actual stored objects and records their lengths without preserving the bulky identifier in its output. Backend verification and runner calls both remain zero.

## Disposition and verification

| Prior requirement | Round-2 result |
|---|---|
| Exact Boolean/integer Literal event types | **Closed.** Seven valid event baselines decoded. All 23 numeric substitutions across all 15 Boolean/integer Literal fields reject before conversion, including protocol versions and input-rejection flags. `BeforeValidator` preserves the original strict identity/freshness requirements. |
| Exact flat/nested structured content | **Flat and simple referenced objects corrected; related generic requirement remains open as R1.** The new union/patterned forms still coerce numeric values. Valid alternatives were retained in the diagnostic. |
| Original oversized-instruction rejection | **Original reproduction closed.** It now raises `GenerationProviderError` with an attributable attempt/preflight/cost/status record, `GenerationInputLimitError`, unknown unmeasured costs and zero backend/runner calls. No instruction body is archived. |
| Preflight publication recovery | **Ordinary case works; unbounded identifier case remains R2.** Independent failures at attempt, preflight, cost and status publication all replay to a complete failed-call record without execution. All real ArtifactStore references resolve, request hashes match the actual request, and the underlying input-limit cause is preserved. |

The previously closed F1–F5 paths were not repeatedly exercised without a new concern. The worker, runner, backend and shared schemas were unchanged in this correction scope. No backend fallback, training claim, score reinterpretation or memory-limit strengthening was introduced. The sampled-memory limitation and tiny-profile qualification remain as previously reviewed.

The owner's retained receipt records **58 focused / 213 full tests passed**, zero native calls. All eight product/test hashes match both the receipt and exact reviewed commit; the retained test-output hashes also match. These are owner runs, not suites rerun by this reviewer.

The reviewer executed only this narrow new diagnostic:

```sh
PYTHONPATH=src:tests .venv/bin/python -B docs/evidence/M2/review-round2/reproduce-round2.py
```

It exited 0 with empty stderr. Exit 0 means its assertions reproduced the recorded outcomes, including the defects above; it is not provider approval. The diagnostic uses backend/runner doubles and temporary real artifact storage. [review-verification.json](../evidence/M2/review-round2/review-verification.json) preserves command, exit status, source/output hashes, owner-receipt checks and the unchanged protected-path check.

No MLX/native inference, model load, download, install, remote compute, historical/candidate code execution or whole-suite rerun occurred. Only this report and new `docs/evidence/M2/review-round2/` files were written by this reviewer; prior receipts/reviews, product, index, branch and commits were preserved. The same M2 owner should address these two findings, followed by scoped re-review. The coordinator's current-version source-bound native verification remains unexecuted. Immutable v1 receipts do not prove protocol v3, larger envelopes remain unqualified, and full M2 remains partial until actual runtime-backed contract/scenario authoring is delivered.

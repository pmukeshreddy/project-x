# M2 provider fix-round 3 re-review

Reviewed product `20af743778815635bb4af521164199bd8f09dcb8` against assigned fix base `b516c63799539c00ba711c07254de885a4a4310d` (product unchanged from the previously reviewed `3f859ca`). Read the provider brief, explicit ownership transfer, prior review, current report/interfaces and complete M2-owned change package. The supplied 66,275-byte package matches SHA-256 `48117c671b014ee2477837f48d5d19bc65dfc89fcc0413b3181330f749681fab`. Review ownership remains `/root/review_m2_provider`; current implementation ownership is `/root/m2_authoring_recovery`. Reviewer date: 2026-09-19.

**Specification compliance: FAIL pending the two P2 findings below.** R2's identifier/recovery defect is closed. R1's original model-union and patterned-map examples are closed, but exact Literal branch preservation remains incomplete for scalar unions. The new callback boundary also admits schemas whose valid output it subsequently rejects.

**Implementation quality: CHANGES REQUIRED.** No P0/P1 defect was identified in this scoped correction review. Current-version native verification remains an unexecuted coordinator gate after code clearance.

## Findings

### R1 — P2: The second validation pass still changes a selected scalar union branch

Locations: `src/feature_rl/generation/provider.py:543`, `:578`, `:587`–`:590`. The branch-preservation claim is in `docs/interfaces-M2.md:39`.

The new restoration preserves selected model classes, but scalar alternatives have no corresponding branch identity. `_LiteralPrevalidator.validate_json` then calls the original `model_validate`, which repeats the equality-based Literal coercion that the inert validator had excluded:

| Caller field | Raw JSON value | Inert validator's selected value type | Returned value |
|---|---|---|---|
| `flag: Literal[True] \| int` | `1` | `int` | `True` (`bool`) |
| `value: Literal[3] \| float` | `3.0` | `float` | `3` (`int`) |

Both full-provider reproductions returned `record.success=true` and published `accepted_response_usage=true`. Every real ArtifactStore reference resolved. The raw inputs are valid members of their numeric alternatives; the failure is returning a different Literal type after the guard selected the valid numeric branch. This is the same original R1 boundary requirement, now exposed without model alternatives.

Preserve the exact selected scalar alternative through final content validation, including nested containers. Add assertions on returned Python types and serialized values, alongside valid Boolean/integer alternatives. A negative assertion that merely rejects every mixed union would not demonstrate that valid alternatives retain their semantics. If a particular form cannot be supported soundly, define and refuse it before execution rather than silently returning a different branch.

Evidence: `remaining_scalar_union_coercions` in [round3-diagnostics.json](../evidence/M2/review-round3/round3-diagnostics.json), produced by [reproduce-round3.py](../evidence/M2/review-round3/reproduce-round3.py). The receipt records the inert value type separately from the full provider's returned type.

### R3 — P2: Callback-sensitive schemas pass preflight but valid content fails under altered validation semantics

Locations: `src/feature_rl/generation/provider.py:455`–`:478`, `:504`–`:505`, `:538`, `:587`–`:590`, and pre-execution admission at `:978`.

The advertised unsupported-schema boundary handles function validators directly inside a union, but its analysis is incomplete for other supported callback forms. Two independent full-provider examples pass schema preflight and execute the synthetic runner before falsely rejecting output that the caller's ordinary strict JSON validator accepts:

1. A union's first model has `flag: Literal[True]` and a `model_post_init` hook that raises `ValueError`; its second model has `flag: bool`. Ordinary `model_validate_json` on `{"branch":{"flag":true}}` returns the second model. The prevalidator removes the post-init hook, selects the first model and restores that model instance. Final validation invokes the hook and rejects that branch, while the fallback cannot consume an instance of the other model. The union detector inspects function-validator nodes but does not recognize this post-init dependency.
2. A non-union model has `flag: Literal[True]` and an integer field with a `mode="before"` field validator that normalizes a decimal string. Its declared JSON validator accepts `{"flag":true,"number":"3"}` and returns integer `3`. The inert prevalidator removes the callback and rejects the string against the integer node before the actual callback can run. This schema is outside the documented unsupported forms, yet the provider reports an invalid model response after execution.

Both cases produce `ValueError` and one backend-verification/runner-double call, rather than a pre-execution `GenerationSchemaUnsupportedError`. The failure archives correctly record nonacceptance, but that accounting does not repair the changed caller-schema semantics or the wasted call.

Either preserve these lifecycle/validation semantics within the declared supported subset, or identify unsupported forms completely and reject them before backend verification. Align the interface with that boundary and test both accepted ordinary JSON and the refusal point. Preserve the working M0 tuple/enum/UTC transport and the single-execution lifecycle behavior demonstrated below; no shared M0 schema or dependency change is required.

Evidence: `callback_semantics.PostInitUnion` and `callback_semantics.BeforeFieldContent` in [round3-diagnostics.json](../evidence/M2/review-round3/round3-diagnostics.json). Each includes the accepted ordinary-JSON baseline, callback observations, provider failure and execution count. These are trusted synthetic schema diagnostics, not model/native output claims.

## Previous finding disposition and retained behavior

| Requirement | Round-3 result |
|---|---|
| R1 original Boolean-model union and patterned mapping | **Original reproductions closed.** Correct Boolean alternatives and patterned values pass; numeric Boolean substitutes reject. The mixed model union now returns `NumberAlternative(flag=1)` with an actual integer. Scalar unions remain open above. |
| R2 unbounded request/response/prompt IDs | **Closed.** All three fields reject both 129-byte and the original 1,048,577-byte identifiers. Copied and constructed requests for every field reject at public-boundary revalidation before any archive, backend or runner call. |
| R2 maximum-ID preflight and replay | **Closed.** With all three IDs at 128 characters, normal rejection and failures at attempt/preflight/status publication preserve attribution and recover without execution. All store references resolve, the request hash matches the supplied request, and the underlying `GenerationInputLimitError` survives. The largest retained/published payload is 2,463 bytes with a 4,096-byte input cap. |
| Event identity validation | **Preserved.** Seven genuinely valid baselines decode; all 21 oversized ID mutations reject, and maximum IDs decode. The product's numeric-Literal tests now validate their baseline after replacing the invalid `AUTO` hashes. Earlier exact event guards remain intact. |
| JSON compatibility | **Preserved in checked forms.** String-enum Literal, tuple and UTC fields pass; a non-UTC timestamp rejects. Complete existing synthetic M0 RequirementContract and ScenarioPlan fixtures pass through the provider. This verifies schema transport only, not authored tasks, resolved provenance or runtime-backed qualification. |
| Lifecycle and explicit unsupported forms | **Simple checked cases work.** A default factory, post-init hook and after validator each run once on accepted content. Plain-validator and decorated callback-union schemas produce typed unsupported errors with zero backend/runner calls. Other admitted callback forms remain open as R3. |

The backend, worker and runner were unchanged in this correction scope. No backend substitution, weakened native resource mode, score reinterpretation or training claim was introduced. Previously reviewed cleanup, immutable publication, observed-cost and memory-sampling limitations remain in force. Prior whole-suite and closed runner diagnostics were not repeated.

## Evidence and remaining gate

The owner's retained receipt records **66 focused / 221 full tests passed**, exit 0, with zero native calls. The reviewer independently matched all eight source/test files, both owned documents and both retained stdout logs against the receipt and exact product commit. The owner's receipt declares empty stderr and contains no separate stderr artifact path. These are retained owner test runs, not new reviewer suite runs.

The reviewer ran this narrow diagnostic:

```sh
PYTHONPATH=src:tests .venv/bin/python -B docs/evidence/M2/review-round3/reproduce-round3.py
```

It exited 0 with an empty reviewer stderr file. Exit 0 means assertions matched the recorded outcomes, including the remaining defects; it does not mean provider approval. The diagnostic uses explicit backend/runner doubles, actual temporary ArtifactStore publication/resolution, and trusted schema fixtures. [review-verification.json](../evidence/M2/review-round3/review-verification.json) retains the command, observed exit, package/source/output hashes, owner-evidence checks and protected-path preservation check.

No native/MLX inference, model load, download, install, remote compute, historical/candidate execution, product/index/branch/commit edit or whole-suite rerun occurred. Only this report and new evidence under `docs/evidence/M2/review-round3/` were written by the reviewer. Earlier reports and raw evidence remain preserved. The current M2 owner should fix these findings, followed by scoped re-review. The coordinator's current-version source-bound native verification remains pending; immutable v1 smokes cannot substitute for it. Larger envelopes remain unqualified, and full M2 remains partial until actual runtime-backed contract/scenario authoring is implemented and reviewed.

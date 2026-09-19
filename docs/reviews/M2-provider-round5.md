# M2 provider fix-round 5 re-review

Reviewed exact product `e14f67983db678813afcfeb191532e691b0b20e3` against assigned base `840e11407d6c0c15548678fef2f2102dd58d100e`. Reviewer: `/root/review_m2_provider`, 2026-09-19. Accountable implementation owner: `/root/m2_authoring_recovery`.

**Specification compliance: PASS for the scoped provider repair.** R3 and R4 are closed under the explicitly authorized capability boundary. R1 and R2 remain closed. This is code clearance for this slice; it does not complete M2 or satisfy the pending coordinator native gate.

**Implementation quality: PASS.** No open P0, P1 or P2 finding was identified in this changed scope. The narrowed guard removes the two unsound approximations from round 4, and supported validation retains the checked exact types and ordinary callback semantics.

Read the round5 brief first, the final round4 report and both retained reproducers, the coordinator validation decision, current provider/interfaces/report, the owner tests and complete 40,451-byte product/test/document diff. The package SHA-256 is `9df907c52ffc5a44606663ef871d9dda8f92d903d4aada5ed17747cff88faef1`. The four changed product paths are `generation/provider.py`, `tests/test_generation.py`, `docs/interfaces-M2.md` and `docs/reports/M2-provider.md`; the remaining integrated-range material does not conceal another generation source change.

## Findings disposition

| Finding | Round-5 disposition and evidence |
|---|---|
| R1 — equality-compatible Literal coercion and branch reselection | **Remains closed.** `provider.py:568` checks exact types at each Literal node, and `:659` returns the one validated/restored result without a second branch-selection pass. Independent full-provider controls keep raw integer `1` as `int` in `Literal[True] \| int`, raw `3.0` as `float` in `Literal[3] \| float`, and raw model `flag: 1` in `NumberAlternative`. Boolean alternatives, nested tuple alternatives, recursive model references and patterned mappings pass with their intended types; numeric substitutes without a valid alternative reject. Multi-member `Literal[True, 1]` and `Literal[3, 3.0]` controls retain the explicitly valid exact member. |
| R2 — oversized identities/input attribution and recovery | **Remains closed.** Request/event/attempt/call-record models, identifier bounds, public request revalidation and input-limit handling are unchanged in this diff. Prior maximum-identifier and bounded-input evidence remains applicable; those unchanged diagnostics were not rerun. The new unsupported-schema path also completed bounded publication/replay in the independent check below. |
| R3 — after-callback dependencies falsely reject valid responses | **Closed.** `provider.py:476` admits no callback/default/chain core forms; `:669` rejects unsupported forms before backend verification. The original numeric-Literal chain baseline accepts `number: "3"` as integer `3`, and the original omission baseline accepts `["skip", "keep"]` as `["keep"]`. Both now receive attributable `GenerationSchemaUnsupportedError`, with zero backend/runner calls. The omission callback does not run on refusal. Matching string-Literal schemas use the original validator and succeed; the omission callback runs once per item. This addresses the cause across callbacks, rather than merely denying chain nodes. |
| R4 — restored model sets collapse after cardinality checks | **Closed.** `provider.py:686` rejects any guarded set/frozenset form, including nested/reference forms, before execution. Set reconstruction was removed from `:629`. Independent direct and nested `set`/`frozenset` cases first establish that two distinct labels are ordinarily valid while duplicate value-equal leaves fail `min_length=2`. All eight provider cases then refuse the unsupported schema before backend/runner invocation. There is no successful record containing an invalid one-element result. |

The earlier F1–F5 repairs remain outside this change's altered implementation and have no newly observed regression. The selected backend, worker, runner, resource enforcement, strict event models and protocol version are unchanged.

## Changed boundary and positive controls

`provider.py:445` distinguishes numeric/Boolean Literals requiring extra exactness from strings, null and string-valued enum Literals. Schemas needing no numeric guard return `None` at `:679` and use the original `schema.model_validate_json` path in `_parse_envelope`. Unsupported Literal domains are rejected even when there is no numeric guard: Decimal Literals and non-string-valued enum Literals do not inherit Pydantic's equality coercion.

For the numeric/Boolean subset, the recursive feature analysis sees nested schemas and definitions. Callbacks, chains, defaults, post-init/custom-init hooks, custom serialization, dataclass/call/unknown forms, sets/frozen sets, non-string-key core schemas and `extra='allow'` models are refused. The independent checks include Decimal Literal, root and nested extra-allow models, defaulted fields, integer dictionary keys and field serialization, each with a valid ordinary-JSON baseline followed by zero-execution refusal. Extra-ignore models preserve declared data while discarding extras; extra-forbid models accept valid content and reject extras. These controls verify the root's newly reported Decimal and extra-field cases as well as the prior findings.

This is an explicit limited capability, as documented at `docs/interfaces-M2.md:39`–`:43`. In particular, the guarded dictionary gate requires a direct `str` key core (patterns are supported); numeric guarding does not promise arbitrary string-producing key validators or key representations. Unsupported numeric-Literal lifecycle/default schemas require an explicit supported proposal or a separately reviewed extension. No schema conversion, alternative backend, weaker checker or weakened task gate was added.

The original-validation path preserves a combined before-validator, default factory, post-init hook and after-validator exactly once; the after-validator sees JSON mode. Actual `RequirementContract` and `ScenarioPlan` models accept the existing complete synthetic test fixtures through the full provider. A string-enum Literal, tuple and UTC timestamp retain their intended types, while a non-UTC timestamp rejects. These are transport checks using existing unit fixtures, not authored features or empirical qualification evidence.

An injected failure while publishing an unsupported numeric-Literal schema produces `ArchivePublicationError` with recovery state. `replay_publication` resolves all eleven real-store archive references, preserves the underlying `GenerationSchemaUnsupportedError`, leaves `success=false` and `accepted_response_usage=false`, and makes no backend/runner call. Every other full-provider case also resolves all eleven references and checks publication, success and accepted-usage consistency.

## Verification and remaining gates

The reviewer ran one narrow diagnostic:

```sh
PYTHONPATH=src:tests .venv/bin/python -B docs/evidence/M2/review-round5/reproduce-round5.py
```

It completed **44 cases, exit 0, empty stderr**, using trusted schemas, explicit backend/runner doubles and real temporary ArtifactStore publication/resolution. [round5-diagnostics.json](../evidence/M2/review-round5/round5-diagnostics.json) contains the raw per-case results, ordinary-validation baselines, execution counts, archive hashes and observed UTC/source identities. [diagnostic-execution.json](../evidence/M2/review-round5/diagnostic-execution.json) records the actual command, timestamps, exit and separate stdout/stderr hashes. The test event/token/cost fixtures are synthetic and do not represent native measurements or qualified tasks.

The owner receipt records **98 focused tests passed / 253 full tests passed**, exit 0, zero native calls. The reviewer mechanically matched all **24** recorded source/test/document/stdout/stderr files to both the receipt and exact product commit, checked the complete review-package identity, and confirmed that product paths and all prior reviewer reports/evidence remain unchanged against that commit. [review-verification.json](../evidence/M2/review-round5/review-verification.json) retains these checks. The precommit owner receipt does not itself claim a committed revision; the independent file comparisons provide the exact binding. No unchanged full suite was rerun.

M0 was concurrently adding optional bounded artifact reads. During this diagnostic, `artifacts/__init__.py` and `artifacts/store.py` were dirty but their source hashes were stable before and after execution; both hashes and the dirty status are in the raw evidence. This check exercised default store reads only. It does not approve the separate M0 extension, and the owner's earlier 253-test run predates that integration.

No native/MLX inference, model load, download, install, remote compute, historical/candidate execution or product/index/branch/commit edit occurred. Only this report and new reviewer evidence under `docs/evidence/M2/review-round5/` were written. Earlier receipts remain preserved.

The coordinator's fresh integrated full suite, independent M0 bounded-read clearance and one current-source native verification remain pending. Immutable protocol-v1 smokes cannot substitute for protocol-v3 execution/provenance evidence. Larger envelopes remain unqualified, sampled memory retains its disclosed transient-overshoot limit, and full M2 remains partial until actual runtime-backed contract/scenario authoring is implemented and reviewed.

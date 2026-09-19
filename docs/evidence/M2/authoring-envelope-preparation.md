# M2 authoring envelope preparation

This note is bounded preparation for the later M2 authoring slice. It inspected only the author-visible M1 request, B archive and license references, plus inert B source spans. It did not read `SourcePair`, H/reference content, diffs, history or private cases. It did not import or execute Click, load model weights, run inference, access the network, download anything or draft a contract.

## Admissible inputs and measured sizes

The retained M1 result labels the request `reconstructed_specification` and exposes this `AuthoringSourceView`:

| Reference | Artifact SHA-256 | Decoded payload |
| --- | --- | ---: |
| request evidence | `d8d8a97861a597c7bfe181dfd3bbd6dbf72b7854ee13d555add1b1e07f0d2c61` | 5,280 bytes |
| B source archive | `4ff1d8a5d478ffb12950ec2661f3b38be5b7c9925691726c27b83234c5814575` | 1,546,240 bytes |
| license text | `b84ace5d4d01f55ab2db4e25ffddb9239104176e99a73e4799dcadbd8e612ab9` | 1,475 bytes |

The B archive contains 149 files and 1,408,900 uncompressed file bytes. Sending the archive wholesale would violate attributable retrieval discipline. Inert inspection identified an initial narrow context: the complete request; `src/click/core.py:1532-1560,1878-1965`; `src/click/exceptions.py:212-243`; `tests/test_options.py:136-160`; `tests/test_commands.py:8-41`; `docs/commands-and-groups.md:72-100`; and the license text. These spans cover `Group`, command resolution, the existing option-suggestion behavior, ordinary command invocation and public group documentation. They do not establish suggestion ordering, normalization, exact wording or a public exception API.

| Selected text | Bytes | Pinned-tokenizer tokens, without chat template |
| --- | ---: | ---: |
| request evidence | 5,280 | 1,906 |
| `core.py` spans | 5,436 | 1,159 |
| `exceptions.py` span | 1,022 | 230 |
| option suggestion test | 771 | 186 |
| ordinary command regression span | 849 | 212 |
| group documentation span | 621 | 144 |
| license | 1,475 | 297 |
| total selected text | 15,454 | 4,134 summed per span |

For measurement only, I assembled a `GenerationRequest(stage="initial_authoring")` around those exact texts and the real M0 `RequirementContract` output schema. The request JSON was 19,547 bytes; the canonical `RequirementContract` JSON schema was 8,347 bytes; the provider prompt was 27,916 UTF-8 bytes and 7,661 tokens before chat templating. The exact worker chat template added 8 tokens, for **7,669 input tokens**. The current qualified native smoke was 450 input and 92 output tokens, and the qualified tiny profile permits at most 2,048 input and 128 output tokens. The first real contract envelope therefore requires a separately qualified larger profile.

Tokenizer-only measurement verified model ID `mlx-community/Qwen3-4B-Instruct-2507-4bit`, revision `50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b`, manifest SHA-256 `697253a717e5857f1dfe3c14594f747c9c8118e6bc9c877bfc0c6faa6a7f50a0`, tokenizer SHA-256 `aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4`, Transformers 5.17.0 and tokenizers 0.23.2. Every local tokenizer/config/template asset was checked against the pinned manifest before `AutoTokenizer` was opened with `local_files_only=True` and `trust_remote_code=False`. No weight file or model class was loaded.

The synthetic M0 examples provide shape-only lower bounds: `RequirementContract` is 1,707 bytes / 429 tokens and `ScenarioPlan` is 1,583 bytes / 376 tokens. They contain one short requirement or scenario and are not Click drafts. A real contract with multiple evidence links, compatibility obligations and ambiguity decisions is provisionally estimated at 2,000–4,000 output tokens. Reviewed M3 discovery observations are likely to add roughly 2,048–4,096 input tokens, producing an estimated first contract input of about 9,717–11,765 tokens. These ranges are planning estimates; only the 7,669-token envelope above was measured.

## Concrete interface and capability gaps

The provider accepts a caller-selected final `StrictModel`, while M0 `RequirementContract` and `ScenarioPlan` include artifact-envelope fields whose truthful values are controller-owned: `kind`, schema version, visibility, provenance and measured costs. Generation evidence and token costs do not exist until the provider call completes. A model cannot truthfully emit the final artifact directly before those values exist.

The later M2 implementation needs a narrow proposal/finalization boundary derived from the actual M0 field annotations, rather than a handwritten shadow copy of either artifact. The controller should exclude its envelope fields when deriving the proposal schema, supply fixed contract/scenario inputs itself, merge the proposal only after the call, and validate the complete result with the real M0 class. This keeps `EvidenceLink`, `Requirement`, `AmbiguityDecision`, `AllowedChanges`, `Scenario`, `SeedPolicy` and all nested constraints authoritative in one place. The final provenance and `CostRecord` values then come from the actual provider and discovery receipts.

`AuthoringSourceView` also exposes `license_text`, but initial `AuthoringContext.role` has no license role. Labeling it as baseline would blur source meaning. The safest minimal path is to validate and retain the license reference deterministically outside the model prompt unless a reviewed provider-interface extension adds an explicit license role. No change is made here.

The M0 `ScenarioPlan` validator cannot dereference its contract, so exact equality between `mandatory_requirement_ids` and the frozen contract's mandatory feature plus compatibility IDs remains a joined service check. This needs no M0 schema change, but M2 must enforce it before storing the plan.

## Minimal later implementation proposal

1. Accept only the actual M1 `AuthoringSourceView` and the reviewed M3 discovery result. Resolve bytes with author-role access, independent envelope/payload caps and an explicit retrieval allowlist. Every context carries its real artifact reference, exact locator, exact text and provenance label.
2. Derive a proposal model from the real M0 `RequirementContract` field definitions while withholding controller-owned envelope/provenance/cost fields. Fix request text, provenance label, public checks and episode limits from validated inputs where appropriate; do not ask the model to invent them.
3. Generate the semantic contract proposal through the production provider, validate every quote and locator against an allowlisted resolved source, reject unsupported observables or critical unresolved ambiguity, add actual provenance/costs, and validate/store the final M0 `RequirementContract`. Freeze that first draft before any privileged review.
4. After the frozen contract and reviewed runtime capability handoff exist, derive the scenario proposal from the real M0 `ScenarioPlan` fields. The controller fixes the contract reference, exact mandatory ID set and seed policy; generated scenarios provide the M0 preconditions, actions, observations, expected relation, input domain, oracle origin and reset needs. Final joined validation checks IDs and meaningful coverage before storing the M0 artifact.

This proposal does not name or assume an M3 method and does not freeze Click task semantics.

## Bounded larger-envelope measurement plan

1. After M3 review, serialize the exact discovery result and retrieval expansions, then rebuild the exact contract request. Record request bytes, schema bytes, each context span and aggregate decoded bytes.
2. Reverify the pinned manifest and tokenizer assets and run the offline tokenizer-only chat-template measurement. Repeat for the scenario request only after the frozen contract exists. No model load is needed for this step.
3. Set immutable per-stage caps from those measurements before execution. A provisional contract ceiling is measured input plus 20% headroom rounded up to the next 1,024 tokens, with a 4,096-token output cap; replace both with recorded exact values before comparison batches. Keep the provider's total context, stdin, output, wall, CPU, memory and file limits intact.
4. The previous provider native budgets are consumed. Before the first real authoring call, obtain a separately declared larger-envelope qualification allocation. Run one nontraining current-source qualification at the frozen limits, recording input IDs/count, emitted output, truncation, wall/CPU time and active/peak/cache memory. Failure, truncation or a memory-policy breach blocks authoring rather than widening limits silently.
5. Freeze the measured profile for the initial contract and preserve every attempt. Apply the declared initial-plus-two-stage-repair and four-total-repair ceilings; each retry records its cause and changed input. Scenario generation receives its own measured envelope after contract freeze.

Until that qualification and the reviewed M3 handoff exist, actual contract/scenario generation remains blocked by a known capacity gate rather than by an M0 or M1 data-access gap.

# M2 authoring/scenario review — round 1

**Specification compliance: FAIL. Implementation quality: FAIL.** No P0 findings; three P1 findings and two P2 findings at the reviewed checkpoints. The same M2 owner is already addressing this batch. Uncommitted follow-up fixes are not approved by this report.

## Scope and evidence

Reviewed implementation base `4530ad7` through product target `c4ed70df6f3218ee2280b346eedb57945d0a44ab`: all new `requirements/**`, `scenarios/**`, their public exports, the scenario-planning generation-stage changes, and the dedicated authoring/generation tests. The supplied aggregate diff is `.superpowers/sdd/implementation-plan/review-4530ad7..c4ed70d.diff`; unrelated root documentation and the independently owned M6 core are outside this product verdict. Also reviewed the reproduction delta `d267208` and the subsequently assigned scenario-driver fix `aa7508617a195bdf88e60581cbb3d9f4fac3fd0e`.

Binding references: `feature_rl_pipeline.md` §§5–6.4, `codex_multi_agent_implementation_prompt.md`, `docs/implementation-plan.md`, `docs/briefs/M2-authoring.md`, `docs/briefs/M2-authoring-review.md`, `docs/decisions/M2-proposal-finalization.md`, `docs/decisions/M2-authoring-execution-budget.md`, and the code-completion/review-policy rulings. Inspected relevant actual M0 schemas/storage interfaces, M1 `AuthoringSourceView`, M3 runtime source/build/recipe checks, and the existing provider publication-recovery implementation.

The reviewer ran only the labeled synthetic diagnostic in [reproduce.py](../evidence/M2/review-authoring-round1/reproduce.py). The final run explicitly imports the reviewed M2 Git bytes from a temporary directory beneath the assigned review-evidence directory, so concurrent owner edits do not change its result. Shared upstream dependencies were not changed by the diagnostic. Command:

```sh
PYTHONPATH=src .venv/bin/python -B docs/evidence/M2/review-authoring-round1/reproduce.py
```

Final diagnostic execution: **exit 0, 0.207407 seconds**. [diagnostics.json](../evidence/M2/review-authoring-round1/diagnostics.json) records source/fixture hashes and the outcomes; [diagnostics.stderr.txt](../evidence/M2/review-authoring-round1/diagnostics.stderr.txt) is empty. An intervening diagnostic attempt encountered concurrent provider changes before source pinning; its nonzero result is retained in `diagnostic-attempt-2.{stdout,stderr}.txt`. It is not product validation evidence. No full suite, native generation, tokenizer, Docker, network, downloads, production-store reads, H/private/history reads, product edits, index changes, commits, or subagents were performed by this reviewer. The owner's previously reported 131 focused tests were not rerun.

## Findings

### A1 — P1: storage failures discard publication recovery and can repeat successful generation

Locations: `src/feature_rl/requirements/service.py:315` and `:209`; `src/feature_rl/scenarios/service.py:285` and `:227`.

Both services catch every `GenerationProviderError` as a rejected candidate, serialize only its call record/cost into a journal, and continue to the next generation candidate. The provider's actual error can carry `generation_succeeded=true`, `publication_complete=false`, the retained response/usage, and `GenerationPublicationRecovery`. This recovery is dropped at the authoring boundary. The diagnostic supplies precisely that typed failure followed by a valid proposal: the service makes **two provider calls**, records the first already-successful generation as rejected, and returns attempt 2. The journal contains no recovery state. With one candidate, callers receive `AuthoringExhausted` and journal references instead of the pending publication capability.

A second fault injection makes publication of a rejected-candidate journal fail. The service then raises a plain `OSError` without the retained generation, cost, or replay capability. The provider's previously archived objects may still exist, but the authoring operation loses the recoverable result and complete journal handoff needed by M6.

Preserve a typed pending-publication result/error at both boundaries. Complete storage replay and subsequent finalization from the already-produced content without another model call. Do not consume a semantic repair or erase known costs because storage is unavailable. The newer `AuthoringPublicationPending` final-artifact path is a useful boundary, but it does not cover these earlier failures.

Evidence cases: `provider_publication_recovery_dropped`, `rejected_journal_publication`.

### A2 — P1: final contract request and provenance classification are not joined to resolved evidence

Locations: `src/feature_rl/requirements/service.py:280`, `:285`, `:314`; `src/feature_rl/requirements/finalize.py:92`–`:107`.

The real resolver proves the supplied request context matches its stored authoring-request bytes. The service separately accepts `ContractFinalizationInputs.visible_request` and `provenance_label`, and the finalizer copies them into the published contract without comparing them to that resolved request.

Using the actual store-backed resolver, the diagnostic supplies the admitted request “Unknown commands should offer a close command suggestion.” with `reconstructed_specification` provenance. It nevertheless publishes a contract whose visible request is “DIAGNOSTIC UNRELATED REQUEST: delete every CLI command.” and whose top-level classification is `historical_request`. The generated requirement evidence continues to point at the original reconstructed request. This can silently misstate both the solver task and its historical provenance; it does not require forged source bytes or a fake resolver.

Derive or validate these controller-owned values against the exact admitted request/classification. Any supported transformation or reconstructed clarification must remain explicit and attributable rather than permitting an unrelated request or an unsupported provenance upgrade.

Evidence case: `visible_request_and_provenance_not_joined`.

### A3 — P1: scenario sources and supported observations are not joined to the frozen task's capabilities

Locations: `src/feature_rl/scenarios/service.py:244`–`:249`; `src/feature_rl/scenarios/finalize.py:59`–`:65`; `src/feature_rl/scenarios/models.py:27`–`:48`.

The service correctly dereferences the frozen contract and checks its exact canonical context and ID sequence. However, it resolves the remaining evidence independently and never checks that the request/B/discovery belong to that frozen contract. `supported_observables` is also accepted as an arbitrary controller tuple; it is not checked against the contract's resolved discovery.

The diagnostic freezes a contract using one stored request/B/discovery set, then plans against a second real resolver whose request and B references are absent from that contract's provenance. It changes the scenario observation to `object identity` and supplies that label as a controller input. The second discovery still declares only `CLI exit code` and `combined terminal output`. The service accepts and publishes the plan, including the unrelated oracle source and unsupported observation.

Bind planning to the exact contract's admitted request/B/recipe/discovery capabilities. Allow attributable expansions within that task explicitly; do not admit an unrelated source set or new observation capability merely because the caller supplies its label. These are deterministic artifact/capability joins, not a demand for a general natural-language semantic proof.

Evidence case: `scenario_source_and_capability_not_joined`.

### A4 — P2: repair metadata does not require an actual input change

Locations: `src/feature_rl/requirements/service.py:239`–`:268`; `src/feature_rl/scenarios/service.py:158`–`:206`.

Repair validation requires nonempty diagnosis/change strings and a different request/response/prompt identity tuple. It does not compare the substantive request inputs. The diagnostic changes only those three IDs, leaves instruction, system prompt, contexts, seed, and limits identical, adds a claimed `changed_input`, and the service performs the repair. Such an unchanged retry can repeat a diagnosed capacity failure and consume the bounded allocation despite the explicit no-unchanged-retry rule.

Require an attributable actual change to relevant request inputs; compare resumed attempts to their retained request evidence as well as candidates supplied together. A new identity or a prose claim is insufficient evidence of a changed input.

Evidence case: `stage_and_total_repairs`, field `contract_repair_semantic_input_unchanged=true`. Its additional cumulative-count observation is **not** scored as a demand for an M2 global registry: M6's factory wrapper owns aggregate M3/M2/M4 candidate repair accounting. M2 must preserve enough durable attempt state for that wrapper, and the actual reproduction must enforce the prior usage it already knows.

### A5 — P2: the reproduction driver still rejects valid earlier-success states

Locations: `docs/evidence/M2/authoring-production/run.py` at `aa75086`, `scenario():529`–`:534`.

At `d267208`, the scenario command had three independent first-path failures: `native_calls != 1` rejected repaired contracts; JSON-decoded `GroundedSource` objects failed strict Python deserialization of nested visibility enums; and `fixed_cost("scenario_design", ...)` used an unsupported `CostRecord.category`. The owner was notified immediately. `aa75086` fixes the enum transport and category and admits native-call counts 1–3, but also requires `candidate_repairs_used == 3` unconditionally.

The contract writer derives that count as one prior M3 repair plus the number of contract repairs. Therefore a contract accepted initially or after its first repair correctly records 1 or 2, and the new scenario guard still rejects it. Both states reproduce before any tokenizer/provider operation. Derive and check the actual retained totals and remaining budget instead of requiring the one count expected for the latest local attempt.

Evidence cases: `driver_repaired_contract`, `driver_grounded_source_json`, `driver_cost_category` document the original failures; `driver_fixed_source_and_cost` verifies the leaf fixes; `driver_fixed_earlier_success_states` reproduces the remaining guard failure. No synthetic state is a production contract or scenario.

## Boundaries that are implemented

Proposal construction uses M0 field definitions, including metadata and defaults, and finalization constructs the actual M0 artifacts. The model does not author post-call costs or final envelope metadata. Requirement finalization checks source/locator membership, literal quote presence, provenance labels on evidence links, allowed changes, discovered entry-point/observable labels, duplicate requirement IDs, unknown IDs, and unresolved ambiguity dispositions. Real M3 baseline build/installed execution is used for Click discovery; the reviewed M3 execution path itself checks build/source/recipe/policy consistency.

The explicit `scenario_planning` stage accepts one authoring contract without an existing plan and rejects the specified private/reference/plan-role inputs. The scenario service checks the exact stored contract text/locator/context and complete ordered requirement-ID sequence. It derives the mandatory feature-plus-compatibility ID set and rejects unknown IDs, duplicate scenario IDs, missing mandatory ID coverage, and a false same-cases-within-group setting. Final artifact publication retains a replayable `AuthoringPublicationPending` object. These checks do not close A1–A4.

Quote presence and label/ID coverage establish **structural grounding**, not whether an assertion is entailed by evidence or a scenario meaningfully distinguishes the requested behavior. No code review or fixture here qualifies a task. Semantic omission controls, independent alternatives, human review, actual per-seed checker coverage, and downstream task admission remain M4/M5/M6 responsibilities. The review does not invent a public `NoSuchCommand` API or derive behavior from H.

## Actual construction status and next gate

The retained execution and coordinator handoffs establish one real B build/discovery and three actual contract-provider calls. The first two hit the fixed 120-second deadline; the final repair completed but returned malformed model output. The coordinator independently inspected the final authoring response/journal and reported invalid JSON plus duplicate IDs and ungrounded/missing evidence fields. No parser weakening or recovered handwritten contract is warranted. **No grounded frozen contract or scenario exists; no privileged H feasibility review was authorized or performed.** Global usage is the prior M3 repair plus two contract repairs, or 3/4 candidate repairs. Missing successful model construction is an unverified/failed execution gate, not by itself a code defect under the current user scope.

The earlier exposure disclosure remains material: the owner saw SourcePair/changed-file metadata and private H execution metadata/base64 observations in two accidental reads, while reporting no H/archive/diff/history bytes decoded or used. The owner was therefore not fully blinded. This reviewer read neither those private artifacts nor H. Retain the owner's exact disclosure and request/B/public-only model context audit in the final M2 report.

Re-review the owner's fixes for A1–A5 and the final authoring interfaces/report. Then the coordinator can perform the scoped non-GPU integration checkpoint and proceed with downstream code completion while keeping actual task construction and semantic qualification explicitly unverified.

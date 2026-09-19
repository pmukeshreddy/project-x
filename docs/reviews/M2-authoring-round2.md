# M2 authoring re-review — round 2

**Specification compliance: FAIL. Implementation quality: FAIL.** Two P1 findings and one P2 finding remain; no P0 finding. This is one consolidated batch for the existing M2 owner.

Reviewed product `7b8974637e9ffc5bab79f9590b90c7021228d473`, against the previous `c4ed70df6f3218ee2280b346eedb57945d0a44ab`, and final documentation handoff `f0102723cb2d18543dc2c57c01ea116c3d141bbd`. Scope includes the provider recovery additions, requirement/scenario fixes, their tests, reproduction-driver changes, authoring report/interfaces, and verification receipt. The supplied aggregate `review-c4ed70d..78408b6.diff` also contains independently owned M6/root work, which is outside this verdict. The original binding requirements and code-completion ruling remain those listed in [round 1](M2-authoring-round1.md).

## Original findings

| Finding | Re-review status |
|---|---|
| A1 — publication recovery | Partial. Successful-generation provider errors now propagate, `replay_result` returns retained content/usage, and rejection-journal publication has a typed replay path. Failed-generation publication recovery still disappears; the new resumed-result argument also lacks exact archive joins. See B1/B2. |
| A2 — visible request/provenance | Closed for the reported conditions. Finalization now requires one resolved request and compares its text, classification, and provenance-input membership. |
| A3 — scenario source/capability joins | Original foreign-source and caller-invented-observable cases are closed. The new source-set comparison rejects valid public checks; see B3. |
| A4 — unchanged repairs | Closed for identity-only changes, including sequential resumed journals. A semantic request hash excludes only request/response/prompt IDs and adjacent unchanged inputs reject. |
| A5 — earlier-success driver states | Closed for the reported states. The driver accepts retained repair counts 1, 2, or 3, uses strict JSON source transport and a valid cost category, and increments the retained native-call count. |

Aggregate cross-module candidate-budget enforcement remains M6 factory responsibility. This review does not request a parallel M2 registry or global orchestrator.

## Remaining findings

### B1 — P1: resumed results are not bound to the exact archived operation

Locations: `src/feature_rl/requirements/service.py:328`–`:350`; `src/feature_rl/scenarios/service.py:288`–`:309`.

`recovered_result` validation checks success/publication flags plus request and response IDs. Neither service resolves the archived request/schema/context/status or validates retained content, usage, and costs against that archived operation. It then journals the caller's current request and appends the caller-supplied result cost.

The diagnostic uses the production provider controller with the existing inert backend/event-stream runner, injects one archive failure, and calls the actual `GenerationProviderError.replay_result`. All 11 provider archive objects exist in the temporary fixture store. Both services nevertheless accept the recovered result after the instruction changes while IDs remain identical: the published journal's request hash differs from the archived generation request hash. A second invocation changes recovered wall/CPU costs to `0.0`; both final artifacts accept those values while the archived cost remains `1.5` wall seconds and `1.0` CPU seconds. No second runner or model call is needed to create either mismatch.

Resolve and validate the exact archived request, expected output schema and associated context/status/content/usage/cost bindings before using a recovered result. Preserve the operation's original measurements and attribution; ID equality alone is insufficient.

Evidence: `contract_recovered_request_mismatch`, `scenario_recovered_request_mismatch`, and both `*_recovered_cost_mismatch` cases in [diagnostics.json](../evidence/M2/review-authoring-round2/diagnostics.json).

### B2 — P1: failed generation still loses pending provider publication

Locations: `src/feature_rl/requirements/service.py:372`–`:394`; `src/feature_rl/scenarios/service.py:337`–`:359`.

The new propagation branch only covers `GenerationProviderError` when `record.generation_succeeded` is true. A malformed/terminated generation can also have a publication failure and a necessary `GenerationPublicationRecovery`. That error still becomes an ordinary rejected journal and eventually `AuthoringExhausted`, or execution continues to a queued repair.

The diagnostic runs a malformed-response fixture through the actual provider controller and fails response-archive publication. Its original typed error retains ten publication payloads, but only `attempt` and `request` have been published. The authoring service returns `AuthoringExhausted` with no recovery or original error in its cause/context. The unpublished response/event/usage payloads are therefore lost at the service boundary. Known journal cost survives; complete failed-call evidence does not.

Preserve and finish pending publication independently of whether generation succeeded. Only after archive recovery should the failed generation be finalized as a rejected attempt and any permitted changed repair proceed. Apply the same rule if rejection-journal publication also fails.

Evidence: `failed_generation_pending_publication_lost`. This is the remaining part of A1, not an experimental-generation failure.

### B3 — P2: the frozen-source join rejects admitted public checks

Location: `src/feature_rl/scenarios/service.py:172`–`:183`.

The expected source set includes only `authoring-request`, `source-archive`, and `click-runtime-discovery` kinds, while the actual source set includes every supplied context. Therefore any public-check context makes the sets differ, even when that exact public artifact is already in the frozen contract's `public_checks` and provenance inputs and the real store-backed resolver admits it.

The diagnostic constructs that valid, explicitly labeled fixture and confirms the resolver accepts every source. Scenario generation then rejects before invoking its provider with “scenario evidence set differs from the frozen contract inputs.” The stage/routing specification allows attributable public-check evidence; it must not require omitting such evidence to pass the new join. Include the contract's exactly admitted public checks while continuing to reject unrelated sources and private/reference material.

Evidence: `admitted_public_check_rejected`.

## Verification and limits

One narrow diagnostic command ran:

```sh
PYTHONPATH=src .venv/bin/python -B docs/evidence/M2/review-authoring-round2/reproduce.py
```

**Exit 0; 0.301291 seconds; empty stderr.** [reproduce.py](../evidence/M2/review-authoring-round2/reproduce.py) imports the exact reviewed M2 Git bytes from temporary review-owned directories and records product/test hashes. Its production-provider paths use mocked backend verification and inert runner observations; they are controller diagnostics, not native inference or feature evidence. All artifact stores are temporary synthetic stores. No native model, tokenizer, Docker, network, production/private store, H/history, or executable application checks ran. No product/index/commit changes or subagents were used.

The final handoff's 17 source and 30 evidence inventory entries match their current bytes/hashes. The retained focused log says **137 passed in 0.97s**, while the report/receipt say 0.96s; correct that minor reporting difference. The retained full-suite logs report 406 passes plus eight M6 child-import failures without exported `PYTHONPATH`, then 414 passes with it. The final report honestly discloses that both full-suite runs exceeded the focused-only concurrent instruction and lack before/after source-stability evidence. Neither was rerun or treated as a stable cross-module integration gate here.

Describe finalizer coverage as structural requirement-ID coverage; the interface's claim of “meaningful coverage” still needs downstream semantic qualification. Literal quote presence, recognized observation labels, and ID joins do not prove evidence entailment or a behaviorally discriminating checker.

The actual Click construction remains three exhausted model calls: two fixed-bound timeouts and one strict duplicate-`allowed_changes` rejection at line 142, column 5. No valid frozen contract/scenario exists, and no H feasibility review is authorized. The declared owner exposure history remains disclosed in the final report. These execution facts are not scored as missing code or grounds for a handwritten replacement. Fix B1–B3 through the same owner, then re-review the changed scope before the M2 integration gate.

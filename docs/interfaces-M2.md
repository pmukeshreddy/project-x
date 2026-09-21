# M2 Codex/Astra authoring interfaces

The sole authoring provider is `CodexGenerationProvider`. It invokes `gpt-6-astra`
through the installed Codex CLI, using the existing ChatGPT authentication.
There is no API-key provider, local inference backend, automatic model substitution,
or fallback authoring implementation. A live Astra authoring run has not been
performed for this refactor.

## Configuration and call

Install a Codex CLI supporting `exec --ignore-user-config --output-schema --json`
(the interface was checked against 0.155.1). `codex login status` must report
ChatGPT authentication; otherwise sign in with `codex login`. The provider never
reads/copies tokens or launches an interactive login. An unavailable CLI, login,
model, or invalid/missing output fails explicitly.

```python
from feature_rl.generation import CodexConfig, CodexGenerationProvider

provider = CodexGenerationProvider(
    config=CodexConfig(model="gpt-6-astra", reasoning_effort="high"),
    archive=store.put_bytes,
)
# Run only when authoring is intended:
# result = provider.generate(validated_request, RequirementContractProposal)
```

`CodexConfig` has `executable` (default `codex`), optional `codex_home`, fixed
`model="gpt-6-astra"`, and `reasoning_effort` (`low`, `medium`, `high`, `xhigh`, `max`).
`AuthoringSettings.codex` and `FeatureWorkflowSettings.codex` use this same model.
Existing `CODEX_HOME` and the usual Codex authentication store/keychain are reused.
API-key and alternate endpoint environment variables are not passed to the process.
User/project configuration, plugins, hooks, skills, shell, apps, browsers, and
other agent tools are disabled for authoring. Each call uses a fresh ephemeral
session in an empty temporary directory with a read-only sandbox and no approvals.
The source repository enters through the existing resolved evidence contexts.

The [Codex non-interactive interface](https://learn.chatgpt.com/docs/non-interactive-mode)
supplies JSONL events and schema-constrained output;
[Codex authentication](https://learn.chatgpt.com/docs/auth) supplies the existing login.

## Validation

The output envelope contains exactly `response_id`, ordered `source_ids`,
`requirement_ids`, and `content`. The provider rejects duplicate JSON keys,
nonfinite values, changed identities, missing/reordered sources, unknown or
duplicate requirement IDs, unknown fields, and invalid types. JSON Schema
validation checks the original raw JSON; the original strict Pydantic proposal
model then runs all existing domain validators. No artifact validator is removed
or relaxed. Codex's transport schema requires all fields explicitly and expresses
Pydantic's disjoint tagged unions as `anyOf`; local validation still uses the
original schema, including `oneOf`.

A successful result requires one completed Codex turn, one final artifact message,
valid reported usage, a matching final-response file, and successful local process
completion/cleanup. Tool execution, malformed events, failed/incomplete turns,
missing output and limit violations reject the result. Finalizers retain the
existing grounding, permission, requirement-coverage, and cross-artifact checks.
The controller still builds and validates the runtime definition and TaskBundle
through the existing architecture; Astra never invents runtime execution evidence.

## Stage and context boundary

Every `AuthoringContext` carries a fixed `context_id`, explicit role, immutable source reference, locator, text, and provenance label. Request, response, and prompt IDs use the M2-local ASCII identifier grammar with a 128-character maximum; call-record request and response identities use the same bound. The public provider boundary revalidates an already typed `GenerationRequest`, so unchecked `model_copy` or `model_construct` updates cannot bypass the limit. Request, response, prompt, context, and allowed-requirement IDs must be fixed before inference; placeholders and duplicate IDs reject.

| Stage | Admitted explicit context | Forbidden context |
|---|---|---|
| `discovery` / `initial_authoring` | request, B/baseline, and public-check data with public or authoring visibility | H, diffs, reference trees, `SourcePair`, private/evaluation data, frozen private scenarios, and ambient stores |
| `scenario_planning` | exact frozen authoring `RequirementContract` plus its admitted request, B/baseline, and discovery evidence | H, diffs, reference trees, `SourcePair`, private/evaluation data, caller-invented observations, and alternate evidence sets |
| `control_authoring` | exactly one frozen authoring `RequirementContract`, one or more B excerpts, optional admitted request/public checks, multiple explicit private/evaluation reference excerpts, and at most one private/evaluation `ScenarioPlan` | reference data labeled as baseline, `SourcePair`, solver-safe/alternative context, checker/oracle/control outputs, and authoring traces |
| `alternative_authoring` | exactly one visible frozen authoring `RequirementContract`, one or more B excerpts, and admitted request/public-check or explicitly public solver-safe context | reference/H data, private scenarios, `SourcePair`, checker/oracle/control outputs, private context, and authoring traces |
| `checker_generation` | required frozen `RequirementContract` and `ScenarioPlan`, plus explicitly supplied attributable request/B/public-check context | reference implementation/H data, non-allowlisted roles or kinds, and private B/public-check data |

The initial contract draft therefore occurs without H or privileged checker inspection. After that draft is frozen, a controller may call the later checker stage with the frozen contract and private/evaluation scenario plan as explicit data. This later privilege does not broaden initial-authoring inputs and does not give the model access to the underlying store.

`control_authoring` and `alternative_authoring` are transport boundaries for M4; M2 does not construct their requests, proposals, or final artifacts. Both require one authoring-visible JSON `RequirementContract` and at least one public/authoring byte context with kind `source-archive` or `runtime-discovery`. Control authoring may also receive `authoring-request` and `public-check` byte contexts, multiple `reference` contexts whose refs are private/evaluation byte `source-archive` objects, and at most one private/evaluation JSON `ScenarioPlan`. M4 must bind all B excerpts to the exact admitted B archive and all reference excerpts to the exact admitted H archive; M2 never accepts `SourcePair` as model context. Alternative authoring allows `authoring-request`, `public-check`, and public byte `solver-safe-context` objects, and rejects every `reference` or `scenario` role. M4 must render only visible contract fields and verify exact contract/B/public-source joins. The explicit `reference` role is rejected in every other generation stage.

Provider records for both new stages use private visibility and the existing `authoring` cost category. Attempt registration, immutable archive metadata, resource enforcement, and publication recovery are unchanged; adding the enum values does not authorize or perform a generation call.

## Resources and accounting

`GenerationLimits` bounds local Codex wall time (up to 3600 seconds), CPU time,
stdin/schema size, combined stdout/stderr, final response size, and sampled process memory.
No process-wide file-size cap is applied to Codex authentication/state databases.
The existing process-group cleanup and Linux/macOS monitor remain in force.
These are local CLI limits, not measurements or hard limits on remote Astra compute.

`input_tokens` and `output_tokens` are acceptance/accounting ceilings checked
against Codex's reported counts after completion. Codex exec does not expose hard
per-call token caps; an overrun is retained as cost and rejected. Batch accounting
reserves declared ceilings and charges larger measured usage. Finite USD caps
remain unsupported because subscription currency usage is not reported.

`GenerationUsage` contains only actual Codex `input_tokens`, `cached_input_tokens`,
and `output_tokens`. There are no fabricated token IDs, logprobs, local model
revisions, GPU allocations, or sampling seeds. Scenario/episode seeds are unchanged.
Wall/CPU values describe the Codex process. Remote compute, human time and USD
remain unknown, never zero by assumption.

## Requirement authoring

`RuntimeDiscoveryService.discover(prepared)` probes the configured repository profile through M3's installed-wheel path. Its author-visible `RuntimeDiscoveryObservation` binds the exact baseline, environment recipe, and private build/execution receipt hashes. `BaselineRetriever` reads only declared inert archive paths and inclusive line ranges; safe directory metadata is ignored while links and other non-regular members reject. `AuthoringEvidenceResolver` reconstructs every supplied request, baseline span, public check, and discovery context from the controller store before inference and rejects altered text, locators, roles, or references.

`RequirementContractProposal` contains the semantic fields of the requirement
contract. `ContractAuthoringService.generate(...)` accepts one to three
`GenerationRequest` values, grounds evidence and validates discovered capabilities,
then publishes the immutable contract. Each validation failure may be followed by
another bounded attempt with fresh request IDs. No diagnosis, changed-input proof,
authorization, predecessor link or semantic history is required.

Each call retains its exact request hash and provider outcome for resource accounting
and storage recovery. `AuthoringExhausted` reports rejected attempt records.
Publication recovery reuses the already-produced bytes; it does not generate a new
answer. Provider archives remain bounded and checked against their request, schema,
usage and cost. Resource reservations and the three-attempt limit still apply.

## Controller scenario slots

The controller builds one scenario slot per frozen requirement. Astra supplies the
behavioral cases within those slots; see [verifier generation](interfaces-M4.md).
There is no separate scenario-generation call or finalizer.

## Immutable archives and recovery

The attempt receipt binds request/schema hashes, request/response/prompt IDs,
Astra model, reasoning effort and `codex-exec-v1`. Complete calls archive request,
response, retrieval, schema, options, provenance (including CLI version), usage,
cost, raw Codex events, and status. Initial contract records are authoring-visible;
later-stage records are private. Oversized input and failed attempt registration
retain bounded attempt/cost/status records without archiving oversized context.

`GenerationProviderError` retains the exact failure, call record and measured
cost. Publication failures retain `GenerationPublicationRecovery`; replay writes
only the retained bytes and never invokes Codex again. Recovery revalidates the
Codex events, original output schema and artifact lineage. Authentication failures
stop the workflow without consuming further attempts. Validation failures permit
at most three attempts per role using Astra.
Old local-worker archives/configuration are not a compatible authoring path;
historical evidence under `docs/evidence` is retained solely as historical evidence.

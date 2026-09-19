# M2 local generation-provider interfaces

This interface covers the bounded local generation provider and grounded requirement/scenario authoring. Import the public boundaries from `feature_rl.generation`, `feature_rl.requirements`, and `feature_rl.scenarios`; M0 retains ownership of shared artifact schemas and storage. Checker implementation and reward remain later modules.

## Provider construction and call

```python
from feature_rl.generation import BackendConfig, LocalGenerationProvider

provider = LocalGenerationProvider(
    backend=BackendConfig(
        python_executable=python_path,
        model_directory=model_directory,
        model_manifest=model_acquisition_manifest,
        dependency_manifest=wheel_closure_manifest,
    ),
    archive=controller_store.put_bytes,  # write-only capability
)
result = provider.generate(request, OutputSchema)
```

`BackendConfig.verify()` accepts exactly `mlx-community/Qwen3-4B-Instruct-2507-4bit` at revision `50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b`, the reviewed 11-file positive allowlist, the pinned model and tokenizer hashes, and the reviewed 34-wheel dependency closure. It rejects extra, missing, changed, symlinked, remote-code, or architecture-mismatched model files. The worker interpreter must be the provider interpreter whose installed closure was checked. The supported platform is CPython 3.13 on Darwin arm64 with macOS 26 or newer. There is no backend or model fallback.

The provider receives only a write-only archive callback. It cannot retrieve artifacts and does not pass an artifact store, repository, task conversation, or controller state to the worker. Source existence, locator/text equality, provenance labels, frozen status, and contract/scenario joins remain caller-side authoring/controller gates. Each call stages the fixed worker in a new temporary directory with task-specific Hugging Face, Xet, Transformers, and temporary caches plus a fresh prompt cache. It preserves an existing `HOME` value and does not set or repurpose `CODEX_HOME`. The worker gets one canonical JSON request over bounded stdin, the explicitly named model directory, and an offline/local-files-only environment. It runs with Python isolated mode, loads no remote code, exposes no tools, and emits JSONL protocol events over stdout.

`generate(request: GenerationRequest, output_schema: type[StrictModel]) -> GenerationResult` validates one exact JSON envelope:

```json
{
  "response_id": "preassigned-response-id",
  "source_ids": ["every-context-id-in-input-order"],
  "requirement_ids": ["only-predeclared-ids"],
  "content": {}
}
```

Protocol v3 defines an exact strict schema for every identity, input, memory-control, model-loaded, token, completion, and input-rejection event. Every event repeats the protocol/request/response/prompt identity. The identity event binds the model revision, config/tokenizer/weight hashes, manifest hashes, exact dependency versions, seed, prompt hash, staged-worker hash, offline flags, and remote-code setting. Boolean and integer Literal fields have pre-conversion validators, so equality-compatible JSON numbers such as `1`, `0`, or `3.0` cannot become `true`, `false`, or protocol version `3`. Extra fields, missing fields, wrong exact types, contradictory identities, positive/nonfinite selected-model logprobs, duplicate JSON keys, tool events, event-order drift, inconsistent usage, nonzero exits, cleanup failure, and resource termination all raise `GenerationProviderError`.

Envelope content is re-serialized and validated through Pydantic's strict JSON transport. Schemas whose Literals are only strings, string-valued enums or null use the original caller validator unchanged. Their callbacks, defaults, post-init hooks, JSON validation mode and serialization therefore retain ordinary Pydantic semantics. All actual M0 Literal fields use this path; `RequirementContract` and `ScenarioPlan` fixtures preserve their string-enum, tuple and UTC transport. Literal values outside JSON-native strings, nulls, Booleans, integers and floats, and enums whose values are not strings, are unsupported and refuse before execution rather than inheriting Pydantic's equality coercion.

Numeric or Boolean Literal schemas use a separate exact guard because Pydantic can otherwise equate JSON numbers and Booleans during union selection. This guard is limited to callback-free models composed from strict primitives and enums, scalar/model unions, nullable fields, lists, tuples, definitions/references, typed objects, and dictionaries with string keys, including patterned keys. It replaces model construction with inert validation models, wraps every Literal with an exact raw-JSON-type check, restores only model values and returns the single selected result. There is no second branch-selection pass, so raw integer `1` in `Literal[True] | int`, raw float `3.0` in `Literal[3] | float`, model alternatives and nested containers retain their exact selected Python and JSON types.

The numeric/Boolean guard refuses callbacks and chains, defaults and model lifecycle hooks, custom initialization or serialization, dataclass/call and unknown core forms, set/frozenset values, dictionaries whose keys are not strings, and models configured with `extra='allow'`. The set restriction prevents restored value-equal models from collapsing only after cardinality validation; the extra-field restriction prevents restoration from losing separately stored accepted fields. Models using ordinary `extra='forbid'` or `extra='ignore'` remain supported. Refused forms raise an attributable `GenerationSchemaUnsupportedError` before backend verification or execution, and publication failure remains replayable without execution. A future caller needing a refused guarded form must use a supported proposal schema or extend and review this boundary before the call.

`GenerationResult` contains the caller-schema-validated `content`, exact `GenerationUsage`, partial `CostRecord`, and `GenerationCallRecord`. Usage includes the exact fully templated input token IDs, every emitted token ID, the selected token's pre-sampler model log-softmax score, and `sampling_policy="greedy_argmax"`. Pinned `mlx_lm.generate_step` applies argmax to those model scores; the scores are not log probabilities under the deterministic behavior distribution. `behavior_logprobs` is therefore explicitly null. A downstream training record must not substitute these model scores for behavior logprobs or invent behavior probabilities by retokenizing. A call at the emitted-token limit is marked truncated and rejected even when its bytes parse as valid JSON. These records make an authored sample attributable; they do not by themselves establish training suitability or a learning result.

## Stage and context boundary

Every `AuthoringContext` carries a fixed `context_id`, explicit role, immutable source reference, locator, text, and provenance label. Request, response, and prompt IDs use the M2-local ASCII identifier grammar with a 128-character maximum; the same bound applies to event, attempt, and call-record identities. The public provider boundary revalidates an already typed `GenerationRequest`, so unchecked `model_copy` or `model_construct` updates cannot bypass the limit. Request, response, prompt, context, and allowed-requirement IDs must be fixed before inference; placeholders and duplicate IDs reject.

| Stage | Admitted explicit context | Forbidden context |
|---|---|---|
| `discovery` / `initial_authoring` | request, B/baseline, and public-check data with public or authoring visibility | H, diffs, reference trees, `SourcePair`, private/evaluation data, frozen private scenarios, and ambient stores |
| `scenario_planning` | exact frozen authoring `RequirementContract` plus its admitted request, B/baseline, and discovery evidence | H, diffs, reference trees, `SourcePair`, private/evaluation data, caller-invented observations, and alternate evidence sets |
| `checker_generation` | required frozen `RequirementContract` and `ScenarioPlan`, plus explicitly supplied attributable request/B/public-check context | reference implementation/H data, non-allowlisted roles or kinds, and private B/public-check data |

The initial contract draft therefore occurs without H or privileged checker inspection. After that draft is frozen, a controller may call the later checker stage with the frozen contract and private/evaluation scenario plan as explicit data. This later privilege does not broaden initial-authoring inputs and does not give the model access to the underlying store.

## Resource and token contract

`GenerationLimits` declares positive wall, CPU, stdin, retained-output/file, input-token, emitted-output-token, MLX memory, and external physical-footprint limits. The fully templated chat input is tokenized before model loading and rejects above its declared input limit. `mlx_lm.generate_step(max_tokens=...)` bounds emitted tokens; the controller independently verifies the event count and refuses truncation.

The `tiny_smoke_2048x128` measurement profile is qualified only through 2,048 actual input tokens and 128 emitted output tokens. `larger_unqualified` permits a caller to declare a larger positive pair within the pinned model's 262,144-token architectural context capacity. That architectural capacity is not a measured host-usable envelope. Before a first construction batch uses larger values, the controller must record a separate bounded measurement under the same watchdog, CPU, wall, and output policy. This slice made three measured `initial_authoring` calls with 9,216-token input and 4,096-token output caps: two reached the 120-second deadline, and one completed generation but failed strict duplicate-key validation. None produced an accepted artifact.

| Resource | Boundary and evidence meaning |
|---|---|
| wall time | external monotonic deadline, at most 120 seconds per current call |
| CPU time | kernel `RLIMIT_CPU`, declared positive integer seconds, at most 120 for the current interface |
| file output | kernel `RLIMIT_FSIZE`; retained stdout+stderr is also monitored against the combined byte cap |
| stdin | controller registers the attempt, then rejects an oversized request/schema/templated prompt before backend verification or spawn; worker has a 1 MiB absolute defense |
| emitted tokens | native generation-loop maximum plus controller event-count check; truncation rejects |
| MLX allocation | 3.5 GiB MLX memory guideline and wired limit with the MLX cache limit set to zero |
| process physical footprint | external `proc_pid_rusage` sampling every 20 ms; process group killed above 4 GiB; any missing/exceptional observation fails the call immediately |
| declared memory ceiling | 5 GiB, leaving a 1 GiB guard band above the sampled kill threshold |
| process cleanup | from successful spawn onward, a `finally` path kills/reaps the fresh process group after normal exit, boundary failure, monitor exception, or interruption and checks absence |

## Requirement authoring

`ClickDiscoveryService.discover(prepared)` runs the fixed public Click probe through M3's installed-wheel path. Its author-visible `ClickDiscoveryObservation` binds the exact baseline, environment recipe, and private build/execution receipt hashes. `BaselineRetriever` reads only declared inert archive paths and inclusive line ranges; safe directory metadata is ignored while links and other non-regular members reject. `AuthoringEvidenceResolver` reconstructs every supplied request, baseline span, public check, and discovery context from the controller store before inference and rejects altered text, locators, roles, or references.

`RequirementContractProposal` is derived from the actual M0 `RequirementContract` field metadata for the semantic fields. `build_contract_request(...)` accepts a bounded requirement-ID namespace without requiring the model to use every ID. `ContractAuthoringService.generate(...)` resolves the evidence, checks the request contexts, makes at most one initial attempt plus two diagnosed repairs, grounds every quote and locator, validates entry points and observations against discovery, binds the final visible request and provenance label to the resolved request, constructs the real M0 artifact, and stores it immutably.

Every attempt creates a journal with an exact request hash and a semantic request hash that excludes only request/response/prompt identities. A repair must change that semantic hash. `AuthoringExhausted` returns all verified rejected-journal refs. `AuthoringJournalPublicationPending.replay(store)` publishes a failed rejection journal before any later generation. Any provider error with pending `GenerationPublicationRecovery` propagates before journaling or another generation. `GenerationProviderError.replay_result(archive)` completes publication for a successful generation; `replay_error(archive)` completes publication for a failed generation while preserving its original response, usage observation, cost, status, and error attribution. The authoring services accept `recovered_result=` or `recovered_error=` only after bounded resolution of the complete archive set and exact comparison of the request, schema, contexts, attempt/status, content where present, usage, response where present, and cost. A fully archived failed result can then be journaled, and a journal write failure remains replayable, without another model call. `AuthoringPublicationPending.replay(store)` handles a later accepted-journal or final-artifact publication fault.

## Scenario planning

`build_scenario_request(...)` creates the explicit `scenario_planning` stage from one exact authoring `RequirementContract`, the admitted request/B/discovery/public-check contexts, and the contract's actual requirement IDs. `ScenarioAuthoringService.generate(...)` dereferences the contract and requires its canonical context text, locator, ref, ordered ID sequence, exact admitted request/B/discovery/public-check reference set, request text/provenance, and requirement observation set. Public checks must be the exact refs frozen in `contract.public_checks`; unrelated public refs still reject. It applies the same three-attempt journaling, semantic-change, provider-recovery, and storage-replay rules as contract authoring.

`ScenarioFinalizer` derives the mandatory set from every mandatory feature requirement plus every compatibility obligation, rejects unknown IDs or unsupported observations, grounds each oracle in the admitted evidence, requires structural requirement-ID coverage of the mandatory set, preserves `same_cases_within_group=true`, constructs the real M0 `ScenarioPlan`, and stores it with an exact contract join. Structural ID presence is not proof of semantic entailment or a behaviorally discriminating checker. The retained reproduction/controller default uses M4's implemented `SeedPolicy.algorithm="m4-sha256-v1"`; an explicitly supplied unsupported algorithm remains a downstream compatibility failure.

The production Click run retained one successful M3 discovery and three provider calls. Two calls reached the fixed 120-second deadline. The final concise four-ID call completed in 84.458 seconds but returned a duplicate-key response that strict JSON rejected. No contract or scenario was frozen; this is an explicit construction failure rather than an API fallback or handwritten artifact.

The physical-footprint boundary is sampled and can overshoot between polls. It is not a zero-transient allocator reservation. The MLX limit is a library guideline. Receipts report the maximum sampled physical footprint separately from the kernel-reported lifetime maximum. Process count is observed through the single fixed worker design and group cleanup; this interface does not claim an OS-enforced one-process quota.

## Immutable call archive

Before backend verification or subprocess execution, the provider creates UTC `GenerationAttemptMetadata` and publishes `generation-attempt`. It binds a random attempt ID, producer, protocol, request/prompt/response IDs, request and output-schema hashes, all generation source-file hashes, and configured model/config/manifest/dependency identities. If this registration fails, no worker runs.

If a validated request, output schema, or fully templated prompt exceeds the declared stdin byte cap, the provider does not archive that oversized value. It publishes bounded `generation-preflight`, `generation-cost`, and `generation-status` objects alongside the attempt. The preflight object contains the bounded IDs, request/schema hashes, declared cap, observed component sizes, violated components, failure cause, and `execution_started=false`. The public exception remains `GenerationProviderError`; its record uses `GenerationInputLimitError` as the cause. A publication failure retains the same bounded payloads for replay without backend verification or execution. Requests with longer request/response/prompt identities are invalid at the M2 request boundary and cannot enter this archival path.

After execution, every successful or failed call publishes separate `generation-request`, `generation-response`, `generation-retrieval`, `generation-schema`, `generation-options`, `generation-provenance`, `generation-usage`, `generation-cost`, `generation-events`, and `generation-status` objects. The response receipt records exit status, termination, wall/CPU measurements, watchdog failures, sample count, maximum sampled and lifetime memory observations, any breach sample, stderr/stdout, and cleanup result. The raw JSONL event stream is retained byte-for-byte. The usage object always has `accepted_response_usage`; it is true only after both event and envelope/content validation. Every failure retains independently validated input/token observations and known cost counts while setting acceptance false.

An archive callback can itself be unavailable. `GenerationProviderError` then contains a partial `GenerationCallRecord`, known `cost`, bounded `response`, usage observations, and `GenerationPublicationRecovery`. `error.replay_publication(write_only_callback)` publishes only missing retained payloads and status without rerunning inference. A repeated storage failure returns another typed error with updated partial references and recovery state. The provider cannot promise an artifact when the store cannot write; it exposes that condition as `ArchivePublicationError` rather than losing measurements or silently generating again.

Initial-authoring records use authoring visibility; later checker-generation records use private visibility. The cost record is deliberately partial: wall time, CPU time, and accepted token counts are measured when present; GPU time, energy, human time, and currency cost remain null/unknown. Local execution does not imply zero compute cost merely because no remote API was used.

The production evidence commands and complete receipts are described in `docs/evidence/M2/production-provider-smoke.md`.

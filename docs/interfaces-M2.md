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
        model_id=model_id,
        revision=model_commit_hash,
        model_manifest_sha256=model_manifest_sha256,
        dependency_manifest_sha256=dependency_manifest_sha256,
        device="cpu",  # or an explicit visible device such as "cuda:0"
        dtype="float32",  # float16 / bfloat16 are also explicit choices
    ),
    archive=controller_store.put_bytes,  # write-only capability
)
result = provider.generate(request, OutputSchema)
```

`BackendConfig.verify()` checks the configured model ID, immutable commit revision,
SHA-256-pinned model/dependency manifests, exact model bytes, and installed package
versions. Both the controller and fresh worker repeat these checks. The worker
interpreter must be the provider interpreter whose closure was checked. The
supported controller platforms are CPython 3.11+ on Linux and macOS. Authoring
uses local Transformers/PyTorch with an explicit CPU or CUDA device and dtype;
there is no device, model, or API fallback. No MLX runtime is imported.

Supply a materialized, non-symlink model directory containing a standard,
unquantized Transformers causal LM: `config.json`, `tokenizer.json`,
`tokenizer_config.json`, a chat template, and either `model.safetensors` or
safetensors shards with `model.safetensors.index.json`. Custom model code,
quantization backends, pickle checkpoints, extra files and missing shards reject.
The model must expose a KV cache, an EOS token, and `max_position_embeddings`.

The existing model-manifest format is retained: `model_id`, `revision`,
`runtime_positive_allowlist` (all filenames), `actual_total_bytes`, and `files`
records containing `path`, `bytes`, and `sha256`. Sharded weight identity hashes
the sorted filename-to-SHA-256 mapping. The dependency manifest has `wheels`
records with `name`, exact `version`, `filename`, and `sha256`. It must include
`torch`, `transformers`, `tokenizers`, `safetensors`, `packaging`, and their active
installed dependency closure. Manifests and model files must be regular files;
all model files are flat within the configured directory.

Install the explicitly chosen wheel closure with hash checking before authoring.
The provider validates installed versions; wheel hashes are installation inputs,
not an attestation of every installed package byte. Torch wheel selection remains
specific to the chosen host. The controller does not install packages, download
models, change GPU drivers, or provision a server. The MLX lockfile and request
compatibility fields have been removed.

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

Protocol v4 defines an exact strict schema for every identity, input, memory-control, model-loaded, token, completion, and input-rejection event. Every event repeats the protocol/request/response/prompt identity. The identity event binds the model revision, config/tokenizer/weight hashes, manifest hashes, exact dependency versions, seed, prompt hash, staged-worker hash, offline flags, device, dtype, and remote-code setting. Boolean and integer Literal fields have pre-conversion validators, so equality-compatible JSON numbers such as `1`, `0`, or `4.0` cannot become `true`, `false`, or protocol version `4`. Extra fields, missing fields, wrong exact types, contradictory identities, positive/nonfinite selected-model logprobs, duplicate JSON keys, tool events, event-order drift, inconsistent usage, nonzero exits, cleanup failure, and resource termination all raise `GenerationProviderError`.

Envelope content is re-serialized and validated through Pydantic's strict JSON transport. Schemas whose Literals are only strings, string-valued enums or null use the original caller validator unchanged. Their callbacks, defaults, post-init hooks, JSON validation mode and serialization therefore retain ordinary Pydantic semantics. All actual M0 Literal fields use this path; `RequirementContract` and `ScenarioPlan` fixtures preserve their string-enum, tuple and UTC transport. Literal values outside JSON-native strings, nulls, Booleans, integers and floats, and enums whose values are not strings, are unsupported and refuse before execution rather than inheriting Pydantic's equality coercion.

Numeric or Boolean Literal schemas use a separate exact guard because Pydantic can otherwise equate JSON numbers and Booleans during union selection. This guard is limited to callback-free models composed from strict primitives and enums, scalar/model unions, nullable fields, lists, tuples, definitions/references, typed objects, and dictionaries with string keys, including patterned keys. It replaces model construction with inert validation models, wraps every Literal with an exact raw-JSON-type check, restores only model values and returns the single selected result. There is no second branch-selection pass, so raw integer `1` in `Literal[True] | int`, raw float `4.0` in `Literal[3] | float`, model alternatives and nested containers retain their exact selected Python and JSON types.

The numeric/Boolean guard refuses callbacks and chains, defaults and model lifecycle hooks, custom initialization or serialization, dataclass/call and unknown core forms, set/frozenset values, dictionaries whose keys are not strings, and models configured with `extra='allow'`. The set restriction prevents restored value-equal models from collapsing only after cardinality validation; the extra-field restriction prevents restoration from losing separately stored accepted fields. Models using ordinary `extra='forbid'` or `extra='ignore'` remain supported. Refused forms raise an attributable `GenerationSchemaUnsupportedError` before backend verification or execution, and publication failure remains replayable without execution. A future caller needing a refused guarded form must use a supported proposal schema or extend and review this boundary before the call.

`GenerationResult` contains the caller-schema-validated `content`, exact `GenerationUsage`, partial `CostRecord`, and `GenerationCallRecord`. Usage includes the exact fully templated input token IDs, every emitted token ID, the selected token's pre-sampler model log-softmax score, and `sampling_policy="greedy_argmax"`. The Torch worker applies argmax to those model scores; the scores are not log probabilities under the deterministic behavior distribution. `behavior_logprobs` is therefore explicitly null. A downstream training record must not substitute these model scores for behavior logprobs or invent behavior probabilities by retokenizing. A call at the emitted-token limit is marked truncated and rejected even when its bytes parse as valid JSON. These records make an authored sample attributable; they do not by themselves establish training suitability or a learning result.

## Stage and context boundary

Every `AuthoringContext` carries a fixed `context_id`, explicit role, immutable source reference, locator, text, and provenance label. Request, response, and prompt IDs use the M2-local ASCII identifier grammar with a 128-character maximum; the same bound applies to event, attempt, and call-record identities. The public provider boundary revalidates an already typed `GenerationRequest`, so unchecked `model_copy` or `model_construct` updates cannot bypass the limit. Request, response, prompt, context, and allowed-requirement IDs must be fixed before inference; placeholders and duplicate IDs reject.

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

## Resource and token contract

`GenerationLimits` declares positive wall, CPU, stdin, output/file, token, and
process-memory limits. The worker tokenizes the full chat input before loading
weights and checks actual input plus reserved output against the model's context.
Greedy decoding emits each selected token and its actual model log probability;
the controller checks token counts and rejects truncation.

Limits describe the selected model and host directly; historical MLX measurement
labels and allocator fields are rejected. No Transformers authoring run is claimed
by this implementation change.

| Resource | Boundary and evidence meaning |
|---|---|
| wall / CPU | external deadline and kernel `RLIMIT_CPU`, each at most 120 seconds per call |
| file output | kernel `RLIMIT_FSIZE` and combined stdout/stderr byte cap |
| stdin | oversized request/schema/prompt rejected before backend verification or spawn; worker absolute cap 1 MiB |
| emitted tokens | bounded greedy loop and independent event-count check; truncation rejects |
| process memory | configured `physical_footprint_kill_bytes`: Linux RSS via `/proc`, macOS physical footprint via `proc_pid_rusage`; sampled at `physical_footprint_poll_seconds` |
| CUDA memory | CUDA calls require `cuda_memory_bytes`, enforced by Torch's per-process allocator fraction; CPU calls require null |
| declared memory ceiling | must exceed the sum of the process threshold and CUDA allocator limit, leaving a guard band |
| cleanup | finally-path kill/reap and process-group absence check, including failures and interruption |

Linux receipts record sampled RSS and leave macOS footprint fields null. CPU
calls leave CUDA allocator measurements null. CUDA receipts record active, peak,
and reserved allocator bytes separately. Sampled process limits can overshoot
between polls; the CUDA allocator limit does not include driver/context overhead.
These are bounded execution controls, not a host-wide memory reservation. `CUDA_VISIBLE_DEVICES` is preserved, and a missing
configured CUDA device fails rather than selecting another device.

## Requirement authoring

`RuntimeDiscoveryService.discover(prepared)` probes the configured repository profile through M3's installed-wheel path. Its author-visible `RuntimeDiscoveryObservation` binds the exact baseline, environment recipe, and private build/execution receipt hashes. `BaselineRetriever` reads only declared inert archive paths and inclusive line ranges; safe directory metadata is ignored while links and other non-regular members reject. `AuthoringEvidenceResolver` reconstructs every supplied request, baseline span, public check, and discovery context from the controller store before inference and rejects altered text, locators, roles, or references.

`RequirementContractProposal` is derived from the actual M0 `RequirementContract` field metadata for the semantic fields. `build_contract_request(...)` accepts a bounded requirement-ID namespace without requiring the model to use every ID. `ContractAuthoringService.generate(...)` resolves the evidence, checks the request contexts, makes at most one initial attempt plus two diagnosed repairs, grounds every quote and locator, validates entry points and observations against discovery, binds the final visible request and provenance label to the resolved request, constructs the real M0 artifact, and stores it immutably.

Every attempt creates a journal with an exact request hash and a semantic request hash that excludes only request/response/prompt identities. A repair must change that semantic hash. `AuthoringExhausted` returns all verified rejected-journal refs. `AuthoringJournalPublicationPending.replay(store)` publishes a failed rejection journal before any later generation. Any provider error with pending `GenerationPublicationRecovery` propagates before journaling or another generation. `GenerationProviderError.replay_result(archive)` completes publication for a successful generation; `replay_error(archive)` completes publication for a failed generation while preserving its original response, usage observation, cost, status, and error attribution. The authoring services accept `recovered_result=` or `recovered_error=` only after bounded resolution of an authentic complete provider archive variant: the full post-execution set, registration `{attempt,status}`, or preflight `{attempt,preflight,cost,status}`. Full outcomes compare the exact request, schema, contexts, attempt/status, content where present, usage, response where present, and cost. Pre-execution outcomes compare request/schema hashes, identities, disposition, status, observed sizes, and every available cost without fabricating absent execution records. Artifact reads use per-kind caps derived from the request's existing stdin/output limits; response receipts include base64 expansion and fixed metadata, and the outer CAS cap includes its second base64 envelope. These receipt caps do not change native token, memory, wall, CPU, or raw-output limits. A fully archived failed result can then be journaled, and a journal write failure remains replayable, without another model call. `AuthoringPublicationPending.replay(store)` handles a later accepted-journal or final-artifact publication fault.

## Scenario planning

`build_scenario_request(...)` creates the explicit `scenario_planning` stage from one exact authoring `RequirementContract`, the admitted request/B/discovery/public-check contexts, and the contract's actual requirement IDs. `ScenarioAuthoringService.generate(...)` dereferences the contract and requires its canonical context text, locator, ref, ordered ID sequence, exact admitted request/B/discovery/public-check reference set, request text/provenance, and requirement observation set. Public checks must be the exact refs frozen in `contract.public_checks`; unrelated public refs still reject. It applies the same three-attempt journaling, semantic-change, provider-recovery, and storage-replay rules as contract authoring.

`ScenarioFinalizer` derives the mandatory set from every mandatory feature requirement plus every compatibility obligation, rejects unknown IDs or unsupported observations, grounds each oracle in the admitted evidence, requires structural requirement-ID coverage of the mandatory set, preserves `same_cases_within_group=true`, constructs the real M0 `ScenarioPlan`, and stores it with an exact contract join. Structural ID presence is not proof of semantic entailment or a behaviorally discriminating checker. The retained reproduction/controller default uses M4's implemented `SeedPolicy.algorithm="m4-sha256-v1"`; an explicitly supplied unsupported algorithm remains a downstream compatibility failure.

The historical MLX Click run retained one successful M3 discovery and three provider calls. Two calls reached the fixed 120-second deadline. The final concise four-ID call completed in 84.458 seconds but returned a duplicate-key response that strict JSON rejected. No contract or scenario was frozen; this is an explicit construction failure rather than an API fallback or handwritten artifact.


## Immutable call archive

Before backend verification or subprocess execution, the provider creates UTC `GenerationAttemptMetadata` and publishes `generation-attempt`. It binds a random attempt ID, producer, protocol, request/prompt/response IDs, request and output-schema hashes, all generation source-file hashes, and configured model/revision/manifest identities plus device/dtype. Config-file hashes and installed dependency versions are unknown at registration and are recorded in the subsequently verified backend/options and worker identity events. If this registration fails, no worker runs.

If a validated request, output schema, or fully templated prompt exceeds the declared stdin byte cap, the provider does not archive that oversized value. It publishes bounded `generation-preflight`, `generation-cost`, and `generation-status` objects alongside the attempt. The preflight object contains the bounded IDs, request/schema hashes, declared cap, observed component sizes, violated components, failure cause, and `execution_started=false`. The public exception remains `GenerationProviderError`; its record uses `GenerationInputLimitError` as the cause. A publication failure retains the same bounded payloads for replay without backend verification or execution. Requests with longer request/response/prompt identities are invalid at the M2 request boundary and cannot enter this archival path.

After execution, every successful or failed call publishes separate `generation-request`, `generation-response`, `generation-retrieval`, `generation-schema`, `generation-options`, `generation-provenance`, `generation-usage`, `generation-cost`, `generation-events`, and `generation-status` objects. The response receipt records exit status, termination, wall/CPU measurements, watchdog failures, sample count, maximum sampled and lifetime memory observations, any breach sample, stderr/stdout, and cleanup result. The raw JSONL event stream is retained byte-for-byte. The usage object always has `accepted_response_usage`; it is true only after both event and envelope/content validation. Every failure retains independently validated input/token observations and known cost counts while setting acceptance false.

An archive callback can itself be unavailable. `GenerationProviderError` then contains a partial `GenerationCallRecord`, known `cost`, bounded `response`, usage observations, and `GenerationPublicationRecovery`. `error.replay_publication(write_only_callback)` publishes only missing retained payloads and status without rerunning inference. A repeated storage failure returns another typed error with updated partial references and recovery state. The provider cannot promise an artifact when the store cannot write; it exposes that condition as `ArchivePublicationError` rather than losing measurements or silently generating again.

Initial-authoring records use authoring visibility; later checker-generation records use private visibility. The cost record is deliberately partial: wall time, CPU time, and accepted token counts are measured when present; GPU time, energy, human time, and currency cost remain null/unknown. Local execution does not imply zero compute cost merely because no remote API was used.

The production evidence commands and complete receipts are described in `docs/evidence/M2/production-provider-smoke.md`.

# M2 local generation-provider interfaces

This interface covers the bounded local generation provider. It does not yet contain the Click requirement contract, scenarios, checker implementation, or reward. Import the public boundary from `feature_rl.generation`; M0 retains ownership of shared artifact schemas and storage.

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

Protocol v3 defines an exact strict schema for every identity, input, memory-control, model-loaded, token, completion, and input-rejection event. Every event repeats the protocol/request/response/prompt identity. The identity event binds the model revision, config/tokenizer/weight hashes, manifest hashes, exact dependency versions, seed, prompt hash, staged-worker hash, offline flags, and remote-code setting. Extra fields, missing fields, wrong exact types, contradictory identities, positive/nonfinite selected-model logprobs, duplicate JSON keys, tool events, event-order drift, inconsistent usage, nonzero exits, cleanup failure, and resource termination all raise `GenerationProviderError`.

Envelope content is re-serialized and validated through Pydantic's strict JSON transport. This preserves strict M0-style tuples, enums, and UTC timestamps while still rejecting their invalid JSON representations; the provider does not weaken the output schema by converting it through permissive Python validation.

`GenerationResult` contains the caller-schema-validated `content`, exact `GenerationUsage`, partial `CostRecord`, and `GenerationCallRecord`. Usage includes the exact fully templated input token IDs, every emitted token ID, the selected token's pre-sampler model log-softmax score, and `sampling_policy="greedy_argmax"`. Pinned `mlx_lm.generate_step` applies argmax to those model scores; the scores are not log probabilities under the deterministic behavior distribution. `behavior_logprobs` is therefore explicitly null. A downstream training record must not substitute these model scores for behavior logprobs or invent behavior probabilities by retokenizing. A call at the emitted-token limit is marked truncated and rejected even when its bytes parse as valid JSON. These records make an authored sample attributable; they do not by themselves establish training suitability or a learning result.

## Stage and context boundary

Every `AuthoringContext` carries a fixed `context_id`, explicit role, immutable source reference, locator, text, and provenance label. Request, response, prompt, context, and allowed-requirement IDs must be fixed before inference; placeholders and duplicate IDs reject.

| Stage | Admitted explicit context | Forbidden context |
|---|---|---|
| `discovery` / `initial_authoring` | request, B/baseline, and public-check data with public or authoring visibility | H, diffs, reference trees, `SourcePair`, private/evaluation data, frozen private scenarios, and ambient stores |
| `checker_generation` | required frozen `RequirementContract` and `ScenarioPlan`, plus explicitly supplied attributable request/B/public-check context | reference implementation/H data, non-allowlisted roles or kinds, and private B/public-check data |

The initial contract draft therefore occurs without H or privileged checker inspection. After that draft is frozen, a controller may call the later checker stage with the frozen contract and private/evaluation scenario plan as explicit data. This later privilege does not broaden initial-authoring inputs and does not give the model access to the underlying store.

## Resource and token contract

`GenerationLimits` declares positive wall, CPU, stdin, retained-output/file, input-token, emitted-output-token, MLX memory, and external physical-footprint limits. The fully templated chat input is tokenized before model loading and rejects above its declared input limit. `mlx_lm.generate_step(max_tokens=...)` bounds emitted tokens; the controller independently verifies the event count and refuses truncation.

The `tiny_smoke_2048x128` measurement profile is qualified only through 2,048 actual input tokens and 128 emitted output tokens. `larger_unqualified` permits a caller to declare a larger positive pair within the pinned model's 262,144-token architectural context capacity. That architectural capacity is not a measured host-usable envelope. Before a first construction batch uses larger values, the controller must record a separate bounded measurement under the same watchdog, CPU, wall, and output policy. No larger call was made in this slice.

| Resource | Boundary and evidence meaning |
|---|---|
| wall time | external monotonic deadline, at most 120 seconds per current call |
| CPU time | kernel `RLIMIT_CPU`, declared positive integer seconds, at most 120 for the current interface |
| file output | kernel `RLIMIT_FSIZE`; retained stdout+stderr is also monitored against the combined byte cap |
| stdin | controller rejects serialized input above its declared cap before spawn; worker has a 1 MiB absolute defense |
| emitted tokens | native generation-loop maximum plus controller event-count check; truncation rejects |
| MLX allocation | 3.5 GiB MLX memory guideline and wired limit with the MLX cache limit set to zero |
| process physical footprint | external `proc_pid_rusage` sampling every 20 ms; process group killed above 4 GiB; any missing/exceptional observation fails the call immediately |
| declared memory ceiling | 5 GiB, leaving a 1 GiB guard band above the sampled kill threshold |
| process cleanup | from successful spawn onward, a `finally` path kills/reaps the fresh process group after normal exit, boundary failure, monitor exception, or interruption and checks absence |

The physical-footprint boundary is sampled and can overshoot between polls. It is not a zero-transient allocator reservation. The MLX limit is a library guideline. Receipts report the maximum sampled physical footprint separately from the kernel-reported lifetime maximum. Process count is observed through the single fixed worker design and group cleanup; this interface does not claim an OS-enforced one-process quota.

## Immutable call archive

Before backend verification or subprocess execution, the provider creates UTC `GenerationAttemptMetadata` and publishes `generation-attempt`. It binds a random attempt ID, producer, protocol, request/prompt/response IDs, request and output-schema hashes, all generation source-file hashes, and configured model/config/manifest/dependency identities. If this registration fails, no worker runs.

After execution, every successful or failed call publishes separate `generation-request`, `generation-response`, `generation-retrieval`, `generation-schema`, `generation-options`, `generation-provenance`, `generation-usage`, `generation-cost`, `generation-events`, and `generation-status` objects. The response receipt records exit status, termination, wall/CPU measurements, watchdog failures, sample count, maximum sampled and lifetime memory observations, any breach sample, stderr/stdout, and cleanup result. The raw JSONL event stream is retained byte-for-byte. The usage object always has `accepted_response_usage`; it is true only after both event and envelope/content validation. Every failure retains independently validated input/token observations and known cost counts while setting acceptance false.

An archive callback can itself be unavailable. `GenerationProviderError` then contains a partial `GenerationCallRecord`, known `cost`, bounded `response`, usage observations, and `GenerationPublicationRecovery`. `error.replay_publication(write_only_callback)` publishes only missing retained payloads and status without rerunning inference. A repeated storage failure returns another typed error with updated partial references and recovery state. The provider cannot promise an artifact when the store cannot write; it exposes that condition as `ArchivePublicationError` rather than losing measurements or silently generating again.

Initial-authoring records use authoring visibility; later checker-generation records use private visibility. The cost record is deliberately partial: wall time, CPU time, and accepted token counts are measured when present; GPU time, energy, human time, and currency cost remain null/unknown. Local execution does not imply zero compute cost merely because no remote API was used.

The production evidence commands and complete receipts are described in `docs/evidence/M2/production-provider-smoke.md`.

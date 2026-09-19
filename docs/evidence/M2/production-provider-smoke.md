# Production local-provider evidence

Date: 2026-09-19. Backend: local MLX 0.32.2 / MLX-LM 0.31.3 with `mlx-community/Qwen3-4B-Instruct-2507-4bit` at full revision `50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b`. No API, CUDA, remote compute, download, remote code, historical code execution, or generated-code execution occurred.

The evidence driver is `run-production-provider-smoke.py`. It calls the production `LocalGenerationProvider`, production backend verifier, production worker, production process runner, and production `ArtifactStore` write capability. The task-private store is under ignored `.feature-rl/research/M2/provider-production-store`; the checked receipts reproduce every stored payload and artifact reference. The checked JSON receipts are preserved outputs of production protocol revision 1. The current code is protocol revision 2 after the source-grounded corrections described below; no receipt is relabeled as evidence of revision 2.

The revision-1 JSON files did not record a top-level UTC invocation timestamp, producer identity, source revision, or product-file hash inventory. The invocations occurred on 2026-09-19, but their exact UTC start times and exact uncommitted source-tree binding are unavailable and are not reconstructed after the fact. Their raw bytes are retained unchanged. `provider-v2-verification.json` separately records the no-inference revision-2 focused/full test commands, exit statuses, raw-output hashes, UTC recording time, base revision, and current product-file hash inventory. It explicitly leaves the committed product revision null; a later coordinator receipt must bind the reviewed commit.

## Pre-inference input rejection

Command:

```sh
PYTHONPATH=src .venv/bin/python \
  docs/evidence/M2/run-production-provider-smoke.py reject-input \
  > docs/evidence/M2/production-provider-input-rejection.json \
  2> docs/evidence/M2/production-provider-input-rejection.stderr
```

Status: exit 0 from the evidence driver, confirming the expected provider rejection. The fully templated input contained 381 tokens against a declared 16-token maximum. The worker emitted `identity_validated` and `input_rejected`, reported `model_load_started=false` and `inference_started=false`, and exited 65. There is no `model_loaded` event. The provider rejected the call and retained all ten request/response/retrieval/schema/options/provenance/usage/cost/events/status archives. stderr is an intentionally retained zero-byte file.

## Structured production smoke

Both attempted inference receipts are retained. Attempt 1 succeeded at generation and schema validation, but review found that its checked response archive omitted the process runner's memory observations even though the runner had measured them. The implementation was corrected to include those fields; attempt 2 is the final production receipt. This was one diagnosed archive repair, not a model or protocol fallback. Both calls used the same pinned model, prompt, schema, greedy options, and limits.

Final command:

```sh
PYTHONPATH=src .venv/bin/python \
  docs/evidence/M2/run-production-provider-smoke.py smoke \
  > docs/evidence/M2/production-provider-smoke-attempt-2.json \
  2> docs/evidence/M2/production-provider-smoke-attempt-2.stderr
```

Attempt 2 exited 0 and returned the strict content `{"nonce":"M2-PROD-1","status":"BLUE"}` with the assigned response, source, and requirement IDs. The measured input was 383 tokens. The worker emitted 83 token events, including the final stop token, and each carried a finite selected-token model score (misnamed `behavior_logprob` in this pre-correction receipt); the output stopped normally and `truncated=false`. The configured limits were 2,048 input tokens, 128 emitted output tokens, 120 wall seconds, 120 CPU seconds, 1 MiB stdin, 1 MiB combined retained output/file size, 3.5 GiB MLX guideline/wired, zero MLX cache, a 4 GiB sampled kill threshold at 20 ms, and a declared 5 GiB ceiling.

The controller measured 5.811231250176206 wall seconds and 4.042833 CPU seconds, with 212 physical-footprint samples. The maximum sampled physical footprint was 3,145,353,928 bytes. The distinct kernel lifetime-maximum observation was 3,146,140,360 bytes. MLX reported a 2,805,252,776-byte peak allocation. The process exited normally, the process group was absent after cleanup, and stdout/stderr stayed within the cap; stderr is the retained zero-byte file. The response, raw JSONL events, exact 83 output token IDs/model scores, options, model identity, usage, partial cost, and all artifact references are in `production-provider-smoke-attempt-2.json`.

Source review after these two bounded inferences corrected two receipt semantics without another model call. The raw fields named `behavior_logprob` in attempts 1 and 2 are the selected tokens' pre-sampler model log-softmax scores from pinned `mlx_lm.generate_step`; they are not log probabilities under greedy argmax behavior. Those receipts also contain the exact templated input count but not its token-ID sequence. The final worker/provider protocol now names the values `selected_model_logprob`, sets behavior logprobs explicitly unavailable, records `sampling_policy=greedy_argmax`, and archives all fully templated input token IDs on acceptance. Unit tests exercise the corrected serialization. The final worker also preserves existing `HOME` and redirects only task-specific caches; the two earlier calls used a temporary child `HOME` but did not change the user environment. No third inference was run because the authorized two-attempt bound had been reached. These limitations mean attempts 1 and 2 prove the model/resource/schema path but are not byte-for-byte receipts from the final corrected event schema.

The revision-1 smoke establishes the named model/resource/schema path at this small envelope. The focused revision-2 tests establish its corrected field validation and archive semantics without another inference. Neither qualifies a larger host-usable contract/scenario envelope or establishes trainability, CUDA execution, or feature-RL learning. All attempted calls and their costs remain retained, and no candidate construction occurred.

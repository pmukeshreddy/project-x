# M7 training-core slice receipt

Implemented the independent first training slice: valid-only four-outcome GRPO grouping; exact M0 TokenTrace/policy/context validation; family/feature-balanced deterministic sampler and bounded signal accounting; M0/M4 data joins with a mandatory authoritative admission seam; supervised target preparation from the exact verified source; actual Torch causal SFT and sampled-probability clipped GRPO updates; frozen reference; masked token/KL losses; changed-tensor/optimizer receipts; hashed atomic checkpoint save/reload with optimizer and RNG restoration; policy identity/readiness barrier; and a lazy adapter that calls the pinned SkyRL tensor/update/sync APIs.

Validation command (CPU diagnostic environment provisioned separately by root; core/MLX environment unchanged):

```sh
PYTHONPATH=src .venv-training-cpu/bin/python -m pytest tests/test_training_core.py tests/test_training_torch.py tests/test_training_data.py tests/test_training_state.py tests/test_training_skyrl.py -q
```

Result: **22 passed**. Output: `docs/evidence/M7/training-core/focused-tests.txt`. Actual Torch2.11 CPU tests verify SFT and GRPO nonzero gradients, changed parameters, populated AdamW state, frozen reference, masked token gradients, zero-gradient weight-decay skip, saved/reloaded optimizer/RNG and identical subsequent update tensors. Invalid source-token/template/policy/seed/checkpoint joins reject. Source AST inspection checks actual pinned trainer method names; it does not execute SkyRL. All model/task/grade/token data in tests are explicitly synthetic diagnostics. No feature-learning evidence is claimed.

Tests were introduced before the corresponding modules and failed for missing functionality. Later source/archive interface and uninitialized barrier regressions were observed red and fixed against actual M3 API (`SourceArchive.read(payload, SandboxPolicy())`) and explicit initialized-policy checks. Only owned focused tests were run during parallel M5/M6 work, per coordinator instruction; no repository-wide result is claimed.

No model download, native generation, GPU invocation, dependency change, shared config/CLI change or new compatibility research occurred. Existing source audit pins are unchanged. Raw research remains ignored. Torch imports are lazy; core import works in the original no-Torch environment.

This is **not full M7 completion**. Remaining code work: actual AgentRunner/action protocol and M3/M4 execution lifecycle; authoritative M5/M6 released-task/revocation adapter; trusted harness SFT renderer and per-example provenance publication; complete TrainingConfig/OperationResult service with budgets, retries, signal gating and costs; distributed SkyRL experiment construction/native checkpoint metadata+resume and fresh worker probe collection; actual model/tokenizer gateway; runnable train/resume/evaluate integration commands and M0 artifact publication. The current low-level data gate requires a supplied authoritative resolver and has no default admission bypass. No M0 TrainingCheckpoint or released task has been manufactured.

GPU/FSDP/vLLM execution, actual native imports, forwarded arm64 worker endpoint, real released-feature episodes, useful signal, full-stack update/reload and experiments remain **unverified/deferred**. Their absence does not block the next code slice. Existing owner remains responsible for runner and remaining training integration after this checkpoint's independent review.

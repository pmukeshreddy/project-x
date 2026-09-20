# Native launch and recovery contract (execution deferred)

The implemented controller uses pinned SkyRL `f5bc3b78dfddfb352870d5d7430cd226e5785838` and Harbor `3de07a0e01f3368921766437fc7afece3ddec23d`. It requires Linux x86_64, Python 3.12, compatible NVIDIA CUDA hardware and the preserved upstream FSDP/Harbor environment. This Mac's arm64 Docker worker and CPU Torch diagnostics do not qualify that controller. No native training, inference, CUDA initialization, package installation or model download was performed for this checkpoint.

Follow [the exact dependency overlay decision](../decisions/training-dependency-overlay.md): frozen upstream FSDP + Harbor extras, then only the approved hash-pinned project overlay with `--no-deps --require-hashes --no-index --find-links`. Retain upstream Torch/vLLM/custom wheel sources. Use the resulting interpreter directly or `uv run --offline --no-sync`; another ordinary sync can undo the project overlay. The retained upstream metadata declares extras `fsdp` and `harbor`; the environment setup must record its complete installed inventory and verify the approved overlay before launch. M0 owns that installation closure.

Operator inputs must identify actual controller-owned model/reference/tokenizer directories with no symlinks or group/other-writable ancestry, immutable per-file manifests, and a separate private work directory. `publish_directory` imports model/reference **manifests**, not tensor payloads. `NativeSettings.tokenizer_sha256` is the SHA256 of canonical JSON mapping every regular relative tokenizer file to its raw SHA256. `PolicyConfig.identity.tokenizer_digest` must match. Native policy provider is `skyrl`, the model identifier must match the configured initial policy, the public prompt must use `HARNESS='feature-rl-source-v1'`, and training probabilities require temperature 1/top_p 1. Freeze valid tokenizer probe IDs explicitly; do not substitute arbitrary IDs. The canonical public tool artifact comes from `feature_rl.agents.protocol.protocol_payload()` with kind `m7-tool-protocol`; `validate_protocol` authenticates it. The pinned FSDP optimizer family is `adamw`.

M3 uses the explicitly configured protected Docker socket and the platform recorded in the repository runtime profile. See [the endpoint decision](../decisions/training-worker-endpoint.md). Forwarding that endpoint from a future CUDA controller remains unverified. Composition invokes actual M3 qualification; task resolution invokes current M5/M6 acceptance/revocation verification. A provisional TEST fixture does not satisfy these gates.

A native GRPO launch uses `algorithm='grpo'`, an explicit `group_size >= 2` (four is the default sampler size), one frozen training seed and actual released TRN tasks. Configure `groups_per_update`, `mini_batch_groups`, `max_updates`, `max_groups` and `max_wall_seconds` explicitly. Use a context limit large enough for each complete sampled turn and a frozen probe of 1–256 valid input tokens with 1–32 output tokens. The service checks nonzero gradients plus changed trainable and optimizer tensor content, then exact FSDP/controller RNG checkpoint reload and agreeing inference probes. These checks have not been executed on the native GPU stack. GPU memory fit remains an external hardware/model requirement.

GRPO uses `group_size` distinct episode seeds and one explicit case seed per assigned group. There is no separate difficulty admission probe: useful groups enter GRPO immediately, while zero-advantage groups retain their costs and assigned positions without an optimizer step. Set the frozen roster, update/group/wall limits and initial/reference manifests explicitly. The current unknown-cost profile requires `budget_usd=null`; a finite currency cap is rejected without an actual native cost meter. This does not authorize paid resources. Wall checks occur between bounded phases; native lifecycle deadlines and sampled M3 CPU cleanup tolerances are not exact aggregate deadlines.

This direct library command is executable with real operator JSON files and the qualified environment. The M6 CLI wraps the same constructors. It does not provision anything or generate tasks. The four arguments are services JSON, NativeSettings JSON, TrainingConfig JSON, and a frozen invocation identifier:

```sh
/srv/feature-rl/skyrl/.venv/bin/python - services.json native.json training.json grpo-001 <<'PY'
import sys
from feature_rl.cli import read_json
from feature_rl.pipeline.configuration import CLIConfiguration, compose
from feature_rl.contracts import TrainingConfig
from feature_rl.training.native import NativeSettings
from feature_rl.training.service import TrainingService
from feature_rl.artifacts import canonical_json

services = read_json(sys.argv[1], CLIConfiguration)
settings = read_json(sys.argv[2], NativeSettings)
config = read_json(sys.argv[3], TrainingConfig)
app = compose(services, qualification=True)
trainer = TrainingService(store=app.factory.store, registry=app.factory.registry,
    lifecycle=app.resolver, builder=app.factory.builder, runtime=app.runtime,
    grader=app.grader, settings=settings, revision=services.revision)
try:
    result = trainer.train(config, invocation=sys.argv[4])
finally:
    trainer.close()  # Cleanup only; an exception preserves the original failure as context.
print(canonical_json(result.model_dump(mode='json')).decode())
PY
```

Ordinary native run composition instead creates an inert `NativeSessionFactory` with the explicit bootstrap TrainingConfig, then `NativeRunService(..., native_factory=factory)`. Call `run(task, policy, limits, case_seed=<frozen int>, invocation=<frozen ID>)`. Omitted ordinary case seed defaults to policy.seed. The service selects the outer run claim before startup/activation, calls exactly one child AgentRunner episode and closes native ownership before selecting its parent result. The returned RolloutRecord retains the actual child run job ID. The parent includes child costs once plus incremental native startup/activation/shutdown and unknown controller/storage channels.

Clean training interruption: repeat the identical invocation and inputs in the same private work directory. The durable journal restores confirmed sampler positions and exact optimizer/RNG state; completed jobs return their selected result. An unknown native update is never replayed implicitly.

Explicit interrupted-update recovery: retain the exact last confirmed TrainingCheckpoint ArtifactRef and original private controller journal. Confirm/reconcile shutdown of every old native startup first. Use the **same** frozen settings, TrainingConfig and implementation, a new invocation, and `trainer.train(config, invocation='recovery-001', resume=checkpoint_ref)`. Recovery authenticates the checkpoint itself, progress and update/reload receipt in the original claim's selected update observation. It restores confirmed tensors/optimizer while retaining the original journal's assigned-data high-water mark, skipping later unknown dispatched data and preserving the original costs. Unknown dispatches consume update budget separately from confirmed optimizer_steps. The original journal is superseded; outputs use `work_directory/jobs/<new job_id>`, and old tensor directories remain immutable. Recovery does not extend the frozen update budget or make unknown work into a successful step. Missing journals, unconfirmed cleanup or a checkpoint older than a later confirmed update stop for reconciliation.

Ordinary run recovery uses `native_run.recover(original_claim)` after publication failure. It republishes retained or selected child work, including pending source/grade publication, without policy resampling. Durable parent completion can recover after confirmed shutdown without another native session. A lost startup/activation/child outcome stops for reconciliation. An unpublished retained startup has a cleanup-only `NativeSessionFactory.close_startup` path, including after quarantine; unknown startup accounting stays unknown.

For terminal CLI execution, call `TrainingService.close()` / `NativeRunService.close()` in `finally` and preserve/report cleanup errors with the original failure. TrainingService permits an in-process startup-publication retry until explicitly closed; terminal cleanup closes `_opening` ownership without requiring current task usability. NativeRunService also aborts unpublished startup automatically on its terminal failure. Cleanup never initializes or generates. When explicit training recovery has no remaining update budget after unknown dispatches, it returns BLOCKED before native initialization and does not select the original job's checkpoint as a new successful output.

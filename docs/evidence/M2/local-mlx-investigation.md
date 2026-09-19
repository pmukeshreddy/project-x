# M2 local MLX authoring-backend qualification

Date: 2026-09-19 UTC. This investigation explicitly selects local MLX with `mlx-community/Qwen3-4B-Instruct-2507-4bit` as the candidate authoring backend. It is a disclosed stack decision after the Codex CLI hard-cap path failed qualification; it is not a silent model or provider fallback. The earlier CLI evidence remains authoritative in `rollout-budget-qualification.md`.

No Click program, historical candidate, generated program, private check, repository feature input, training job, CUDA job, remote inference service, or paid model API was run. The single inference prompt was synthetic. No product source, `pyproject.toml`, production lock, global package, package-user configuration, `HOME`, or unrelated model cache was changed.

## Disposition

The named local stack passed a bounded one-request feasibility smoke:

- exact model and tokenizer files were loaded from a positive local allowlist at revision `50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b`;
- remote/custom code was disabled and rejected by configuration checks;
- an oversized 4,105-token request was rejected before model load or inference against the 2,048-token input limit;
- the accepted request contained 26 actual tokenizer tokens;
- generation yielded exactly 16 tokens, no seventeenth token, with `finish_reason="length"` and `truncated=true`;
- the process exited 0 in 4.524 seconds with 5,010 retained output bytes, zero stderr, 2.393 GB MLX peak allocation, and a 2.726 GB maximum sampled physical footprint; and
- the process group was absent after exit.

No second inference attempt was needed or made. This qualifies the exact stack for later provider implementation, subject to M0 accepting the pinned dependency closure and the memory-limit qualification below.

## Model provenance and acquisition

The model is `mlx-community/Qwen3-4B-Instruct-2507-4bit`, pinned to full Hugging Face revision `50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b`. The official repository metadata reports a 4-bit Qwen3 text-generation model, last modified `2026-01-02T17:11:57Z`, derived from `Qwen/Qwen3-4B-Instruct-2507`, and declares `apache-2.0`.

The quantized repository tree does not contain a license file. Its model-card metadata links to the base-model license. That Apache 2.0 text was acquired from base-model revision `cdbee75f17c01a7cc42f958dc650907174af0554`; it is 11,343 bytes with SHA-256 `832dd9e00a68dd83b3c3fb9f5588dad7dcf337a0db50f7d9483f310cd292e92e`. This relationship is preserved as a provenance qualification rather than treating a mutable `main` link as the license pin.

The runtime allowlist contains exactly 11 safetensors, tokenizer, and configuration files. It excludes `.gitattributes`, `README.md`, every Python file, and every other repository path. The bounded downloader used a 3,000,000,000-byte cumulative cap and an 1,800-second monotonic deadline. It acquired 2,278,969,697 bytes in 52.321 seconds. Each actual file has a retained SHA-256. Both hashes published by Hugging Face match: `model.safetensors` is 2,263,022,417 bytes with SHA-256 `2a73c6c248601ab904e035548abd8e6abb65ea27dcb5f342fb0a8910eb44173f`; `tokenizer.json` is 11,422,654 bytes with SHA-256 `aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4`.

Local validation opened the weight file through safetensors, found 904 tensors and MLX format metadata, confirmed that the index maps every weight to the one allowlisted safetensors file, and confirmed `model_type="qwen3"`, architecture `Qwen3ForCausalLM`, and 4-bit/group-size-64 quantization. The config contains neither `model_file` nor `auto_map`.

`local-mlx-model-acquisition.json` contains all file sizes, hashes, and revision-pinned acquisition URLs. `local-mlx-provenance.json` contains the repository, source, release, and license pins.

## Dependency proposal

The task-private Python 3.13.7 environment uses only wheels:

- `mlx==0.32.2`, wheel SHA-256 `df8c75e509de868fca148dfeb38d92ce956eed386569c87caeb72bd16d2d6962`;
- `mlx-metal==0.32.2`, wheel SHA-256 `e6abeac9ac5265830c9c1541b6f96e9be37a85c2446763a46ad466c63a3837ab`; and
- `mlx-lm==0.31.3`, wheel SHA-256 `758cfddf1180053b7613db76fad3d246a331a2a905808e1164a275621fc983b8`.

The complete 34-wheel closure is 96,889,626 bytes, below the 500 MB dependency limit. Every wheel was fetched from `files.pythonhosted.org` through a bounded downloader, checked against the resolver-provided SHA-256, then installed with `--no-index`, `--only-binary=:all:`, and `--require-hashes`. No build hook ran. `pip check` reported no broken requirements. `local-mlx-dependencies.json` contains every resolved package, version, filename, source URL, size, and hash; `local-mlx-install.json` contains the installed freeze and install status.

The official tags pin MLX v0.32.2 to commit `1f8e74e3f12f31365464a6867c6579f0e9b29d85` and MLX-LM v0.31.3 to commit `ed1fca4cef15a824c5f1702c80f70b4cffc8e4dd`. The installed `mlx_lm/generate.py` and `mlx_lm/utils.py` are byte-identical to those tagged source files. MLX, MLX Metal, and MLX-LM declare the MIT license.

One preparation error is retained rather than hidden: the initial `pip --dry-run` used the task-private venv but omitted `PIP_CACHE_DIR`. Pip may have reused or populated its ordinary cache while resolving metadata and downloading candidates. It installed no package and changed no pip configuration. No ordinary cache entry was removed. All subsequent wheel acquisition and installation used task-private paths.

## Input, output, and context controls

The loader receives a local directory, `local_files_only=True`, and `trust_remote_code=False`, with `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`. Before loading weights, it requires the exact file allowlist, rehashes both config and 2.263 GB weights, rejects `model_file` and `auto_map`, and validates the architecture. This closes the installed MLX-LM loader branch that otherwise imports a custom file named by `config["model_file"]`.

Input tokens are counted after applying the pinned tokenizer's chat template. The rejection proof measured 4,105 tokens, reported `model_load_started=false` and `inference_started=false`, and exited 0 without loading weights. An initial pre-inference proof is also preserved; it was repeated only to add in-process weight-hash verification, not because the rejection failed. The accepted smoke measured 26 tokens against the same 2,048 limit.

MLX-LM v0.31.3's pinned `generate_step` loop stops yielding when its counter reaches `max_tokens`; the higher-level stream response classifies a maximum-length result as `length`. The smoke used greedy argmax, seed 0, and `max_tokens=16`. Its 16 raw token events decode to `BLUE` repeated 16 times, then the wrapper classified the result as length-truncated. A source-level nuance remains: `generate_step` evaluates the next-token operation asynchronously before yielding the current token, so it may compute one lookahead candidate even though it never yields or returns more than the declared token limit. The proven hard bound is on emitted output tokens.

Every inference starts a fresh operating-system process and passes no prompt cache, so it has a fresh generation context and cache. The cache directory is task-private and the process runs offline. `local-mlx-smoke-plan.json` records all limits before inference; `local-mlx-smoke-attempt-1.json` preserves the exact command, environment, raw token events, output, timings, memory readings, exit status, and cleanup result.

## Memory qualification

The machine is an Apple M4 with 17,179,869,184 bytes of unified memory. MLX 0.32.2 reports Metal available and a 12,713,115,648-byte maximum recommended working set. The inference process sets the MLX memory guideline and wired limit to 3.5 GiB and disables the MLX free cache. Its external watchdog polls macOS `proc_pid_rusage(RUSAGE_INFO_V4).ri_phys_footprint` every 20 ms and kills the process group above 4 GiB. This leaves 1 GiB below the declared 5 GiB process/device-memory ceiling.

The watchdog was separately exercised with a synthetic allocator at a 64 MiB threshold. It observed 74,465,760 bytes, terminated the process with SIGKILL after 0.211 seconds, and verified that the process group was gone. The 7,356,896-byte sampled overshoot shows the limitation: this is an enforceable sampled termination boundary with a guard band, not an allocator reservation that prevents every transient byte above its threshold. MLX's own `set_memory_limit` documentation calls that setting a guideline and only promises allocation failure after memory and swap are exhausted. The smoke's observed 2.726 GB footprint remained 1.274 GB below the 4 GiB kill threshold and 2.643 GB below the declared 5 GiB ceiling.

The watchdog proof and its raw allocation events are in `local-mlx-memory-watchdog-proof.json`. If the production contract requires a mathematically strict no-transient 5 GiB allocator ceiling, this probe does not establish one; it establishes the named sampled watchdog with a measured guard band and a comfortably bounded observed run.

## Known limits and handoff

This is protocol and resource feasibility evidence for one pinned machine and package/model revision. It does not establish authoring quality, feature correctness, determinism across MLX releases or Apple GPUs, or a general latency bound. Local energy use was not measured. There was no provider billing or model API request.

M0 owns any production dependency and lock-file decision. M2 implementation should use the pinned local path, exact file manifest, offline and no-remote-code checks, pre-load input rejection, emitted-token cap and truncation classification, fresh process/cache, 120-second process deadline, 1 MiB output cap, and the external physical-footprint watchdog. It must treat any identity/hash/config drift, missing token record, output-token excess, memory/deadline/output termination, nonzero exit, or cleanup failure as a provider failure.

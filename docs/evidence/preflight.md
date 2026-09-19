# Execution preflight: feature RL pipeline

Audit completed: 2026-09-19T07:33:45Z. This was a read-only feasibility audit except that the coordinator started the already-installed Docker Desktop application under the task's existing authorization. No image was pulled, no container was launched for this audit, no package was installed, no credential value was read or printed, and no model inference or paid service was invoked.

## Disposition

| Capability | Status | Evidence and consequence |
| --- | --- | --- |
| Local Linux container engine | **Conditionally available** | Docker Desktop 27.4.0 is now running with a Linux/aarch64 engine. Before startup, the selected `desktop-linux` context had no socket and all daemon calls failed. The engine exposes 10 CPUs and 8,217,968,640 bytes (~7.65 GiB) to containers. |
| Required sandbox security boundary | **Unverified / blocking M3-M4 execution** | `docker info` reports `security=["name=seccomp,profile=unconfined","name=cgroupns"]`. No candidate or historical code should run until an explicitly hardened container is exercised and its effective controls are observed. Harbor's Docker egress controls also require Linux containers plus the nftables features used by its sidecar; configuration alone is not evidence that Docker Desktop supplies them. |
| Host compute for construction/orchestration | **Available but tight** | macOS 26.3 arm64, Apple M4, 10 CPU cores, 10 integrated GPU cores, 16 GiB unified memory. The workspace volume has 27 GiB free. Docker already accounts for 11.34 GB of images, 3.212 GB of container writable data, and 22.96 GB of local volumes. Preserve existing daemon state; it belongs to other work. |
| Local SkyRL CUDA/vLLM training | **Blocked** | There is no `nvidia-smi`, no NVIDIA CUDA device, Docker has only ~7.65 GiB, and no remote compute is configured. The official SkyRL Docker path uses NVIDIA runtime/GPU flags; the stable Harbor recipe uses Qwen3-8B with eight policy GPUs and four inference engines. This Mac can orchestrate and run small CPU checks, but it cannot supply the documented training backend. |
| Codex authoring provider on host | **Authenticated; inference untested** | `/opt/homebrew/bin/codex`, `codex-cli 0.154.0`; `codex login status` exits 0 with `Logged in using ChatGPT`. `OPENAI_API_KEY`, `CODEX_API_KEY`, `CODEX_ACCESS_TOKEN`, `ANTHROPIC_API_KEY`, and `GOOGLE_API_KEY` are all unset (presence check only). The configured subscription is authorized for implementation; this preflight stopped at request construction and made no model inference call. |
| Local SkyRL/Harbor install | **Absent** | `harbor` is not on `PATH`, `uv tool list` reports no tools, and `python3 -m pip show harbor skyrl` finds neither package. This is expected; installation must occur in a pinned construction container, not on the host. |

## Docker evidence

The engine was initially stopped:

```text
$ docker desktop status
Could not retrieve status. Is Docker Desktop running?
[exit 1]

$ docker info
ERROR: Cannot connect to the Docker daemon at unix:///Users/mukeshreddypochamreddy/.docker/run/docker.sock.
[exit 1]
```

After the coordinator started Docker Desktop, a bounded readiness loop (`docker info` once per second, deadline 30 seconds) reached ready without a fixed wait. The follow-up probes were:

```text
$ docker desktop status
Status  running
[exit 0]

$ docker version --format 'client={{.Client.Version}} server={{.Server.Version}} api={{.Server.APIVersion}} os={{.Server.Os}} arch={{.Server.Arch}}'
client=27.4.0 server=27.4.0 api=1.47 os=linux arch=arm64
[exit 0]

$ docker info --format 'name={{.Name}} os={{.OperatingSystem}} ostype={{.OSType}} arch={{.Architecture}} cpus={{.NCPU}} memory_bytes={{.MemTotal}} driver={{.Driver}} security={{json .SecurityOptions}}'
name=docker-desktop os=Docker Desktop ostype=linux arch=aarch64 cpus=10 memory_bytes=8217968640 driver=overlayfs security=["name=seccomp,profile=unconfined","name=cgroupns"]
[exit 0]

$ docker ps --format '{{.ID}}\t{{.Image}}\t{{.Status}}'
a64a4a85da69  moby/buildkit:buildx-stable-1  Up ...
[exit 0]

$ docker system df
Images 18 / 11.34GB; Containers 16 / 3.212GB; Local Volumes 8 / 22.96GB; Build Cache 391.5MB
[exit 0]
```

The audit did not stop, remove, inspect inside, or otherwise alter the pre-existing BuildKit container, images, containers, or volumes.

## Host compute evidence

```text
$ sw_vers
ProductName: macOS
ProductVersion: 26.3
BuildVersion: 25D125
[exit 0]

$ uname -m
arm64
[exit 0]

$ sysctl -n hw.memsize
17179869184
[exit 0]

$ sysctl -n hw.physicalcpu
10
[exit 0]

$ system_profiler SPDisplaysDataType
Apple M4; GPU; Total Number of Cores: 10; Metal 4
[exit 0]

$ zsh -lc 'command -v nvidia-smi'
[exit 1]

$ df -h '/Users/mukeshreddypochamreddy/Desktop/project x'
/dev/disk3s5  460Gi  395Gi  27Gi  94%  /System/Volumes/Data
[exit 0]
```

## Codex provider and automation surface

OpenAI's current authentication documentation says Codex CLI supports ChatGPT subscription login and API-key login, and `codex login status` reports the active method. The local result establishes an authenticated ChatGPT-backed CLI session, but not model availability, rate limits, or successful inference.

Local help and official non-interactive documentation establish these controls:

- `--json` emits JSONL events, including command/tool events and token usage.
- `--output-schema FILE` constrains the final response to a JSON Schema; it does not replace trace auditing.
- `--ephemeral` avoids persisted session files.
- `--sandbox read-only`, `--ignore-user-config`, and `--ignore-rules` reduce local authority and configuration inheritance.
- `--disable FEATURE` works with stable features. The local feature list marks `shell_tool`, `apps`, `hooks`, and `multi_agent` as stable. `-c 'web_search="disabled"'` disables hosted web search.
- Official guidance warns against exposing `OPENAI_API_KEY` or `CODEX_API_KEY` to a job that checks out or executes repository-controlled code. The same separation principle applies to cached ChatGPT credentials.

The following parser-only probe made no inference call and confirms that this installed CLI accepts the restrictive profile. The final `tools.experimental_request_user_input.enabled=false` override is required; feature flags alone leave one question tool in the request.

```text
$ codex exec -C /private/tmp --ephemeral --sandbox read-only \
    --ignore-user-config --ignore-rules \
    --disable shell_tool --disable unified_exec --disable apps --disable hooks \
    --disable multi_agent --disable browser_use --disable computer_use \
    --disable in_app_browser --disable image_generation --disable plugins \
    --disable remote_plugin --disable skill_search \
    --disable skill_mcp_dependency_install --disable workspace_dependencies \
    --disable tool_suggest --disable goals --disable memories \
    --disable sleep_tool --disable view_image \
    --disable default_mode_request_user_input \
    -c 'tools.experimental_request_user_input.enabled=false' \
    -c 'web_search="disabled"' --help
[exit 0]
```

### Source-isolation correction and executed configuration evidence

An earlier probe changed `CODEX_HOME` to an empty temporary directory. It reported an empty MCP list and no workspace strings, but it did **not** represent the authenticated profile and is retained here only as historical evidence of what was run:

```text
$ CODEX_HOME=<empty> codex mcp list --json
[]
[exit 0]

$ CODEX_HOME=<empty> codex debug prompt-input <all tool features disabled> \
    CODEX_PREFLIGHT_SENTINEL | jq <non-content summary>
{"json_type":"array","item_count":5,"workspace_path_hits":0,
 "source_filename_hits":0,"sentinel_hits":1}
[exit 0]
```

The corrected probe did not change `HOME` or `CODEX_HOME`. The actual current-home MCP inventory, with values and command arguments suppressed, is:

```text
$ codex mcp list --json | jq '[.[] | {name, enabled, disabled_reason}]'
chrome_devtools_webmcp enabled=true
computer-use           enabled=false
cua_repl               enabled=true
node_repl              enabled=true
[exit 0]
```

`codex debug prompt-input` and `codex mcp list` do not accept `--ignore-user-config` in CLI 0.154.0; both variants exit 2. Therefore their output cannot establish the effective isolated `exec` configuration. The correction used the actual current home and an empty staging CWD, and ran `codex exec` with `--ignore-user-config`, `--ignore-rules`, `--strict-config`, the complete disabled-feature profile above, and a credential-free loopback Responses capture provider. The loopback endpoint summarized only request metadata and returned HTTP 400; it never called a model.

The first captured `/v1/responses` request demonstrated why request-body inspection is necessary:

```text
CAPTURE_SUMMARY={"request_path":"/v1/responses","tool_count":1,
 "tool_names":["request_user_input"],"workspace_path_hits":0,
 "source_filename_hits":0,"sentinel_hits":1}
```

After adding both `--disable default_mode_request_user_input` and `-c 'tools.experimental_request_user_input.enabled=false'`, the corrected request was:

```text
CAPTURE_SUMMARY={"request_path":"/v1/responses","tool_count":0,
 "tool_names":[],"workspace_path_hits":0,
 "source_filename_hits":0,"sentinel_hits":1}
codex-capture-exit=1  # expected: capture endpoint rejected the request
staging-cleanup-exit=0
```

This executed request-construction check proves that the effective request exposed no tool definitions and contained neither the workspace path nor either source-document filename. It is configuration evidence, not successful provider inference. No machine-level Codex configuration was found at `/etc/codex/config.toml`, `/etc/codex`, `/Library/Application Support/OpenAI/Codex/config.toml`, or `/Library/Managed Preferences/com.openai.codex.plist`.

For an actual M2 generation call, create a fresh staging directory outside this repository whose manifest contains only B, the request, the approved admissible context, and the output schema; verify its ancestors contain no `AGENTS.md`; set that directory with `-C`; pipe the serialized input on stdin; add `--json --output-schema <schema>`; and use the corrected restrictive profile above. `--ignore-user-config` preserves authentication while excluding the user's config; the empty staging directory excludes project config, rules, skills, H, private tests, and repository-global instructions. Reject the generation if its JSONL contains any command, MCP, app, web-search, browser/computer-use, subagent, question, or file-change event. The configured ChatGPT subscription is available for authorized implementation use, but this preflight intentionally made no provider inference call.

Do not mount or copy `~/.codex/auth.json` into an untrusted worker. Host CLI authentication does not by itself solve the credential boundary for a coding-agent worker, so real solver execution remains unverified until credentials stay outside the candidate process or an approved local model endpoint is available.

## Current SkyRL/Harbor compatibility

The most defensible published integration pin is the unified SkyRL `skyrl-v0.3.0` tag, commit `f5bc3b78dfddfb352870d5d7430cd226e5785838`. Its official `pyproject.toml`:

- allows SkyRL on Python `>=3.11`, but enables the Harbor extra only on Python `>=3.12`;
- pins Harbor to commit `3de07a0e01f3368921766437fc7afece3ddec23d` rather than a floating release;
- that Harbor commit reports package version `0.13.1` and Python `>=3.12`.

The compatible Harbor commit is dated 2026-06-10. Its own changelog contains separate-verifier environments (2026-05-14) and phase-scoped network policy (2026-05-30), so the two architectural building blocks named in sections 7-8 are present in the pinned source.

Current tips are not a demonstrated compatible pair:

```text
$ git ls-remote https://github.com/NovaSky-AI/SkyRL.git HEAD refs/heads/main refs/tags/skyrl-v0.3.0
eb4a93f7a2553a966c6ea3ec79b61908ababfc2a  HEAD/main
f5bc3b78dfddfb352870d5d7430cd226e5785838  refs/tags/skyrl-v0.3.0
[exit 0]

$ git ls-remote https://github.com/harbor-framework/harbor.git HEAD refs/heads/main refs/tags/v0.23.0
2993946dd5b64a46dac3aa766d03065f432a1468  HEAD/main
3c305dc5611e3600afc3c818735a82322f0264f8  refs/tags/v0.23.0
[exit 0]
```

Harbor's current package is `0.23.0`, but SkyRL `skyrl-v0.3.0` does not declare Harbor 0.23 compatible. SkyRL main still pins the same Harbor commit, despite other dependencies and container guidance having moved. Therefore do not combine “latest SkyRL” and “latest Harbor”; begin with the exact stable SkyRL tag plus its lockfile and Harbor commit, then qualify any upgrade as a new compatibility pair.

The official stable integration recipe is also much larger than this host: `run_codecontest.sh` selects Qwen3-8B, FSDP, vLLM/NCCL, eight policy GPUs, four inference engines, tensor parallel size two, and defaults Harbor to the Daytona provider. The current SkyRL installation guide recommends a CUDA 13.0/NVIDIA runtime image and warns that older images are commit-bounded. Neither is evidence of local feasibility on Apple Metal.

## Smallest secure local route

1. **Continue M0-M2 and orchestration logic without historical execution.** Pure schemas, content-addressing, static parsing, archive validation, and controller tests can run locally. Treat repository contents and issue text as data. Do not invoke their setup scripts, imports, build hooks, or tests on the host.
2. **Pin the first integration container before installation.** Use Linux arm64 + Python 3.12, SkyRL `skyrl-v0.3.0` (`f5bc...`), its checked-in lockfile, and Harbor `3de07a0...`. Record the base-image digest and dependency hashes in `EnvironmentRecipe`. Build/install only inside a disposable construction container. Select and record the upstream digest before the ordinary implementation pull; no separate approval pause is required.
3. **Prove the container boundary before using historical code.** The worker must have an explicit seccomp profile (the daemon default is unconfined), `no-new-privileges`, all capabilities dropped with only justified additions, non-root UID, read-only root filesystem plus bounded tmpfs, pids/CPU/memory/time limits, no host namespaces, no Docker socket, no host home, and no credential mounts. Exercise filesystem escape, process isolation, timeout/reap, resource exhaustion, and egress-denial controls and record observed results.
4. **Keep controller, agent worker, and verifier separate.** The trusted controller alone owns hidden expectations and Docker orchestration. Use Harbor's `verifier.environment_mode = "separate"`; transfer only an allowlisted source artifact. Inspect both explicit and default artifact paths. Candidate code never runs or imports in the controller or verifier image-build process.
5. **Verify Harbor networking on this actual backend.** Start with `no-network`; if an agent endpoint is needed, allowlist only that endpoint. Test DNS, IPv4, IPv6, direct-IP, redirect, and phase-switch behavior. Harbor documents that local Docker allowlisting depends on nftables support; reject the trial if the effective policy cannot be demonstrated.
6. **Use Codex only in a credentials-separated role.** M2 authoring may use the empty-staging-directory, tool-free profile above: only B and explicitly admissible evidence enter stdin, and H/private tests never enter the directory or prompt. Inspect JSONL and reject unexpected tool activity. Do not expose cached ChatGPT auth to candidate code. A real solver needs a model gateway or agent design in which the untrusted shell cannot read provider credentials; this remains a concrete M7 gate.
7. **Defer the RL update/reload gate.** Local construction and single-task grading can proceed after sandbox qualification. SkyRL FSDP/vLLM training requires authorized Linux/NVIDIA compute. No such host or budget is configured, so section 18 step 4 and all GPU-backed M7 claims remain `blocked or unverified` rather than simulated.

## Actionable blockers

- **M3/M4 sandbox execution:** Docker is live, but the daemon reports unconfined seccomp and Harbor nftables enforcement has not been exercised. Next action: select and pin a small trusted Linux arm64 probe image, then run the hardened boundary/egress qualification before any repository-derived command.
- **M7 coding-agent credential isolation:** Host Codex is authenticated, but placing its cached credential in the worker would violate the secret boundary. Next action: choose a credential-brokering or external-agent design and prove the worker cannot read the credential.
- **M7 training and section 18 step 4:** no CUDA GPU or remote compute is available. Next action: obtain an explicitly authorized Linux/NVIDIA target and budget, then run the exact pinned SkyRL/Harbor smoke before freezing the stack.
- **Disk headroom:** only 27 GiB is free while Docker already holds substantial unrelated state. Next action: estimate the pinned images/model/artifacts before building. Do not delete existing Docker state without its owner's direction.
- **Version freeze:** Harbor 0.23.0 is current independently, but is not the SkyRL-declared integration pin. Next action: smoke `skyrl-v0.3.0` + Harbor `3de07a0...` first; any later pair needs its own executable compatibility evidence.

## Primary sources checked on 2026-09-19

- OpenAI Codex authentication: https://developers.openai.com/docs/auth
- OpenAI Codex non-interactive mode: https://developers.openai.com/docs/non-interactive-mode
- OpenAI Codex configuration: https://developers.openai.com/docs/config-file/config-basic
- SkyRL stable integration metadata: https://github.com/NovaSky-AI/SkyRL/blob/skyrl-v0.3.0/pyproject.toml
- SkyRL Harbor example and resource configuration: https://github.com/NovaSky-AI/SkyRL/tree/skyrl-v0.3.0/examples/train_integrations/harbor
- SkyRL current installation constraints: https://docs.skyrl.ai/docs/getting-started/installation
- SkyRL custom-agent/Harbor generator contract: https://docs.skyrl.ai/docs/tutorials/agent-integration
- Compatible Harbor commit: https://github.com/harbor-framework/harbor/commit/3de07a0e01f3368921766437fc7afece3ddec23d
- Harbor separate verifier: https://docs.harborframework.com/core-concepts/tasks/separate-verifier
- Harbor network policies: https://docs.harborframework.com/core-concepts/tasks/network-policies
- Harbor current release metadata: https://github.com/harbor-framework/harbor/blob/main/pyproject.toml

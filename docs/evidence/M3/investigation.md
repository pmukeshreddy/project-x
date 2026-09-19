# M3 bounded sandbox feasibility investigation

Investigated 2026-09-19 UTC while M1 implementation proceeded. This is **trusted boundary evidence only**, not M3 implementation, an accepted `EnvironmentRecipe`, Click baseline execution, or candidate qualification. No historical source, package hook, dependency installer, or repository test was executed or imported. Coordinator handoff after M1/M2 provider approval remains required before product implementation or historical execution.

## Result and scope

Docker Desktop 27.4.0 Linux arm64 can enforce an explicit seccomp profile, nonroot identity, capability removal, no-new-privileges, private namespaces, read-only root, bounded writable tmpfs, disabled external networking, cgroup limits, and externally controlled timeout/output cleanup on this machine. Eleven disposable containers ran only the trusted probe payloads archived here. Every container was removed, including failed probes, OOM, timeout, and output overflow. The final label-filtered container inventory was empty.

The evidence supports the initial **no-service** Click execution track. It does not establish Harbor phase networking, Redis/service isolation, arbitrary network allowlists, historical test health, source extraction safety, dependency reproducibility, or the required three fresh reference/three interruption-reset executions. No user setting or Docker daemon configuration was changed. Existing images, containers, and volumes were neither pruned nor modified; the pinned Python image remains available for later reuse.

## Source and interpreter choice

Read the B manifests and CI as inert Git blobs:

```sh
git --git-dir=.feature-rl/research/M1/git/click.git show 19fd4d6e18bc9fce451f92f422696b11169faa57:pyproject.toml
git --git-dir=.feature-rl/research/M1/git/click.git show 19fd4d6e18bc9fce451f92f422696b11169faa57:.github/workflows/tests.yaml
```

Both exited 0. B requires Python >=3.10 and explicitly tests Python 3.12. Its Linux runtime has no required third-party dependency; `colorama` is Windows-conditional. The baseline tests group contains pytest; build metadata specifies `flit_core>=3.11,<4`. CI uses a locked uv/tox wheel workflow. The existing pytest `not stress` default is part of B, not a repair introduced here. Dependency wheel/version/hash acquisition and the faithful setup decision remain future M3 work; no dependency wheels were downloaded in this investigation.

Selected [Docker Official Python](https://hub.docker.com/_/python), `3.12-slim-bookworm`, resolving its OCI index through the public authenticated registry API without persisting the registry bearer token:

- Index: `sha256:d5ae74acb8026b32a2f6deea45003c5bd4e2880700c19c44bda54670ad3eff90`.
- **Linux arm64/v8 execution image:** `python@sha256:eb5be8e5b4d0a159c237946bbdd06356dda5d19c30fc4f7843e8046d3a590333`.
- Observed interpreter: **CPython 3.12.14 aarch64**.
- Sum of compressed image layers: **45,094,999 bytes**; Docker reports image size 45,102,452 bytes.

The exact [index](image-index.json), [platform manifest](image-arm64-manifest.json), and [pin](image-pin.json) are retained. SHA-256 of each manifest's raw bytes matches its stated registry digest. This is a contemporary patched 3.12 image, not a claim to reproduce the historical CI patch release or original operating-system image. Compatibility still needs the baseline run.

The immutable pull was:

```sh
docker --config .feature-rl/research/M3/docker-client \
  --host unix:///Users/mukeshreddypochamreddy/.docker/run/docker.sock \
  pull --platform linux/arm64 \
  python@sha256:eb5be8e5b4d0a159c237946bbdd06356dda5d19c30fc4f7843e8046d3a590333
```

Exit 0 in 3.680 seconds; Docker returned that exact digest and “Downloaded newer image”. [Complete pull output](pull.log). The private client-config directory is empty and used only to avoid the unrelated desktop credential helper; it does not change the user's Docker configuration.

An earlier `docker buildx imagetools inspect python:3.12-slim-bookworm` stalled in `docker-credential-desktop get`. Its scoped Docker/buildx/helper processes (84857/84862/84863) were terminated after the public registry route succeeded; the original command exited 137. No credential values were printed or persisted. Registry manifest retrieval succeeded independently, so no permission or authentication change was needed.

## Explicit seccomp

The daemon still reports `name=seccomp,profile=unconfined`. Workers do not inherit that policy. The explicit profile originates from [Moby profiles commit 245180c51918481c0525424b3ee025d2b435d46c](https://github.com/moby/profiles/blob/245180c51918481c0525424b3ee025d2b435d46c/seccomp/default.json), retrieved using:

```sh
curl -fsSL https://api.github.com/repos/moby/profiles/commits/main -o .feature-rl/research/M3/moby-profile-commit.json
curl -fsSL https://raw.githubusercontent.com/moby/profiles/245180c51918481c0525424b3ee025d2b435d46c/seccomp/default.json -o .feature-rl/research/M3/seccomp-upstream.json
```

Both exited 0. The [upstream profile](seccomp-upstream.json) SHA-256 is `785b2429264afba4d594320337cb17f144f3c7d51585f9805eef72e28f4f9334`. The [applied profile](seccomp.json) SHA-256 is **`005f6ae1a0f3f9d1a0c044f83289e2ea54180c97a105b2539422588eac2fde44`**. It preserves the upstream default-deny allowlist and tightens it by removing `ptrace`, `process_vm_readv`, and `process_vm_writev`, and allowing `socket` only for AF_UNIX, AF_INET, and AF_INET6. This excludes kernel and cross-VM socket families. Conditional capability-based allow entries never activate because all capabilities are dropped. Docker documents explicit [seccomp profile application](https://docs.docker.com/engine/security/seccomp/).

## Exact execution configuration

All probes used this create configuration, with a unique `feature-rl-m3-probe-<12 hex>` name per attempt. Exact expanded argv, names, payloads, timestamps, exit statuses, effective configurations, state observations, cleanup commands, and stdout/stderr are in [command-receipts.json](command-receipts.json). Complete original `docker inspect` output, including repeated inline seccomp bytes, is retained in the ignored `.feature-rl/research/M3/*receipts.json` files. The checked-in receipts replace only those redundant inspect responses with a pointer; the applied profile and effective fields are preserved here.

```sh
docker --config '/Users/mukeshreddypochamreddy/Desktop/project x/.feature-rl/research/M3/docker-client' \
  --host unix:///Users/mukeshreddypochamreddy/.docker/run/docker.sock \
  create --name "$probe_name" --label feature-rl.investigation=M3 -i \
  --platform linux/arm64 --pull never --network none --ipc private --cgroupns private \
  --read-only --user 65534:65534 --cap-drop ALL \
  --security-opt no-new-privileges=true \
  --security-opt 'seccomp=/Users/mukeshreddypochamreddy/Desktop/project x/.feature-rl/research/M3/seccomp.json' \
  --pids-limit 32 --cpus 0.5 --memory 128m --memory-swap 128m --shm-size 1m \
  --ulimit nofile=128:128 --ulimit core=0:0 --ulimit fsize=33554432:33554432 \
  --log-driver none --restart no \
  --tmpfs /workspace:rw,noexec,nosuid,nodev,size=16m,uid=65534,gid=65534,mode=0700 \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=8m,uid=65534,gid=65534,mode=0700 \
  --workdir /workspace --env PYTHONDONTWRITEBYTECODE=1 --env PYTHONHASHSEED=0 \
  --env LANG=C.UTF-8 --env LC_ALL=C.UTF-8 --env TZ=UTC \
  python@sha256:eb5be8e5b4d0a159c237946bbdd06356dda5d19c30fc4f7843e8046d3a590333 \
  python -u -
```

The host driver sends only trusted Python text over stdin using `docker start --attach --interactive NAME`. No bind mount or named volume is present. `PidMode` and `UTSMode` are the Docker default empty strings, which create private namespaces; no `host` or `container:` namespace sharing is requested. The image has no entrypoint, volume declarations, or OnBuild hooks. Inspected configuration has `Privileged=false`, `ReadonlyRootfs=true`, `CapDrop=["ALL"]`, `Binds=null`, `NetworkMode=none`, `IpcMode=private`, `CgroupnsMode=private`, and `LogConfig.Type=none`.

The host driver uses a monotonic deadline and selector-based combined stdout/stderr byte cap. Defaults are 12 seconds and 65,536 retained bytes. The timeout probe uses 2 seconds; overflow uses 16,384 bytes. It calls `docker kill NAME` on a limit, inspects state, calls `docker rm --force NAME` in cleanup, and verifies `docker inspect NAME` fails. Host Docker control calls have a 15-second deadline. If the attach CLI does not exit within five seconds after kill, the driver terminates that CLI; this happened for output overflow. The source of each executed trusted driver is preserved: [first batch](attempt1-probe.py), [second boundary attempt](attempt2-probe.py), [successful boundary](probe.py), [supplement](supplement-probe.py).

The writable source workspace and scratch space are memory-backed and individually bounded; `/dev/shm` is separately bounded at 1 MiB. The Docker-provided `/dev` tmpfs is root-owned, and aggregate memory is still constrained by the 128 MiB cgroup. Logs are not persisted by the daemon, so unbounded container output cannot consume daemon log disk. The controller retains at most the declared output cap plus one transient read chunk. For production, source/archive input caps and allowed-artifact extraction caps must also be implemented; this probe does not supply those APIs. Docker documents the [memory/swap and CPU controls](https://docs.docker.com/engine/containers/resource_constraints/).

## Observed results

| Probe | Observed status/output | Interpretation |
| --- | --- | --- |
| Effective identity/filter | `python 3.12.14 aarch64 uid 65534 gid 65534 pid 1`; all five `Cap*` masks zero; `NoNewPrivs: 1`; `Seccomp: 2`; `Seccomp_filters: 1` | Explicit filter and reduced privilege are active. |
| Process/mount isolation | `/proc` initially contains only PID 1; separate namespace inode records retained; root mount `ro`; no Docker socket, `/Users`, `.git`, or `/run/secrets`; `/root/.codex` cannot be traversed (`EACCES`) | No host workspace/home/credential mount was supplied. |
| Filesystem escape | Writes to `/escape`, `/etc/hosts`, `/proc/sys/kernel/hostname`, `/sys/fs/cgroup/pids.max`, traversal `/workspace/../../escape`, and symlink targeting `/etc/hosts` return errno 30 (`EROFS`) | Read-only mounts deny writes through direct and indirect paths. |
| Privilege/namespace | `setuid(0)`, `unshare(CLONE_NEWUSER)`, `ptrace(PTRACE_TRACEME)`, and mount attempt return errno 1 (`EPERM`) | Actual denial, beyond flags in configuration. |
| Executable files | Executing a newly written script from `/workspace` or `/tmp` returns errno 13 (`EACCES`) | tmpfs `noexec` is active. Python can still interpret source, as intended. |
| External network | IPv4 TCP to `1.1.1.1:443`, IPv6 TCP to `2606:4700:4700::1111:443`, and UDP DNS to `1.1.1.1:53` return errno 101 (`ENETUNREACH`) | Direct external address families have no route. |
| DNS and alternate families | `getaddrinfo('example.com',443)` child exits 1 with `gaierror -3`; IPv4 route table empty; AF_ALG 38 and AF_VSOCK 40 sockets return `EPERM`; private loopback port 2375 refuses connection | DNS and tested alternate socket paths fail. This is a no-network track, not an endpoint allowlist. |
| PID cap | `pids.max=32`; the 32nd attempted child fails with errno 11 after 31 children; `pids.events max 1`; probe exits 0 after killing and reaping its children | Fork exhaustion is cgroup-enforced. |
| Disk caps | `/workspace` fails with errno 28 at 16,777,216 bytes; `/tmp` at 8,388,608; `/dev/shm` at 1,048,576 | The declared writable-filesystem bounds are enforced. |
| CPU | `cpu.max=50000 100000`; 2.027 wall seconds consume 1.014 CPU seconds; `nr_throttled=20`, `throttled_usec=988931` | Actual half-CPU throttling occurred. |
| Memory | `memory.max=134217728`; `memory.swap.max=0`; allocating 256 MiB ends with container exit 137 and `OOMKilled=true` | Hard memory/no-swap limit observed. |
| Nontermination | Parent and child loop forever after writing `/workspace/interrupted`; deadline stops container in 2.066 seconds; exit 137; remove exit 0 and subsequent inspect fails | External timeout removes the entire container process scope. |
| Output overflow | 16,384 bytes retained; reason `output_limit`; container killed (state exit 137); attach CLI eventually killed (exit -9); total 5.423 seconds; remove exit 0; inspect fails | Retained bytes and cleanup are bounded. CLI output draining deserves explicit production handling. |
| Fresh workspace | New container prints `workspace_entries []`, `tmp_entries []`, `proc_pids ['1']`; exit 0 | One demonstrated fresh state after interruption; not the three-repeat milestone. |

Two boundary attempts failed because the trusted observer treated an expected restriction as fatal: first, `Path.exists('/root/.codex')` raised `EACCES`; second, `socket.if_nameindex()` tried a denied netlink socket. The observation code was corrected to report the permission error and read `/proc/net/dev` without relaxing the policy. Both failed attempts exited 1 and were removed. The final boundary and supplementary probes exited 0. `/proc/net/dev` includes loopback and kernel tunnel devices, so this record does not claim that loopback is literally the only interface; direct route and socket tests establish the stated no-egress result.

## Handoff and remaining work

- Reuse the digest and explicitly applied profile as qualified starting inputs; fail closed on missing Docker or policy mismatch. Never fall back to executing repository code on the host.
- Before historical execution, complete M3's actual typed recipe/lifecycle/controller APIs under the coordinator's handoff. Keep private oracle data out of all workers and H out of reusable dependency layers.
- Resolve the baseline's locked test/build wheels as inert bytes, record wheel hashes, and perform install/build hooks only in hardened disposable workers. Check actual imported Click paths for both B and H. Decide and document a single neutral setup policy for baseline/reference/candidates.
- Add source-byte, path/link, archive, disk, and extraction validation, interruption-safe owned-container tracking, and typed cleanup/error reporting. The probe driver is not the runtime implementation.
- Run the real baseline/reference repeatability and interruption checks only after authorization gates. Feature absence/reference semantics belong to M4/M5. Local service support remains unsupported/unexecuted.

Executed container window: 2026-09-19T07:52:42Z to 07:55:18Z; combined attached container durations approximately 12.701 seconds across 11 attempts. Evidence was prepared against observed repository HEAD `0dc354797016b6f2f9ccbbb03c923876e769d378` in a shared active workspace; this does not identify an M3 implementation revision. No paid infrastructure, model invocation, or package installation was performed. Monetary/token/human-review cost is **unknown**, not encoded as zero.

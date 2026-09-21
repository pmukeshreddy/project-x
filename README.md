# Feature RL

This project constructs immutable feature tasks from retained source evidence,
grades source implementations in a pinned Docker boundary, and checks current
automated qualification before release, agent execution or learning. It includes native
agent runs, GRPO training integration, frozen evaluation and historical audits.
Evaluation compares the base model (`base`) with feature-environment GRPO (`feature_grpo`).

Install the package with Python 3.11 or newer using `python -m pip install .` in
the intended environment. In this workspace, use the existing environment:

```sh
PYTHONPATH=src .venv/bin/python -m feature_rl --help
PYTHONPATH=src .venv/bin/python -m feature_rl config-schema
```

The CLI exposes `construct`, `qualify`, `release`, `run`, `grade`, `audit`, `train`
and `evaluate`, plus source screening, authoring, resolution and publication recovery. Commands use strict JSON inputs
and the actual library services. See [the runbook](docs/runbook.md) for commands,
configuration and recovery, and [the native launch contract](docs/reports/M7-native-launch.md)
for pinned Linux/CUDA setup, training and checkpoint resume.

Environment authoring uses `gpt-6-astra` through the installed Codex CLI and its
existing ChatGPT login. There is one provider, with strict output schemas and
explicit failures; no model downloads, API-key path, or local provider fallback.
Configure `codex` in the workflow/authoring settings; see
[the authoring setup](docs/interfaces-M2.md). This refactor has not generated a real environment.

The environment factory uses compact behavioral specifications and controller-built
hidden checks. Qualification runs baseline, historical gold, three or four plausible
wrong implementations, and one clean-reset gold rerun. Passing tasks can then be
frozen and released. See [qualification](docs/interfaces-M5.md).

`construct-feature` derives each repository's Python version, build backend,
dependency closure, source layout and system package requirements, then freezes
wheel hashes and image digests. Configure the Linux platform, sandbox limits and
publishing registry; per-repository profiles and dependency pins are generated.
Unresolved declarations or an incomplete reproducible closure reject preparation.
Candidate builds resolve their own safe declarations through a trusted controller
registry service. Resolution retains package-index snapshots and an exact
hash-verified wheel closure for offline installation and replay. Shared metadata
and wheel caches can serve other candidates and repositories.
See [runtime construction](docs/interfaces-M3.md).

The same factory also builds Node/TypeScript with npm, Rust binaries with Cargo,
and Go binaries with Go modules. Repository declarations select the toolchain;
dependency acquisition receives only manifests and locks, then builds run without
network access. Exact source, dependency supply, image and retained build products
bind each execution. Optional `.feature-rl/runtime.toml` declares build argv,
entry-point paths, Debian packages and local services with bounded readiness and
fresh container-owned state. See [the toolchain configuration](docs/runbook.md#command-toolchains-and-local-services).
This path currently requires frozen candidate manifests, supports public registry
dependencies, and rejects yarn/pnpm, workspaces, local/git dependencies, library-only
Rust/Go and external services. Python continues to use its existing wheel profile;
Python runtime.toml overrides are explicitly unsupported.

[`construct-feature --github`](docs/runbook.md) captures a merged PR, its discussion
and review metadata, and an optional linked issue. Existing captures can be selected
with `--prepared`; private repositories support token authentication. The reconstructed
contract explicitly selects required files and records their requirement/evidence links.
Projection preserves selected source, stubs, assets, configuration, build and dependency
changes. Unrelated files retain their baseline versions. Candidate dependencies can
use compatible registry packages while the task's Python, platform and system
packages stay fixed. Unavailable dependency resolution leaves the result unmeasured;
unsafe declarations are candidate rejections. Candidate resolutions are build
artifacts; the task and environment recipe retain their original identities.
Experiment splits and authoring budgets remain explicit.
GPU training,
native GPU inference, new task generation and experimental results remain
deferred and unverified. No real task has been fully qualified and released.
SSH human approval is not required for new task admission.

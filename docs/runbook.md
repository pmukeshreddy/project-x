# Controller commands

Run from the repository with `PYTHONPATH=src .venv/bin/python -m feature_rl` or,
after a normal installation, `python -m feature_rl`. `config-schema` prints the
complete strict JSON schema without opening an artifact store, Registry, daemon,
model or trust file. A normal installation also exposes `feature-rl` through the
M0 console entry point. No installation is needed for the repository commands here.

For a selected GitHub feature, prepare the existing workflow's source
inputs without manually cloning a repository or assembling its HTTP cache:

```sh
PYTHONPATH=src .venv/bin/python -m feature_rl prepare-github --request github-source.json --output /srv/feature-rl/captures/feature-001
```

This command needs no controller configuration, Docker or model. An example
`github-source.json` follows; replace the repository, numbers, lineage identifiers,
integration method and cutoff with the actual selected feature's values:

```json
{
  "repository_url": "https://github.com/OWNER/REPO",
  "pull_request": 123,
  "repository_family": "chosen-family",
  "request_lineage": ["chosen-feature"],
  "partition_source_ids": ["chosen-pr"],
  "integration": "squash",
  "admissible_cutoff": "2026-01-01T00:00:00Z",
  "license_path": "LICENSE"
}
```

Add an optional `issue` number when the PR has a linked issue; include that issue in
the partition closure. PR-only requests retain PR discussion, reviews, inline review
comments, commit messages and changed-file metadata.

The command writes raw responses, `sources.tsv`, a bare `repository.git`, and
`prepared.json`, and prints the last document. Construction consumes it directly:

```sh
PYTHONPATH=src .venv/bin/python -m feature_rl --config controller.json construct-feature --request feature.json --prepared /srv/feature-rl/captures/feature-001/prepared.json
```

Alternatively, capture and construct in one command:

```sh
PYTHONPATH=src .venv/bin/python -m feature_rl --config controller.json construct-feature --request feature.json --github github-source.json --capture /srv/feature-rl/captures/feature-001
```

With either option, omit `intake` from `feature.json` and the capture's source
paths/hash/limits from `controller.json`'s `workflow`; the CLI fills and validates
them before composing the existing services. Supplied conflicting values reject.
Keep authoring, sandbox, episode-budget and partition settings
explicit; repository runtime/dependency inputs are derived automatically, and the partition manifest must cover the selected `partition_source_ids`.
The original complete cached-input command remains supported.

For a private repository, set `token_env` to `GH_TOKEN` or `GITHUB_TOKEN` in
`github-source.json` and supply that environment variable with a token authorized
for the selected repository's contents, pull requests and issues. The token is
used for GitHub API and Git HTTPS authentication. It is not stored in capture
JSON, URLs, Git configuration files or command arguments. API credentials are
restricted to the GitHub API origin; Git credentials are scoped to GitHub HTTPS,
with redirects disabled. Offline capture reuse needs no token. See
[GitHub authentication](https://docs.github.com/en/rest/authentication/authenticating-to-the-rest-api)
for token permissions.

Repeating the same preparation request/output verifies and reuses the completed
capture offline. Changed requests, damaged captures and incomplete output
directories reject; a new capture uses a new directory. Fresh captures are labeled
`reconstructed_specification`. PR-only and PR-with-linked-issue requests are supported.
Raw response pages and confirmation snapshots are retained. Missing history,
unavailable license identity, API limits and configured byte/page/time/depth limits
reject. Git transfer has time/depth bounds, but no aggregate pack-file disk quota.

The authored contract selects `feature_files` with requirement IDs, evidence and a
reason each file is necessary. Qualification projects those exact B/H changes,
including stubs, templates, data, configuration, build files, native code and dependency
manifests. Unrelated changes retain B's bytes. Path categories describe history and
do not decide inclusion. Traversal, links and controller-private paths remain forbidden.

Create a controller-private configuration with absolute `store_root` and
`registry_root` paths, `version: "m6-cli-v1"`, and the exact Factory `revision`.
`builder_revision` defaults to that revision; set it to the actual reviewed
construction implementation when consuming roots built under that version.
Revisions describe the actual checked-out service implementations, not user-chosen
approval labels. Existing immutable jobs and roots retain their original versions.

The following source and complete-artifact construction commands require no
Docker or model. `candidate-request.json` is the actual M0 `ConstructRequest`
(`{"candidate": <complete ArtifactRef>}`); `build-inputs.json` is actual M6
`BuildInputs` with resolvable M0/M3/M4 references.

```sh
PYTHONPATH=src .venv/bin/python -m feature_rl --config controller.json screen-source --request candidate-request.json
PYTHONPATH=src .venv/bin/python -m feature_rl --config controller.json construct --request candidate-request.json --inputs build-inputs.json
```

Omitting `--inputs` records a blocked construction prerequisite for an eligible
source. It does not call a generator or create a substitute task. Rejected or
unresolved actual source disposition returns its selected retained source result.
Source and construction replay reuse the original job/output and original costs.

Runtime commands additionally require `runtime` with absolute `state_root`,
`socket_path`, exact M3 `revision`, exact `grading_revision`, an explicit `policy`
(`SandboxPolicy`), and optional bounded `grade_wall_seconds`. New policies default
to `docker-python-v3` and require `platform`. Omit `image` and `profile` for automatic
repository preparation. `workflow.dependency_pins` is removed. Construction reads
repository metadata, requirements/locks, layout and system-package declarations,
selects a compatible official Python image, and resolves a target-platform wheel
closure in a source-free builder. It retains wheel hashes, package versions,
resolution inputs and immutable image digests. Dynamic or contradictory declarations
which cannot be resolved reproducibly fail closed.

At candidate build time, the trusted controller resolves the submitted safe
requirements, extras and transitive dependencies from the trusted package registry.
The resolver retains package-index snapshots and the exact hash-verified wheels
selected for that build. Historical declarations do not seed a finite task package
roster or constrain a candidate's solution. No manual catalog or per-repository
package supply is configured.

Keep `runtime.state_root` and `store_root` persistent to reuse index snapshots and
hash-verified wheel artifacts across candidates and repositories. Each candidate
resolution freezes its exact selected closure and resolution evidence for replay
without a fresh lookup. Preparation retains the baseline dependencies needed by
the runtime image. The public runtime manifest describes that fixed runtime;
candidate resolutions remain separate build artifacts.

New construction requires `runtime.image_repository` (for example,
`registry.example.com/team/runtimes`), registry push access through the dedicated
Docker client config, and Buildx timestamp-rewrite support. Preparation downloads
baseline dependencies and system packages; the trusted controller acquires candidate
dependencies when resolution needs them. Repository hooks run in the qualified
offline sandbox. `runtime.image_seconds` bounds each image operation and registry resolution
(default 600 seconds).

The original task and environment recipe remain fixed for every candidate. Candidate
builds use the controller's frozen resolution to install exact wheels without network
access in separate candidate workspaces. The task's Python interpreter, platform,
system packages and sandbox limits stay fixed. Unavailable compatible resolution
produces an unavailable, unmeasured result; unsafe declarations produce a candidate
rejection. Workers may omit `image_repository` and load the exact policy and image
from the task recipe, then consume the retained candidate closure. Sandbox platform
and limits must still match their configuration.
See [M3 construction](interfaces-M3.md) for retained inputs and package constraints.

Qualification configuration is `qualification` with the actual M5 `revision`
and optional `QualificationPolicy`. Passing all automated gates produces a
successful QualificationReport directly; no SSH enrollment, signature or separate
human acceptance step is required. Missing history, unresolved control diagnoses or execution
evidence still blocks admission. Authenticated audits have their own human-trust
configuration, outside task admission.

```sh
PYTHONPATH=src .venv/bin/python -m feature_rl --config controller.json qualify --request built-task-request.json
PYTHONPATH=src .venv/bin/python -m feature_rl --config controller.json release --request built-task-request.json --accepted-report qualification-report-ref.json
PYTHONPATH=src .venv/bin/python -m feature_rl --config controller.json resolve --request released-task-request.json
```

Task request files are the actual M0 request shape
`{"task_version": <complete TaskBundle ArtifactRef>}`. Ref files contain a full
M0 `ArtifactRef`, including kind, encoding and visibility. Qualify optionally takes
`--policy`; it cannot replace selected Factory history. For Factory-generated
controls, omitted diagnosis entries are produced automatically from their selected
authoring archives and actual qualification grades. Results are frozen in the
qualification summary and rechecked at release; unsupported or inconclusive
controls still block qualification. Pass the successful report
from `qualify` directly to `release --accepted-report`. Release can also consume
the exact QUALIFIED predecessor and its existing Q without `--accepted-report`.
`resolve` performs current admission and prints the exact TaskBundle. It executes
no candidate, grade, transition or model; runtime composition still establishes
the actual trusted M3 service instance required by M5.

Operations print ordinary JSON `OperationResult` on stdout: exit 0 for success,
1 for a selected non-success result, and 2 for a command/composition failure.
`resolve` prints its TaskBundle only after current admission; failures print a
typed non-success operation. Argument syntax errors use normal argparse diagnostics.
Unknown fields, duplicate keys, nonfinite values, oversized/nonregular JSON and
symlink input files reject. No executable config or object loader is accepted.

Use a private umask and retain both streams for real operations:

```sh
umask 077
PYTHONPATH=src .venv/bin/python -m feature_rl --config controller.json construct --request candidate-request.json --inputs build-inputs.json > operation.json 2> recovery.jsonl
```

If publication fails, stderr retains the exact concrete private pending payload,
claim, costs and timestamps as ordinary JSON, including base64 for bytes. It is an
output receipt, not an executable object deserializer. The actual Factory/M5/M6
recovery methods validate retained capabilities; an unknown pre-freeze attempt
requires reconciliation and must not be dispatched again. The Registry remains
the authoritative attempt/cost ledger, and JSONL is its verified projection.

Authoring commands require `authoring: AuthoringSettings` in the same configuration:
Codex settings, exact M2/M4 revisions, evidence scope and a frozen
candidate/batch budget. `authoring-call.json` is an actual `AuthoringCall`, including
the complete M0/M3 resolver inputs and the selected M2/M4 finalization inputs.
Authoring uses Astra (`gpt-6-astra`) through `codex exec`. Sign into the installed
Codex CLI with ChatGPT (`codex login`); the same existing authentication is reused.
Set `codex` to `{"executable":"codex","model":"gpt-6-astra","reasoning_effort":"high"}`
in `authoring` or `workflow`. Optional `codex_home` selects an existing Codex home;
otherwise the current `CODEX_HOME`/default home is used. Do not put credentials in
pipeline configuration. Old `backend`, model manifests, calibration evidence, CUDA/file-size fields,
and generation sampling seeds are rejected. Episode/scenario seed policies remain unchanged.
See [the M2 interface](interfaces-M2.md) for limits, validation, and recovery.

Use the supported order contract → scenario → controls/alternative → bounded
checker fragments → controller assembly with those controls → construction.
Each repeated semantic lane consumes the
shared stage/candidate repair allowance; new request or control IDs do not create
another allowance. The narrow, authenticated transport correction in
[the M6 interface](interfaces-M6.md) retains all original attempts and costs while
separating proven controller transport failures from semantic repairs. Complete
history is limited to the frozen controller scope.

```sh
PYTHONPATH=src .venv/bin/python -m feature_rl --config controller.json author --request candidate-request.json --call authoring-call.json
PYTHONPATH=src .venv/bin/python -m feature_rl --config controller.json import-authoring --request candidate-request.json --call retained-call.json --journals retained-journals.json
```

`import-authoring` reads an ordered array of actual rejected M2 journal ArtifactRefs;
it never invokes a model. It preserves already-incurred costs even if current
source admission is unresolved, and leaves external history incomplete. The real
Click import is retained in `docs/evidence/M6/authoring/click-import.json`: source
scope remains provisional, contract repairs are exhausted at 2/2, and known
candidate repairs are 3/4. **No new task-generation calls are authorized in this
integration phase.** These authoring commands are implemented launch interfaces,
not instructions to rerun Click. Null monetary caps explicitly declare unpriced
compute; USD remains unknown. Finite caps reject without an actual meter.

Ordinary grading requires the runtime settings but no human release gate. A
`GradeRequest` contains `task_version`, the exact source `submission` ArtifactRef,
and `case_seed`. The Factory selects its grade job before calling actual M4,
retains the original receipt and reconciles its costs. Same inputs/configuration
and invocation reuse the result; a new invocation requests another cost-bearing
grade. M4 remains the sole reward producer.

```sh
PYTHONPATH=src .venv/bin/python -m feature_rl --config controller.json grade --request grade-request.json --invocation grade-001
```

Run, train and evaluate additionally require `native` with the actual M7
`revision` and `settings: NativeSettings`. Run/evaluate also require
`native.bootstrap: TrainingConfig`, frozen into the inert NativeSessionFactory.
Training uses the `TrainingConfig` in its request. Evaluation additionally requires
`evaluation: {"revision": "<actual M8 revision>"}`. All three require configured
M3/M4 and current M5/M6 admission; source-only and provisional TEST tasks cannot
stand in for released tasks. M8 authenticates the frozen feature-training source
roster and its selected construction receipts.

```sh
PYTHONPATH=src .venv/bin/python -m feature_rl --config native-controller.json run --request run-request.json --invocation run-001
PYTHONPATH=src .venv/bin/python -m feature_rl --config native-controller.json train --request train-request.json --invocation grpo-001
PYTHONPATH=src .venv/bin/python -m feature_rl --config native-controller.json train --request train-request.json --invocation recovery-001 --resume checkpoint-ref.json
PYTHONPATH=src .venv/bin/python -m feature_rl --config native-controller.json evaluate --request evaluate-request.json
```

`RunRequest` contains `task_version`, `policy`, `limits`, and optional `case_seed`;
omission passes `None` through to the ordinary policy seed default. `TrainRequest`
and `EvaluateRequest` contain `{"config": <actual TrainingConfig/EvaluationConfig>}`.
Training accepts only `algorithm="grpo"` and starts updates from useful groups without a separate difficulty gate.
Evaluation compares `base` with `feature_grpo`. It requires a
preregistration, held-out roster, selected checkpoint and feature-construction
evidence; CLI flags cannot replace them.
The run result's `RolloutRecord.run_id` is the selected child Registry run job ID;
the native parent separately retains startup/activation/shutdown and child costs.

Native launch is deferred and unverified. The executable Linux setup, pinned
SkyRL/Harbor versions, model/reference/tokenizer manifest imports, exact probe
inputs, GRPO launch and interrupted-update recovery are specified in
[the M7 native launch contract](reports/M7-native-launch.md). Use the qualified
Linux Python 3.12/CUDA environment's interpreter with the same `-m feature_rl`
commands. This Mac's CPU tests and arm64 Docker worker do not establish GPU fit or
the future protected worker socket forwarding. No download or installation is
performed by composition. Native constructors are inert; actual initialization
occurs only after the service selects its Registry claim and unknown-cost intent.
Every one-shot native command calls the service's cleanup-only `close()` in
`finally`; original and cleanup failures are both retained on stderr.

Audit requires `audit` with the actual M8 `revision`, frozen `selection_manifest`,
selection-ID-to-attestation `attestations` mapping, and external `human` enrollment
path/digest. It needs no Docker or native configuration. Patch IDs must exactly
match the frozen selection. `--source-only` passes the actual empty rollout tuple
for a source-only frozen selection; it does not create a dummy RunRequest or human
approval. Historical source/patch reads do not grant current task admission.

```sh
PYTHONPATH=src .venv/bin/python -m feature_rl --config audit-controller.json audit --request audit-request.json
PYTHONPATH=src .venv/bin/python -m feature_rl --config audit-controller.json audit --source-only
PYTHONPATH=src .venv/bin/python -m feature_rl --config controller.json recover --service grade --claim claim.json
PYTHONPATH=src .venv/bin/python -m feature_rl --config controller.json retry-publication --service grade --receipt recovery.json
```

`recover` accepts an exact Registry `Claim` and selects actual composition through
`--service factory|grade|qualification|lifecycle|run|evaluate|audit`. Use the original
service versions/settings. It cannot restart unknown grade/model work. Actual M5
recovery may continue undispatched qualification gates while reusing completed
subjobs and refusing unknown ones. Training recovery uses `train`: repeat identical
inputs/invocation for ordinary resume; an unknown update requires the exact last
confirmed checkpoint, retained original journal, same frozen settings/config/data,
and an explicit new invocation. It consumes the original budget and skips unknown
assigned data; it does not create a new allowance.

`retry-publication` accepts exactly one emitted `FactoryPublicationFailed` JSON
record for its closed set of actual Factory payload kinds, verifies its digest and
delegates to concrete publication validation. An early grade publication outage
retains its actual M4 result in this capability. Other concrete M2/M3/M4/M5 pending
capabilities remain available through their documented library retry APIs or
selected durable recovery; the command does not deserialize arbitrary classes.

## Command toolchains and local services

`construct-feature` selects a repository's root toolchain and uses the existing
Docker boundary for source preparation, builds, discovery, grading and reset.
Python packaging retains the wheel pipeline, including Python projects with
native extensions. Other supported roots are:

| Root | Required declarations | Built entry points |
| --- | --- | --- |
| Node/TypeScript | `package.json`, npm `package-lock.json` v2/v3, numeric Node selector in `.node-version`, `.nvmrc` or `engines.node` | `main`, `bin`, or `index.js`; optional `npm run build` |
| Rust | `Cargo.toml`, `Cargo.lock`, numeric `rust-toolchain[.toml]` or `package.rust-version` | Declared binaries, `src/main.rs`, or `src/bin/*.rs` |
| Go | `go.mod` with `go` version, optional `toolchain` version, `go.sum` when dependencies exist | Packages declaring `package main`, compiled into `bin/` |

Numeric selectors resolve immediately to an official Debian Bookworm image
digest. npm's `packageManager`, when present, must match the installed exact npm
version. npm packages require registry.npmjs.org URLs and SHA512 integrity;
Cargo packages require crates.io checksums; Go uses the public module proxy and
checksum database. Only declarations enter networked acquisition. Repository
build hooks run later inside a qualified, unprivileged, networkless container.
The complete captured dependency supply and each build product are hash-bound
artifacts. Builds do not reuse a previous candidate's output.

For these three command toolchains, an optional `.feature-rl/runtime.toml` can
select the primary language in a polyglot repository and replace the build argv
sequence. Commands run in `/workspace/site`, copied from the saved source, and
entry points name paths relative to that directory. Keep dependency installation
in a replacement build sequence when the project requires it:

```toml
language = "node"
build = [
  ["npm", "ci", "--offline", "--no-audit", "--no-fund"],
  ["npm", "run", "build", "--offline"],
]
entry_points = ["dist/index.js"]
system_packages = ["redis-server", "redis-tools"]

[[services]]
name = "cache"
start = ["redis-server", "--bind", "127.0.0.1", "--port", "6379", "--save", ""]
readiness = ["redis-cli", "-h", "127.0.0.1", "ping"]
ready_stdout = "PONG\n"
startup_seconds = 10.0
```

Declared Debian packages are installed during source-free image construction;
their observed versions and immutable image are retained. Up to four local
services may run as the sandbox user. Each starts in a fresh
`/workspace/services/<name>` directory before a build or execution. Readiness
requires a live foreground process, exit zero and exact declared stdout within
the bounded startup deadline. Services share only that container's loopback
network; no host ports, external network, sidecars or persistent volumes are
enabled. Verified container removal tears down all service processes and state;
the next execution starts fresh, including after workspace reset.

This implementation rejects yarn/pnpm, package-manager workspaces, Cargo git/local
dependencies, Go workspace/replace declarations, library-only Rust/Go observers,
Compose/CI service declarations without an implemented interpretation, and
external services. Candidate manifests and lockfiles remain frozen to the
prepared supply. Explicit Python runtime.toml overrides are rejected; Python
continues to use the wheel profile. Unsupported declarations fail preparation or
candidate admission and never select a substitute runtime.

The focused real Docker tests use labeled test-only sources and do not constitute
generated or qualified feature tasks:

```sh
FEATURE_RL_COMMAND_RUNTIME_DOCKER=1 uv run pytest tests/test_command_toolchains_docker.py -v
```

They require the configured local registry (default
`localhost:5000/feature-rl-toolchain-tests`), Docker's Linux arm64 runtime, and
Buildx at `~/.docker/cli-plugins`. Set `FEATURE_RL_TEST_IMAGE_REPOSITORY` to change
the test registry repository. They exercise npm, a real TypeScript compiler,
checksum-locked Rust/Go dependencies, discovery, fresh execution, stale-build
rejection, saved-source reset, service readiness and cleanup.

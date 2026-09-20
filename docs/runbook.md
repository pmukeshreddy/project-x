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
  "issue": 100,
  "repository_family": "chosen-family",
  "request_lineage": ["chosen-feature"],
  "partition_source_ids": ["chosen-pr", "chosen-issue"],
  "integration": "squash",
  "admissible_cutoff": "2026-01-01T00:00:00Z",
  "license_path": "LICENSE"
}
```

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
Keep authoring, dependency, runtime, episode-budget and partition settings
explicit; the partition manifest must cover the selected `partition_source_ids`.
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
directories reject; a new capture uses a new directory. Fresh issue downloads are
always labeled `reconstructed_specification`, never historical snapshots.
Merged PRs with an explicit same-repository issue are supported. Missing history,
unavailable license identity, API limits and configured byte/page/time/depth limits
reject; there is no automatic deepening, dependency discovery or feature selection.
Git transfer has time/depth bounds, but no aggregate pack-file disk quota.

Mixed/unknown path labels do not require manual source approval. Intake retains
those labels, and qualification records an exact projection of permitted Python
source changes onto B. Documentation, tests, build/dependency files and changes
outside the source policy retain their B versions. Qualification runs its real
feature, compatibility and verifier-control checks against that projection. If a
feature needs an excluded configuration or asset change, the reference must fail
those checks; source screening alone never admits it to training.

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
to `docker-python-v2` and must supply `image` (an immutable base digest) and
`profile` (the repository's pinned `RuntimeProfile`) and `platform`. Legacy Click
policies and recipes without a prebuilt image must be reconstructed. Composition invokes the actual M3 trusted
boundary qualification. A caller flag, copied receipt or altered policy cannot
stand in for that qualification. Candidate grades keep M3's fresh build/isolation
rules and supported pinned image/dependency cache semantics.

New environment construction also requires `runtime.image_repository`, an
explicit registry/repository such as `registry.example.com/team/runtimes`, with
push access through the runtime's dedicated Docker client config. The builder
needs Buildx/BuildKit timestamp-rewrite support, a digest-pinned policy base,
and the profile's hash-pinned dependency wheels. Construction rejects missing
dependencies and failed offline builds before publishing a recipe. Images are
cached by their canonical build-context hash and recipes retain the exact
published digest. `runtime.image_seconds` bounds each image operation (default
600 seconds), separately from task execution limits.

Workers can omit `image_repository` and set `runtime.qualification_image` to the
prepared recipe's `image_digest`. They then pull only that final image for
qualification and execution; no per-server repository dependency installation
or image build is needed. Keep the same `SandboxPolicy`/profile used to construct
the recipe. The runtime manifest records Linux architecture, Docker and CPU-only
host requirements for scheduling. See [M3 construction](interfaces-M3.md) for
cache identity and supported package constraints.

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
actual M2 backend settings, exact M2/M4 revisions, evidence scope and a frozen
candidate/batch budget. `authoring-call.json` is an actual `AuthoringCall`, including
the complete M0/M3 resolver inputs and the selected M2/M4 finalization inputs.
Authoring now uses local Transformers/PyTorch. Configure the model ID, immutable
revision, model/dependency manifest SHA-256 pins, explicit `device` (`cpu` or
`cuda:0`, etc.) and `dtype`. Supply materialized unquantized safetensors and install
the chosen hash-pinned Torch/Transformers wheel closure into the controller's
Python environment first. The old MLX model/configuration is incompatible.
See [the M2 interface](interfaces-M2.md) for the manifest formats, resource limits,
and offline worker contract. No dependencies or models are downloaded by authoring.

Use the supported order contract → scenario → controls/alternative → final checker
with those controls → construction. Each repeated semantic lane consumes the
shared stage/candidate repair allowance; new request or control IDs do not create
another allowance. Complete history is limited to the frozen controller scope.

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

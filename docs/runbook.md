# Controller commands

Run from the repository with `PYTHONPATH=src .venv/bin/python -m feature_rl` or,
after a normal installation, `python -m feature_rl`. `config-schema` prints the
complete strict JSON schema without opening an artifact store, Registry, daemon,
model or trust file. Console-entry metadata is owned separately by M0.

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
`socket_path`, exact M3 `revision`, exact `grading_revision`, and optional bounded
`SandboxPolicy`/`grade_wall_seconds`. Composition invokes the actual M3 trusted
boundary qualification. A caller flag, copied receipt or altered policy cannot
stand in for that qualification. Candidate grades keep M3's fresh build/isolation
rules and supported pinned image/dependency cache semantics.

Qualification configuration is `qualification` with the actual M5 `revision`,
optional `QualificationPolicy`, and optional `human` containing external
`enrollment_path` plus `enrollment_sha256`. The enrollment is read-only and subject
to the actual M5 ownership, signature, expiry and revocation rules. The CLI creates
no enrollment, signatures, attestations or approval. Missing human/history/control
gates retain their actual provisional or failure outcomes.

```sh
PYTHONPATH=src .venv/bin/python -m feature_rl --config controller.json qualify --request built-task-request.json
PYTHONPATH=src .venv/bin/python -m feature_rl --config controller.json accept --review-request review-ref.json --attestation attestation-ref.json
PYTHONPATH=src .venv/bin/python -m feature_rl --config controller.json release --request built-task-request.json --accepted-report accepted-q-ref.json
PYTHONPATH=src .venv/bin/python -m feature_rl --config controller.json resolve --request released-task-request.json
```

Task request files are the actual M0 request shape
`{"task_version": <complete TaskBundle ArtifactRef>}`. Ref files contain a full
M0 `ArtifactRef`, including kind, encoding and visibility. Qualify optionally takes
`--policy`; it cannot replace selected Factory history. Release can also consume
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

The command checkpoint does not yet expose agent/train/evaluate/audit dispatch or
authoring calls. Those joins consume the actual corresponding services as they
are completed; no stub command returns success. No task-generation model call is
authorized in the current integration phase.

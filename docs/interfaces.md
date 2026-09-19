# Shared M0 interfaces (per-kind schema versions)

Status: implemented and independently reviewed at `cb53f99`, with coordinator verification of 103 tests. Review closure is in `docs/reviews/M0-round1.md`. Import public contracts from `feature_rl.contracts`; storage from `feature_rl.artifacts`. Python >=3.11 is declared; this checkpoint executes on CPython 3.13.7 with Pydantic 2.13.5 and pytest 9.1.1. Dependencies and transitive versions are locked in `uv.lock`; queried PyPI compatibility metadata is in `docs/evidence/M0/dependencies.json`.

## Exact schema and examples

[Complete JSON Schema catalog](evidence/M0/schemas.json) contains every required property, nested model, enum, bound and nullable type. [All eleven complete JSON examples](evidence/M0/examples.json) are synthetic unit diagnostics; they are not real candidates, measured training, or qualification evidence. The executable builders and round-trip tests are in `tests/test_contracts_examples.py`. Do not use these examples as production success data.

Every artifact requires `kind`, strict integer `schema_version` (`2` for CandidateRecord and SourcePair, `1` for all other kinds), `visibility`, typed `provenance`, and nonempty `costs`, plus these artifact-specific fields:

| Artifact | Additional required fields (nullable values still required) |
| --- | --- |
| CandidateRecord | `provenance_label`, `repository_url`, `repository_family`, `request_lineage`, `partition`, `sources`, `license`, `commits`, `screening` |
| SourcePair | `provenance_label`, `candidate`, `baseline_commit`, `reference_commit`, `baseline`, `reference`, `relationship`, `changed_files`, `admissible_cutoff`, `verification` |
| RequirementContract | `visible_request`, `capability`, `entry_points`, `requirements`, `compatibility_obligations`, `ambiguities`, `allowed_changes`, `public_checks`, `episode_limits`, `provenance_label` |
| ScenarioPlan | `contract`, `mandatory_requirement_ids`, `scenarios`, `seed_policy` |
| EnvironmentRecipe | `image_digest`, `interpreter_version`, `dependencies`, `setup`, `reset`, `services`, `limits`, `neutral_repairs`, `locale`, `timezone`, `environment`, `randomness`, `network_policy`, `baseline` |
| VerifierBundle | `contract`, `scenario_plan`, `cases`, `completion_manifest`, `worker_adapter`, `public_examples`, `controls`, `permissions` |
| TaskBundle | `state`, `partition`, `repository_family`, `request_lineage`, `source_pair`, `baseline`, `solver_view`, `contract`, `environment`, `adapter_version`, `private_oracle`, `reference_solution`, `qualification` |
| QualificationReport | `task`, `disposition`, `baseline_health`, `baseline_absence`, `reference_run`, `controls`, `fresh_runs`, `interrupted_reset_runs`, `human_reviews`, `rejection_reasons`, `repair_attempts`, `policy_version` |
| RolloutRecord | `run_id`, `task`, `policy`, `limits`, `seeds`, `steps`, `submission`, `stopping_reason`, `disposition`, `reward`, `grading_evidence`, `training_eligible` |
| TrainingCheckpoint | `weights`, `optimizer_state`, `reference_checkpoint`, `data_position`, `policy_version`, `configuration`, `consumed_tasks`, `optimizer_steps`, `update_evidence`, `reload_evidence` |
| EvaluationReport | `configuration`, `frozen_task_roster`, `trials`, `paired_metrics`, `audits`, `disposition`, `limitations` |

All models reject unknown fields, nonfinite numbers and coercion of booleans into integer counts. Models are frozen; their sequence fields are tuples in Python and arrays in JSON. Use enum instances, tuples and timezone-aware UTC datetimes in Python constructors. For JSON transport use `Model.model_validate_json(json_bytes)`. `Model.model_dump(mode="json")` returns JSON-compatible data. Never use `model_construct`/`model_copy(update=...)` for validation; the store revalidates instances anyway.

## Storage

```python
from pathlib import Path
from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole, Visibility

store = ArtifactStore(Path(".feature-rl/artifacts"), ActorRole.CONTROLLER)
log_ref = store.put_bytes(b"actual captured stdout\n", "command-log", Visibility.PRIVATE)
assert store.get_bytes(log_ref) == b"actual captured stdout\n"
# artifact_ref = store.put_artifact(validated_candidate_record)
# candidate = store.get_artifact(artifact_ref)
```

Exact methods: `put_artifact(artifact: ArtifactModel) -> ArtifactRef`, `get_artifact(ref: ArtifactRef, *, max_envelope_bytes: int | None = None) -> ArtifactModel`, `put_bytes(data: bytes, kind: str, visibility: Visibility) -> ArtifactRef`, `get_bytes(ref: ArtifactRef, *, max_envelope_bytes: int | None = None, max_payload_bytes: int | None = None) -> bytes`.

`ArtifactRef` requires `sha256` (64 lowercase hex), `kind` (safe identifier), `schema_version` (strict integer 1 or 2, checked against the kind on typed reads and typed operation links), `visibility` and `encoding` (`json` or `bytes`). No path is accepted in a ref. Known typed artifact names cannot be stored as raw bytes. Byte kinds are descriptive safe identifiers such as `source-archive`, `command-log` and `attestation`.

An object is `<sha256>.json` containing exactly `kind`, `schema_version`, `visibility`, `encoding`, `payload`. The digest covers the complete UTF-8 envelope with sorted keys, compact separators, `ensure_ascii=False`, and no NaN/Infinity. Typed payloads are JSON objects; byte payloads are canonical base64. Thus the digest binds metadata and content; equal bytes under different visibility/kind get different identities. Reordering input model keys does not change identity. Reads verify digest, canonical encoding, metadata, schema, and payload/envelope agreement. Duplicate JSON keys are rejected on store reads. No pickle, imports, evaluation, or executable deserialization is used.

Writes use an exclusive temporary file, file fsync, atomic create-if-absent hardlink publication, and directory fsync. Duplicate writes compare exact serialized bytes; corruption is never overwritten. Descriptor-relative paths and `O_NOFOLLOW` reject symlink roots/ancestors/objects and nonregular objects. Root paths must be symlink-free (resolve a trusted platform alias before constructing a store). A crash may leave `.pending-*` files; readers ignore them and retries publish safely. Automatic cleanup is deliberately not a reader side effect. The store never modifies a committed object in place; a hardlinked object's bytes are still digest-checked on every read.

Errors: `AccessDenied`, `ArtifactIntegrityError`, `ArtifactNotFound`, `ArtifactSizeLimitError` all extend `ArtifactError`. Invalid model construction raises Pydantic `ValidationError`; OS durability/space errors propagate and cannot become success. This implementation targets POSIX filesystems with directory descriptors, hardlinks and fsync, as used by the local controller.

## Visibility and role policy

| Role | Read | Write |
| --- | --- | --- |
| `solver` | public | public |
| `author` | public, authoring | same |
| `controller` | all | all |
| `trainer` | public, training | same |
| `evaluator` | public, evaluation | same |
| `reviewer` | all | none |

Visibility values are `public`, `authoring`, `private`, `training`, `evaluation`, `internal`. A public model cannot contain a nonpublic artifact reference; authoring models can reference only public/authoring artifacts. SolverView exclusively contains public instruction/workspace/check/runtime/inventory refs. Private SourcePair, ScenarioPlan, VerifierBundle, TaskBundle and QualificationReport cannot be declared public. SourcePair.reference, TaskBundle.reference_solution and TaskBundle.private_oracle require `private` or `evaluation` visibility; `authoring`, `training`, `internal` and `public` are rejected. Authoring receives admissible source snapshots and B separately, not H or the SourcePair.

Role arguments are trusted application capabilities, not authenticated identities or OS sandboxes. Do not give untrusted workers direct filesystem access to this store. M3/M4/M6 own the actual worker boundary and inspected solver export. Arbitrary byte content cannot be classified by this store; the producer is accountable for its correct visibility.

## Operation contracts

Configured services keep the plan's methods:

```python
construct(candidate: ArtifactRef) -> OperationResult
qualify(task_version: ArtifactRef) -> OperationResult
release(task_version: ArtifactRef) -> OperationResult
run(task_version: ArtifactRef, policy: PolicyConfig, limits: ResourceLimits) -> OperationResult
grade(task_version: ArtifactRef, submission: ArtifactRef, case_seed: int) -> OperationResult
audit(run_ids: tuple[str, ...]) -> OperationResult
train(config: TrainingConfig) -> OperationResult
evaluate(config: EvaluationConfig) -> OperationResult
```

The same arguments have transport validators `ConstructRequest`, `QualifyRequest`, `ReleaseRequest`, `RunRequest`, `GradeRequest`, `AuditRequest`, `TrainRequest`, `EvaluateRequest`. Task and candidate request validators check artifact kind and JSON encoding. These are data contracts, not unimplemented service stubs.

`OperationResult` requires `operation`, `disposition`, `artifacts`, `evidence`, `costs`, `reason`. Dispositions: `success`, `candidate_rejection`, `unsupported_semantics`, `invalid_measurement`, `infrastructure_failure`, `blocked_dependency`, `provisional`. Success requires at least one output artifact and successful producer receipt. A failed candidate grade can be a successfully performed grading operation; the actual reward/outcome belongs to its output artifact. Evidence may include earlier failures; they must not be discarded.

`PolicyConfig`: identity, policy_version, temperature, top_p, seed, system_prompt, harness_version, require_token_probabilities. `ModelIdentity.tokenizer_digest` and weights are required nullable, allowing honest nontrainable API/CLI episodes. Training-eligible RolloutRecord requires an identified tokenizer, aligned sampled token/logprob/mask arrays and the same behavior-policy version in every step. An `invalid_trajectory` stop requires `invalid_measurement` disposition; an `infrastructure_failure` stop requires `infrastructure_failure` disposition. Both require null reward and prohibit training eligibility, while retaining a saved submission for a separate grading operation. Malformed actions and ordinary token/tool/time limits preserve the final saved submission’s measured grade (0 or 1), backed by grading evidence. `candidate_failure` denotes a terminal candidate build/import/worker grading failure and requires zero reward when measured. Rewards are available only for valid `success` or `candidate_rejection` dispositions. A submitted episode may retain an invalid/infrastructure grading outcome with null reward pending retry.

`ResourceLimits`: positive wall_seconds, cpu_seconds, memory_bytes, pids, output_bytes, disk_bytes, tool_calls, input_tokens, output_tokens. `SeedPolicy`: algorithm, nonempty seeds, explicit same_cases_within_group. `TrainingConfig`: initial_policy, reference_checkpoint, tasks, limits, seeds, algorithm, group_size, max_updates, learning_rate, framework/version, backend_version, nullable budget_usd. Null budget is unspecified, not permission to spend. `EvaluationConfig`: tasks, arms, limits, seeds, partition, harness_version, checkpoint_selection_rule, invalid_trial_rule, metric, episodes_per_trial, frozen_roster, preregistration. pass_at_1 requires exactly one episode. TrialResult `success` requires a boolean resolved outcome and RolloutRecord reference; `candidate_rejection` additionally requires resolved=false. Other dispositions require resolved=null. EvaluationReport success requires at least one measured trial and at least one metric with positive sample_size and a nonnull estimate. Invalid/unresolved trials remain in the report alongside measured trials. Provisional/blocked reports may retain only unknown results; these local checks do not replace M8 roster/denominator verification.

## Evidence, costs and external gates

`EvidenceRecord` requires producer, command argv, UTC recorded_at, integer exit_status, nonempty artifact refs, exact Git revision digest, and scope (`real_integration`, `unit_diagnostic`, `source_inspection`, `human_review`). A record is a claim backed by artifacts, not proof that a command ran. Services must capture and verify actual receipts.

`CostRecord` requires category, wall_seconds, cpu_seconds, gpu_seconds, input_tokens, output_tokens, human_minutes, usd, measurement, note. Measurement `unknown` requires all numeric fields null; `partial` requires some measurement; `measured` requires every field measured. Never substitute zero for unknown. Zero is valid only as an explicitly reported measurement. All artifacts and operation results require nonempty cost records.

`HumanReview` requires actor_type exactly `human`, human_identity, subject_sha256, decision, nonempty evidence and attestation ref. Model signing is rejected. A human-looking string does not authenticate a person: M5 must verify the attestation through an external trust gate. Successful QualificationReport requires present passing three-state/control records, three fresh and interrupted-reset records, real-integration evidence, and human approval bound to the exact task digest. Here `RunAssessment.passed` means the named gate met its expected outcome (including semantically absent behavior in B); it is not the raw candidate feature-pass bit.

Cross-artifact joins, truth/authenticity of receipts, full qualification control coverage, source lineage closure, partition permissions, evaluation accounting, and genuine optimizer updates remain gates of their accountable services. Schema validation alone does not release a task. Artifact refs need not already exist at model construction, so services must resolve dependencies before admission. Qualification refers to the exact built task; a later qualified/released TaskBundle refers back to that report, avoiding a cyclic content hash.

## Development commands

```sh
uv sync --python 3.13.7
PYTHONPATH=src .venv/bin/python -m pytest -q
```

This host marks editable `.pth` files hidden, and Python 3.13 ignores them. Use explicit `PYTHONPATH=src` for local source commands; no permissions were changed. A normal wheel import is checked separately in M0 evidence. Downstream owners request reviewed schema extensions from M0, rather than adding permissive payload dictionaries.


## Provenance admission extension: CandidateRecord and SourcePair v2

`CandidateRecord` and `SourcePair` now require `schema_version=2` and `provenance_label` exactly `historical_request` or `reconstructed_specification`, with no default. M1 produces the validated admission decision, validates its archived edit/cutoff evidence and preserves the same decision in both records. The M0 schema does not infer historical provenance from missing edit history, and it does not authenticate this assertion. Cross-artifact equality remains M1's join validation. Reusable runtime and unrelated artifact models receive no provenance-label field.

`SourceSnapshot`, nested in CandidateRecord, now requires `redirect_chain: tuple[Text, ...] | None` in Python (nonempty array or null in JSON). A known chain must begin with `url`; its last URL identifies the final response URL. A known request without a redirect is `(url,)`. Null means redirect evidence is unavailable and is never equivalent to no redirects. M1 enforces acquisition URL policy and propagates actually captured hop evidence; the shared model does not synthesize hops. The synthetic examples with unavailable historical evidence use explicit null and reconstructed provenance.

`ARTIFACT_SCHEMA_VERSIONS` publishes the current kind/version map. The store writes the model's declared schema version into its envelope/reference. Raw-byte objects remain version 1. ArtifactRef can represent version 1 or 2 for preserved receipts, but current typed reads and required typed links reject v1 CandidateRecord/SourcePair with an unsupported-version error. Other artifact kinds remain version 1. Old stored bytes, references and evidence are preserved; there is no automatic migration or inferred default. M1 must inspect original evidence, revalidate provenance/redirect status and publish new v2 records and dependent references. Changing a reference's version alone cannot upgrade its digest-bound envelope. Old dependent manifests pointing to v1 source/candidate records also require reconstruction before current admission.


## Explicit bounded store reads (M3 handoff)

The two read methods accept optional keyword-only caps without changing artifact identities, schema versions or existing uncapped calls:

```python
recipe = store.get_artifact(recipe_ref, max_envelope_bytes=1_000_000)
source = store.get_bytes(source_ref, max_envelope_bytes=14_000_000,
                         max_payload_bytes=10_000_000)
```

These numbers are illustrative caller policy, not default limits. `max_envelope_bytes` caps the entire stored UTF-8 JSON envelope, including metadata and base64 text, before JSON parsing. The opened path must still be a regular file; its fstat size is checked before reading, then chunked reads obtain at most the cap plus one detection byte to reject growth after stat. The serialized cap is inclusive. Python parsing, canonicalization, buffering and model construction have additional memory overhead; this is not an exact resident-memory ceiling.

`max_payload_bytes` caps the decoded opaque bytes returned by get_bytes. Base64 length and padding determine the decoded size before base64 decoding; oversized payloads never reach that decoder. Standard alphabet validation and canonical re-encoding still reject malformed data. Empty payloads accept a zero payload cap, and exact boundaries accept all canonical padding cases. A payload cap alone does **not** bound the envelope read, JSON allocation or already-created encoded string; callers needing both protections must supply both caps.

Each cap accepts None (no cap) or an exact nonnegative Python int. Boolean, floating-point and string arguments raise TypeError; negative integers raise ValueError. An exceeded cap raises public `ArtifactSizeLimitError` with `limit_name` (`max_envelope_bytes` or `max_payload_bytes`), `limit` and `observed_bytes`. Treat observed_bytes as a lower bound: growth detection records cap+1, not necessarily total file size. Oversize rejection may precede digest/content validation; it never returns unverified bytes. Authorization, safe descriptor-relative paths, metadata/hash/canonical checks, typed validation and exposure checks remain mandatory on successful reads.

Duplicate-write comparisons use the new bounded reader with the expected envelope size. A larger existing object remains an ArtifactIntegrityError for corruption/collision and is never overwritten. M3 must separately enforce aggregate staged bytes, archive expansion, file counts and resource limits; the store only applies the explicitly supplied per-object caps. Independent review and M3 consumption are still required before declaring the downstream allocation gap closed.

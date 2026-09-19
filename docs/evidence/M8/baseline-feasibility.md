# M8 external baseline C feasibility: SWE-Bench++

**Decision as of 2026-09-19:** retain SWE-Bench++ as the preferred real-repository candidate source for arm C, but do **not** label it a runnable or qualified RL baseline. The official public release is accessible and pin-able. It is a 500-instance evaluation split containing solution and test-patch fields, while the trajectories described in the paper are not part of the public release metadata. No task row, solution, locked-test content, model prompt, image, or repository payload was downloaded in this research pass.

Arm C remains blocked until a controlled acquisition and adaptation pilot passes the same M1/M3/M4/M5/M6 boundaries as factory tasks. The paper and dataset card establish relevance; they do not establish compatibility with this system's trusted grading boundary or an RL experiment.

## Primary-source record

- The paper is [arXiv:2512.17419v1](https://arxiv.org/abs/2512.17419v1), submitted 2025-12-19. It describes 11,133 organic PR-derived instances from 3,971 repositories across 11 languages, plus hint-guided training trajectories. Its training experiment is supervised fine-tuning/rejection-sampling work, not evidence that the public release is an RL-ready corpus. The reported setup used MS-Swift on eight NVIDIA H200 144 GB GPUs. See the [method and training details](https://arxiv.org/html/2512.17419#S4.SS3).
- The public dataset is [TuringEnterprises/SWE-Bench-plus-plus at revision `da364537055b9bb5091783af78a02b6a3bc0e130`](https://huggingface.co/datasets/TuringEnterprises/SWE-Bench-plus-plus/tree/da364537055b9bb5091783af78a02b6a3bc0e130), last modified 2025-12-30T02:18:08Z. The Hub API reports it public, ungated, and enabled. The card fetched at that immutable revision declares only `default/test`, with 500 examples and 32,185,197 dataset bytes. A separate datasets-server response retrieved at 2026-09-19T08:30:44Z reports the same split/count, 8,457,514 download bytes, and 32,131,284 dataset bytes; that endpoint did not prove an exact dataset revision, so it is retained only as timestamped live metadata. Preserve both size values and their provenance rather than silently reconciling them.
- The evaluation harness is [GitHub commit `f938edd189049806fef7a76fdf01f0da55baa565`](https://github.com/TuringEnterprises/SWE-Bench-plus-plus/commit/f938edd189049806fef7a76fdf01f0da55baa565), committed 2026-02-05T15:28:20Z. The repository API exposed no tags and no GitHub releases at retrieval time. The pinned tree contains an evaluation harness and language-specific Docker/test/log-parser modules; it does not expose the paper's sourcing pipeline or released training trajectories.
- The [paper](https://arxiv.org/html/2512.17419#S4.SS1) and [public dataset card](https://huggingface.co/datasets/TuringEnterprises/SWE-Bench-plus-plus/blob/da364537055b9bb5091783af78a02b6a3bc0e130/README.md) describe different populations: 11,133/3,971/11 languages in the paper; a 7,000+ private validation set across nine languages and a public 500-task subset across seven languages in the card. The public subset's relationship to the paper's 1,782-instance evaluation sample and training mixtures is not specified well enough to treat their results as release-level baselines.

Pinned-byte verification found the GitHub README and MIT license at `f938edd189049806fef7a76fdf01f0da55baa565` byte-identical to their retained initial `main` snapshots, and the Hugging Face card at `da364537055b9bb5091783af78a02b6a3bc0e130` byte-identical to its retained initial `main` snapshot. The inventories distinguish these immutable bindings from live/timestamped responses.

The paper's manually annotated 488-instance sample reports 30.7% feature changes and 38.5% feature-request issues, but that sample is not identified as the released 500 rows. Those percentages therefore establish potential relevance, not public-release coverage. The card's difficulty thresholds are still placeholders, so the visible `difficulty` field cannot yet support a reproducible selection policy.

## Visible release metadata

The official metadata exposes these string fields without requiring row access:

| Field | Proposed M8 use | Caveat |
| --- | --- | --- |
| `repo`, `instance_id`, `base_commit`, `created_at` | Origin resolution, stable identity, temporal policy, and initial dedup keys | No PR URL, issue URL, reference commit, fork parent, monorepo package, or lineage proof is declared. M1 must resolve and archive those facts. |
| `language`, `task_type`, `repo_type`, `difficulty` | Predeclared coverage and funnel strata | Values and distributions were not inspected. Difficulty definition is unfinished in the card. |
| `problem_statement` | Candidate visible request after provenance and leakage review | Must not reach a generator or student until family/lineage assignment is frozen. |
| `patch` | Privileged reference input and content fingerprint | It is a solution artifact. Never include it in the authoring or solver view. A patch is not a verified integration relationship. |
| `test_patch`, `FAIL_TO_PASS`, `PASS_TO_PASS` | Privileged oracle inputs and test fingerprints | They are not independently controlled M4 observations and must never enter the worker/solver view. |
| `environment_config` | Untrusted recipe hint for controlled reconstruction | It is data, not an approved M3 `EnvironmentRecipe`; do not execute or trust it directly. |

The Hub declares only `test`. There is no upstream train/dev division and no released trajectory schema. The approved architecture permits a distinct controlled local `train` or `development` assignment while retaining immutable `upstream_split="test"` and excluding all related families/lineages from locked evaluation. Doing so forfeits comparison with the external leaderboard; this project does not claim that comparison. It is a study-design fact to record, not a special permission gate for public-data acquisition.

## License and release caveats

The code harness has an [MIT license](https://github.com/TuringEnterprises/SWE-Bench-plus-plus/blob/f938edd189049806fef7a76fdf01f0da55baa565/LICENSE). The dataset has a separate custom license in the [dataset card](https://huggingface.co/datasets/TuringEnterprises/SWE-Bench-plus-plus/blob/da364537055b9bb5091783af78a02b6a3bc0e130/README.md#7-licensing-and-permissions): non-commercial research, academic, or educational use only, with a non-exclusive, non-transferable, revocable grant. The machine-readable dataset metadata has no populated license value.

The row schema has no repository-license field. A card image and the phrase “No Copyright Issues” are not per-task rights evidence. M1 intake must resolve the license text at each pinned source revision, retain provenance, and reject `unknown` or ineligible cases under the eventual study policy. The dataset license also does not replace compliance with the licenses of the underlying repositories or dependencies. The public metadata does not establish whether every future intended use is non-commercial research, academic, or educational use; record that fact from the actual study context before use and leave it unresolved if the project record is insufficient. This is a license-scope constraint, not a blanket human approval requirement for retrieval.

## Required adaptation boundary

### Source-only positive allowlist

For each admitted instance, a trusted controller may read the pinned row and retain solution/oracle material privately. The solver package must be constructed from a positive allowlist containing only:

1. a fresh archive of repository state `B`, reconstructed and verified from the official origin rather than copied from the dataset payload;
2. an admissible visible request derived from pre-implementation sources;
3. allowlisted baseline public documentation/tests; and
4. solver-safe runtime fields produced by M3/M6.

The dataset file itself, `patch`, `test_patch`, F2P/P2P identifiers, private expected values, future Git objects/refs, release-side caches, and raw `environment_config` stay out of the solver image and mounts. Only digests and privileged references belong in split/evaluation manifests.

### Fresh M3 workers

Treat every external recipe, Docker fragment, build hook, dependency declaration, and repository as untrusted input. M3 must create an internal pinned recipe, fetch approved dependencies during controlled construction, verify the imported target path, remove later history and target-package artifacts, then run with the network and host credentials unavailable. Every episode starts from a fresh `B` workspace and fresh process/service state. External prebuilt images or instance caches cannot be accepted without digest pinning, layer/mount inspection, and proof they contain no target solution. Qualification requires repeated fresh builds/runs and interrupted-reset evidence through the reviewed M3 API.

### External M4 observations

The supplied test patch and native test result may assist reconstruction, but they cannot be the trusted reward boundary. M4 must rebuild a source-only submission onto clean `B`, run candidate code only inside M3, and return bounded ordinary observations to a separate controller. Every mandatory behavior and preservation obligation needs an explicit case ID, serialized adapter, expected relation, and externally controlled observation. F2P/P2P names are hints for coverage, not self-authenticating verdicts. Missing cases, early exit, forged output, a candidate-import crash, or incomplete preservation checks cannot pass. The private test patch is never mounted in the candidate worker.

Tasks whose semantics cannot be faithfully observed through the supported Python CLI/API adapter are rejected from this track. Filtering to that capability must be declared before inspection and reported as selection bias; it cannot support a multilingual or all-feature claim.

## Split and dedup contract

Before any task text is used for generator tuning, the controller must add these fields to an external-corpus intake record:

| Category | Required fields/evidence |
| --- | --- |
| Release identity | dataset ID and revision SHA; upstream config/split; row `instance_id`; harness commit SHA; row-content digest |
| Repository family | canonical origin URL; repository ID; fork network/root; mirrors; monorepo/package identity; copied-code evidence |
| Request lineage | PR/issue IDs and URLs; base and reference/integration commits; backports; linked requests; descendant/generated-task relations |
| Content dedup | normalized request digest/near-duplicate cluster; changed-path set; privileged patch and test-patch digests; baseline tree digest; environment-config digest |
| Temporal policy | validated PR/request creation and merge times; dataset `created_at` semantics; admissible-source cutoff; exposure uncertainty |
| Study use | immutable local `train` or `development` assignment; never silently rewrite upstream `test`; explicit exclusion from the project's locked test |
| Stratification | language, supported interface, task type, repo type, difficulty, feature/bug classification with provenance |
| Rights | dataset-license decision plus pinned underlying repository/dependency license records |

M1 relation closure must keep forks, monorepo relatives, copied code, backports, linked requests, and descendants in one partition. Deduplicate arm C against factory train/dev, the independent locked test, and any external evaluation corpus. If a conflict is found after freeze, quarantine the affected task/checkpoint lineage and create a new locked test rather than editing old outcomes.

## Proposed adaptation/rejection funnel

The funnel must record `entered`, `accepted`, `rejected`, `invalid`, and a stable reason code at every stage. Current counts beyond metadata are intentionally absent.

| Stage | Current evidence | Acceptance gate |
| --- | --- | --- |
| 0. Metadata preflight | 500 declared rows; payload not retrieved | Exact dataset/harness pins, readable license, visible schema, upstream split recorded |
| 1. License-scope record and acquisition | Not attempted by bounded research scope | Record the actual intended-use classification under the custom non-commercial terms; retain unresolved status if facts are missing; bounded retrieval at the pinned revision |
| 2. Origin reconstruction | Not attempted | M1 verifies source URLs, B/H or patch integration, license, changed files, cutoff, and request evidence |
| 3. Family split and cross-corpus dedup | Not attempted | Closure and content/lineage scans complete before generator access; no locked-test overlap |
| 4. Capability and allowlist build | Not attempted | Predeclared source frame; supported Python CLI/API semantics; M6 inventory contains only allowed B/request/public artifacts |
| 5. M3 environment conversion | Not attempted | Fresh isolated worker, pinned dependencies/images, import-path proof, network policy, repeat/reset receipts |
| 6. M4 verifier conversion | Not attempted | Complete external observations and private controller comparisons; native tests are diagnostic only |
| 7. Qualification | Not attempted | Reference/no-op/semantic negatives/hard-code/regression/forgery controls, alternative positive, repeats, and human review |
| 8. Arm-C pilot | Blocked | Same starting checkpoint, harness, tools, optimizer family, limits, paired evaluation roster, and declared development-tuning budget as D |

Publish the realized funnel and its feature/difficulty/language mix. If Python/interface filtering or unverifiable oracles leave an unrepresentative subset, narrow the claim or reject SWE-Bench++ as arm C; do not call the remainder equivalent to the public benchmark.

## Exact blocked gates and hand-off

- **Data gate:** no task rows were downloaded because this pass was metadata-only and the upstream intake/adaptation APIs are not ready. Later pinned retrieval is a normal controlled implementation step under the recorded license constraints. The local assignment must preserve `upstream_split=test`, exclude related tasks from locked evaluation, and disclose that external-leaderboard comparability is forfeited. Public training trajectories described by the paper were not found in the release metadata.
- **API gate:** actual M8 integration waits for reviewed M0 artifact schemas/storage and reviewed M1/M4/M6/M7 interfaces. M3/M4 interface documents were not present during this pass, so no adapter signature is assumed.
- **Qualification gate:** zero instances have been reconstructed, executed, externally observed, deduplicated, or human-qualified for this pipeline. The public release's 500 rows are candidates, not usable tasks.
- **Human gate:** task qualification still requires independently authored alternative positives and human checker/adjudication review for the exact task version. An LLM review cannot finally approve its own checker.
- **Compute gate:** the user has no remote compute and the local host has no CUDA. Actual arm-C/arm-D RL training and empirical comparison require a compatible reviewed SkyRL/Harbor path and declared GPU allocation. The paper's eight-H200 SFT result is neither locally reproducible evidence nor a substitute for the matched RL experiment.
- **Comparability gate:** paper pass@10 on 1,782 instances, card pass@1 on a different 500-instance public subset, and the proposed paired pass@1 RL evaluation are distinct protocols/populations. None may be carried forward as an M8 result.

Because the official release is accessible, no alternative real-task corpus was surveyed in this bounded pass. [SWE-smith](https://swesmith.com/) remains eligible only as a separately labeled synthetic-data comparison after the real-task arm is operational; it cannot substitute for arm C.

Raw metadata responses are retained under ignored `.feature-rl/research/M8`. Exact URLs, UTC retrieval times, byte counts, and SHA-256 digests are in [source-inventory.json](source-inventory.json). No product code, test cases, task payloads, training, or evaluation execution was performed.

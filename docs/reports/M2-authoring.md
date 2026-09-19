# M2 grounded authoring and scenario planning

## Result

The authoring/scenario product is implemented at product revision `6568a11e0fd85a9f2f4864acc4025030ff55901f`. It provides real M3 Click discovery, store-backed request/B/discovery resolution, M0-derived proposals, deterministic grounding and finalization, an explicit scenario-planning stage, exact frozen-contract joins, bounded repair journals, and replay after provider, journal, or final-artifact publication failure.

The actual Click construction gate did not pass. One M3 discovery succeeded. Three contract calls consumed the stage budget: two reached the fixed 120-second deadline, and the final concise call completed but returned a duplicate-key response. No contract was frozen, no privileged H feasibility review was authorized, and no scenario call was made. There is no handwritten or salvaged production artifact.

## Public API

The public modules are `feature_rl.requirements` and `feature_rl.scenarios`; the full call contracts are in `docs/interfaces-M2.md`.

- `ClickDiscoveryService`, `BaselineRetriever`, and `AuthoringEvidenceResolver` bind model-visible text to exact controller-store artifacts.
- `RequirementContractProposal` and `ScenarioPlanProposal` derive their semantic fields from M0 definitions.
- `ContractAuthoringService` and `ScenarioAuthoringService` enforce one initial attempt plus two diagnosed, materially changed repairs and return real stored M0 artifacts on success.
- `AuthoringExhausted` retains rejected attempt refs. `GenerationProviderError.replay_result` and `replay_error`, `AuthoringJournalPublicationPending.replay`, and `AuthoringPublicationPending.replay` resume storage, rejection journaling, or finalization without another model call. Recovered outcomes must match their bounded immutable request/schema/context/status/content/usage/cost archives exactly.

Contract finalization checks the resolved request text and provenance classification, discovery-bound entry points and observations, ID namespace, allowed changes, quote/locator/source/label grounding, and ambiguity disposition. Scenario planning additionally requires the exact canonical frozen-contract context and IDs, the contract's admitted request/B/discovery/public-check reference set, its request text/provenance, its observation set, structural mandatory feature-plus-compatibility ID coverage, oracle grounding, and the fixed same-case seed policy. This structural coverage does not prove semantic entailment or checker discrimination.

## Actual B-only construction

The sanitized setup receipt was `docs/evidence/M2/coordinator-authoring-input.json`, SHA256 `6172bbb28f9cd9275db21bc47ea55af922fe3d295758f7c443881736d56aebc0`. The controller copied only these author-visible artifacts from `.feature-rl/research/M1/production-store` into `.feature-rl/research/M2/authoring-production/store`:

- baseline `source-archive` `4ff1d8a5d478ffb12950ec2661f3b38be5b7c9925691726c27b83234c5814575`
- request `authoring-request` `d8d8a97861a597c7bfe181dfd3bbd6dbf72b7854ee13d555add1b1e07f0d2c61`
- license `source-response` `b84ace5d4d01f55ab2db4e25ffddb9239104176e99a73e4799dcadbd8e612ab9`

License bytes were verified and retained as eligibility/provenance input. They were never a model context. Model contexts were the exact request, the five declared B retrievals below, and the sanitized discovery observation:

- `src/click/core.py:1532-1560,1878-1965`
- `src/click/exceptions.py:212-243`
- `tests/test_options.py:136-160`
- `tests/test_commands.py:8-41`
- `docs/commands-and-groups.md:72-100`

The retrieval selected 8,699 bytes. No expansion was requested. Directory metadata in the M1 tar is admitted; links and other non-regular members remain rejected.

M3 built and executed Click 8.3.3 once through the installed-wheel path. Discovery artifact `75b5bf2609568199919c697601b92dbd0ab59a7ed192b4ec3b773058080974a0` binds baseline `4ff1d8…`, recipe `fc3506e5c49a7f56da4c49f6bed326923a6e9fc8ead0300fbeeedac8cb09f960`, build receipt `95609a5f73897bd0bb66eff1de654c62d064cb2efe198d8a165adba2032b489a`, and execution receipt `601a47a8ca7cc25a0e7bd6fab1001e3396b98f935ee333d7e5f7209ece258988`. It observed `/workspace/site/click/__init__.py`, `click.Group.resolve_command`, exact-command exit 0/output `ready\n`, and unknown-command exit 2/output containing `No such command 'statuz'.`.

The model was the pinned local `mlx-community/Qwen3-4B-Instruct-2507-4bit` revision `50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b`. Every request used greedy seed 0, 4,096 output tokens, the fixed 120-second wall/CPU limits, 3.5-GiB MLX guideline/wired limits, zero cache, 4-GiB sampled-footprint kill threshold, and 5-GiB declared ceiling. Exact tokenizer receipts were written before each call.

| Attempt | Request revision | Input / cap | Output | Wall / CPU | Disposition |
|---|---|---:|---:|---:|---|
| initial | `c4ed70d` | 6,919 / 9,216 | 2,469 | 120.085s / 45.626s | deadline; journal `acc19437a873207cc3ce74dbe37ce7f479aa6a5f2ff4b11d4fcc095fc2bc1890` |
| repair 1 | `d267208` | 6,948 / 9,216 | 2,520 | 120.113s / 46.228s | deadline despite prose concision; journal `261cd3a33c46f28ea7d55825add6e8509ef196ebb0f952ec29ec3891134b6a4e` |
| repair 2 | `aa75086` | 6,941 / 9,216 | 1,647 | 84.458s / 30.848s | completed generation; strict response rejection; journal `af7cee9c0af50eeddcf1a21f23b0e875964796648d20a34659e68a460806ace6` |

Repair 1 added concise-field guidance. The retained output still expanded all eight namespace IDs. Repair 2 reduced the neutral maximum namespace to four IDs and prohibited duplicate semantics and long quotes. This remained a maximum rather than a required count.

The final response artifact is `08af74f2f8ec3a5b53e5a625de314eecb26999e92155a1a1d6c41e5125e807e8`, stored at `.feature-rl/research/M2/authoring-production/store/08af74f2f8ec3a5b53e5a625de314eecb26999e92155a1a1d6c41e5125e807e8.json`. Its completed output text SHA256 is `0557c089d3f53c16c46ef09b783f0cc841469b5148917e9aa7431460c9d7f49e`. The first strict failure is the second `allowed_changes` key at character 5,329, line 142, column 5. A permissive parse also shows `REQ_1` and `REQ_2` duplicated across feature and compatibility lists, collapsed or non-verbatim B quotes, and missing evidence provenance fields. `final-malformed-diagnostic.json` records the bounded decode. Parser or grounding rules were not weakened.

Global Click candidate-repair use at this gate is 3/4: M3's retained candidate repair 1 plus two contract repairs. M2's contract stage itself is exhausted. The journals and this report expose those counts for the M6 factory; M2 does not create a second global registry.

## Verification

Focused final command:

```sh
./.venv/bin/pytest -q tests/test_authoring.py tests/test_generation.py
```

Result: **137 passed in 0.97s**, exit 0. It covers proposal derivation, source resolution, actual discovery binding, archive retrieval, forged text/locators/refs, request/provenance joins, unsupported entry points/observations, ambiguity and ID failures, structural contract/scenario ID coverage and seed joins, exact frozen-contract text/source/ID joins, semantic repair changes, exhaustion, provider recovery, journal replay, and final-artifact replay.

Round-two focused correction command:

```sh
PYTHONPATH=src ./.venv/bin/pytest -q tests/test_authoring.py tests/test_generation.py
```

Result: **146 passed in 1.01s**, exit 0. It adds exact recovered-request/schema/context/status/content/usage/cost binding for both services, failed-generation publication replay and rejection-journal recovery without provider reuse, exact frozen public-check admission, and the `m4-sha256-v1` reproduction default. An earlier identical pytest invocation printed 146 passes in 1.12s, but its wrapper then failed because it assigned zsh's read-only `status` variable; that wrapper did not capture pytest's exit status and is retained separately rather than treated as verification.

Round-three focused compatibility command used the same two test files. Result: **149 passed in 1.05s**, exit 0. It covers complete registration and preflight recovery variants plus a valid 1,262,039-byte response receipt under the unchanged 1-MiB raw-output limit. No full suite, native generation, tokenizer, or Docker command ran.

Round-four C1 verification ran four registration/preflight tests: **4 passed in 0.17s**, exit 0. Its real `LocalGenerationProvider` case injects the initial registration publication fault, replays the authentic provider error, and journals it through authoring with zero backend verifications, runner calls, or recovery-provider calls. The validator now matches the producer's exact unknown-cost note beginning `Execution did not start`.

Affected full-suite command:

```sh
PYTHONPATH=src ./.venv/bin/pytest -q
```

Result: **414 passed in 97.85s**, exit 0. A preceding invocation without exported `PYTHONPATH` is retained separately: 406 passed and eight independently owned M6 durability subprocess tests failed because their child interpreters could not import `feature_rl`. Both full-suite runs exceeded the focused-only concurrent-owner instruction. Their logs are retained as observations, but source hashes were not captured before and after those runs, so they do not establish a source-stable cross-module integration snapshot while M4 was editing concurrently.

The production commands were:

```sh
PYTHONPATH=src ./.venv/bin/python docs/evidence/M2/authoring-production/run.py contract
```

The command ran three times after two pre-generation controller setup attempts. Setup attempt 1 failed strict Python deserialization before Docker/tokenizer/provider use. Setup attempt 2 completed the single M3 discovery and then exposed safe directory metadata in the baseline tar; retrieval was fixed and discovery was resumed from its retained artifact. Attempts 3–5 made the three provider calls above. Each exit, stdout, stderr, capacity snapshot, and exact measurement is retained separately.

## Isolation disclosure

The model boundary used only the sanitized request/B/discovery contexts described above. No `SourcePair`, H, diff, history, private case, license text, or changed-file metadata entered a prompt or retrieval decision.

The implementation owner was not fully blinded. Before the sanitized handoff existed, two accidental read commands exposed metadata outside the intended owner view:

```sh
sed -n '1,220p' docs/evidence/M1/coordinator-round2-intake-1.json
sed -n '1,100p' docs/evidence/M3/production/run_click.py && sed -n '1,180p' docs/evidence/M3/production/click-repair1-complete.json
```

The first displayed SourcePair identifiers and changed-file categories. The second displayed retained private H execution metadata and base64 observations. The owner did not decode or use H/archive/diff/history bytes, and no model context was selected from those exposures. The later production setup used only `coordinator-authoring-input.json` and attributable B/request/runtime observations.

## Remaining gate

The code is ready for scoped re-review and downstream non-GPU integration. Actual task construction remains failed/unverified because there is no grounded frozen contract or scenario. Semantic qualification, H feasibility, checker coverage, human task review, and registry admission therefore remain blocked for this Click task artifact while later module code may continue under the code-completion ruling.

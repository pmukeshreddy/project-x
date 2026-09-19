# Feature-RL progress and recovery

Read this first, then the current owner brief, actual interfaces/code and latest report/review. Both immutable user inputs were fully read: `feature_rl_pipeline.md` (architecture, SHA256954a6065c483c10bc4574945edc1523149b5968cec75ae896f788b53653beeea) and `codex_multi_agent_implementation_prompt.md` (execution). Do not stop while authorized implementation/review remains.

## Current scope and priority

Complete all module code and integration, run available non-GPU checks and provide reproducible training/evaluation commands. GPU training and experimental results are deferred and explicitly unverified, not code-completion blockers. All admission/evidence requirements stay enforced at runtime. Latest user priority: **one real Click feature through M2, grading, qualification and the integrated path**; keep necessary correctness/isolation checks, consolidate evidence and stop expanding administrative/dependency tooling without a concrete blocker. The prepared M0 training-dependency slice remains undispatched until training checks need it. No fabricated feature, control, approval, rollout, reward or experiment.

## Coordination

Workspace `/Users/mukeshreddypochamreddy/Desktop/project x`, branch `implementation/feature-rl`; initially no ancestor AGENTS.md. Root owns coordination, interfaces, integration, review commits and this ledger. Each module has one real accountable owner; product defects return to that owner. Scheduling decision `docs/decisions/independent-code-slices.md` permits at most two disjoint product slices with reviewed dependencies, root and a review slot. No child subagents or concurrent shared-config edits. Focused tests while owners edit; integrated checks at stable checkpoints. Commit explicit owned paths; preserve Finder `.DS_Store`, `.superpowers/` and ignored private state.

Applied skills already read: using-superpowers, brainstorming (supplied spec is approved design), writing-plans, worktrees, subagent-driven-development, dispatching-parallel-agents, TDD, requesting/receiving review, systematic-debugging, verification-before-completion, openai-docs. No goal-tool goal exists. No routine permission questions. Actual human approval requires a concrete package and a real human, never an agent signature. `docs/briefs/review-policy.md` separates code defects from unexecuted experimental gates.

Host CPython3.13.7/macOS26.3/M4 arm64/16GiB; tests use `PYTHONPATH=src .venv/bin/python -m pytest -q`. Docker Desktop27.4, Linuxarm64,10CPU,8217968640B. No remote compute. No paid provisioning, destructive pruning, automatic external messages or inferred GPU budget. Tool approval is never; omit sandbox_permissions. Commentary should remain current; waits at most60s.

## Owner state and immediate work

| Module | Accountable owner | Current state |
| --- | --- | --- |
| M0 | `/root/m0_contracts` | Core/provenance-v2/MLX config/bounded CAS reads reviewed and integrated |
| M1 | `/root/m1_sources` | Intake/history/splits reviewed and integrated; real Click candidate provisional |
| M2 | `/root/m2_authoring_recovery` | Requirement/scenario product checkpoint0544d9b; ACTIVE on real Click path and focused fixes |
| M3 | `/root/m3_runtime` | Product2190af0 reviewed PASS and integrated342tests PASS |
| M4 | `/root/m4_grading` | Interface preparation only; implement next after M2 handoff |
| M5 | `/root/m5_qualification` | Signature mechanism preflight only; product next after grading |
| M6 | `/root/m6_factory` | ACTIVE finishing minimal registry/event/job/cost/dependency core, then actual orchestration |
| M7 | `/root/m7_learning` | Pinned stack audit only; agent runner, GRPO and SFT code still required |
| M8 | `/root/m8_evaluation` | Corpus metadata audit only; evaluation and audit code still required |

Old `/root/m2_authoring` is errored/read-only and explicitly replaced; do not reactivate as implementation owner. Transfer at5cdd773 and `docs/briefs/M2-owner-transfer.md`. Root interrupted its errored turn without restarting it. Agent inventory can omit dormant owners. A fresh reviewer dispatch hit thread capacity while both owners were active; no M2 reviewer began. Do not repeatedly redispatch unchanged at capacity. M6 has been asked to finish its core checkpoint and FINAL to free the slot.

## M2 current checkpoint and real execution

Read `docs/briefs/M2-authoring.md`, `docs/decisions/M2-proposal-finalization.md`, `docs/decisions/M2-authoring-execution-budget.md`; independent review brief `docs/briefs/M2-authoring-review.md`. Assigned approved base4530ad7. Product6297c42, reproduction020a1f0, strict-JSON transport fix0544d9b9af2ba3f7680d7a1f53256556f73bb4ce. Complete aggregate review diff `.superpowers/sdd/implementation-plan/review-4530ad7..0544d9b.diff`,215137B/11commits; **target will change after active fixes**. No independent product approval yet.

Actual APIs in `feature_rl.requirements` and `feature_rl.scenarios`: derived M0 proposal constraints, bounded B retrieval and source-backed context resolver, real M3 Click discovery, contract/scenario finalizers and services with archived attempts. Exact frozen scenario contract and mandatory feature/compatibility coverage are required; no initial ID count forcing. Owner reports focused126tests passed0.86s at0544 (`tests/test_authoring.py tests/test_generation.py`); root has not rerun them while work continues.

Real reproduction `docs/evidence/M2/authoring-production/run.py`:

- Attempt1 exited1 before Docker/tokenization/native because ArtifactRef JSON used strict Python deserialization; raw streams/exit retained, fixed0544.
- Attempt2 completed **one real M3 baseline build+installed discovery** and failed before tokenization/native because the baseline archive contains safe directory entries while BaselineRetriever rejected all non-files. Owner is fixing directory validation with a regression and reusing the retained discovery; no repeat discovery is needed.
- **Actual native/provider feature-generation calls:0. No frozen contract/scenario yet.** First task draft must be model-generated, grounded and frozen before separate privileged H feasibility review.
- Root also found scenario request contract refs were checked without comparing text/locator to the resolved artifact. Same owner is adding exact canonical context/ID joins. Root requested replayable final-artifact publication recovery after a successful generation, without another model call. These are necessary correctness fixes, not new tooling.
- Root and owner independently read the exact admitted issue request: Did-you-mean suggestions for misspelled subcommands, no requested new public NoSuchCommand API. Existing Group/resolve_command and CLI exit/output observations fit this request; do not infer a new exception contract from H.

### Authoring boundary and setup

Use only **`docs/evidence/M2/coordinator-authoring-input.json`**, SHA2566172bbb28f9cd9275db21bc47ea55af922fe3d295758f7c443881736d56aebc0, for authoring setup. It contains parsed M1 authoring_view refs, author-role store,6exact wheel paths/hashes, socket and pre-exposure B retrieval note; no H/SourcePair data. Exact quotes/locators/text must resolve to admitted bytes; initial request+B/public only. License is eligibility/provenance outside behavior prompt. Unsupported semantics/unresolved ambiguity cannot silently pass.

M2 owner disclosed two exposure incidents: `sed -n '1,220p' docs/evidence/M1/coordinator-round2-intake-1.json` showed SourcePair metadata/changed-file categories; a parallel read of run_click.py plus first180lines of M3 click-repair1-complete.json displayed private H execution metadata/base64 observations. Owner says no H/archive/diff/history bytes decoded/read/used. **Owner is not fully blinded.** No further private reads; model context must remain auditable request/B/public-only, using pre-exposure published B spans plus attributable expansions, with no choices from changed-file metadata. Preserve exact disclosure in final report.

### Native allocation: do not repeat consumed smokes

New actual-authoring budget: first exact post-discovery request/schema/prompt/spans measured with pinned offline tokenizer; input cap measured+20%, rounded1024, absolute16384; output4096. First real structured call doubles as capacity measurement, no extra disposable call. Max6new calls total; stage repairs<=2, prior Click environment repair already1/2 and candidate1/4, leaving at most3candidate repairs. Each call120s wall/CPU,1MiB stdin/output/file,3.5GiB MLX/wired,cache0,4GiB sampled kill20ms with1GiBguard/5GiBdeclared. No unchanged capacity retry, bounds widening, model fallback/download. Recheck host capacity; do not run heavy Docker/CPU/native diagnostics concurrently with model load.

M2 provider e14f67983db678813afcfeb191532e691b0b20e3 independently round5PASS. `LocalGenerationProvider.generate(request,StrictModel)` yields content/usage/cost/record; GenerationProviderError retains records/recovery/replay_publication.11archives, exact tokens/source/config, strict bounded native transport, no fallback. Greedy selected_model_logprobs are not behavior probabilities; behavior_logprobs=None/nontraining.

Old provider budgets ALL CONSUMED: owner2oldv1 + root1v3 at999d1af,18:47UTC; root299tests17.09s then450input92output/25checks/11archives,6.3999wall3.9209CPU,maxsample3139029728B/lifetime3173337824B. `docs/evidence/M2/coordinator-provider-gate.json`, raw logs and permanent ignored coordinator-v3-native-claim.json. Do not rerun.

Pinned MLX `mlx-community/Qwen3-4B-Instruct-2507-4bit` rev50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b,11files2278969697B under `.feature-rl/research/M2/model/`. Model manifest SHA697253a717e5857f1dfe3c14594f747c9c8118e6bc9c877bfc0c6faa6a7f50a0; dependency manifest d2db652d0634ff87b38ea93de0c54cb75560b209c783e6409937903a03f5a831. Existing sizing9e8434c3 (`authoring-envelope/`):7249tokens without license, not actual derived/post-discovery request; earlier7669diagnostic included license. Old tokenizer repeat19:04:31–32UTC consumed. Actual new request measurement remains authorized.

## Reviewed upstream handoff

M0 corecb53f99/provenancev2e16493b/optionalMLX18bd8f34/boundedreadbf065ec738f29f13cf0cc783b53353adcbe6fab8 reviewed. `docs/interfaces.md`; get_artifact(max_envelope_bytes), get_bytes(max_envelope_bytes,max_payload_bytes), inclusive exact int/None, bool rejected. Defaults uncapped: callers supply caps. Immutable canonical CAS, descriptor-relative/nofollow, exact role visibility; human-review model is a claim, not authentication. Pydantic2.13.5/core2.46.5/pytest9.1.1. OptionalMLX34wheels96889626B, core/dev12unchanged.

M1 a658ae4d0ffba45d1fbde6c4edad71da33ea570e reviewed round2PASS; root155tests and2actual Click intakes09:35UTC, receipt SHAdeca99ae7076bef8636a8b755832ad6a8df2ab9b91503f1bf9e0a6cadb0b2ea7. Store `.feature-rl/research/M1/production-store`, readonlygit `.feature-rl/research/M1/git/click.git`. PR3228/issue3107; B19fd4d6e18bc9fce451f92f422696b11169faa57/H831c8f0948af519e45b90801d7430ff25451f972. B-rooted squash/source-head-tree proof, request timing unproved => reconstructed_specification. Private source pair/reference refs are in the M1 receipt, **not authoring inputs**; use sanitized input above for B/request/license.

M3 product2190af04ed10dccd65249963a727f19c0cdedaab reviewed round3 **spec PASS/quality PASS, P0/P1/P2=0**, `docs/reviews/M3-production-round3.md`. Originala9fec98, fix1ad154f8 and fix22190af0 close terminal stale-source and cleanup-lock races; actual synthetic schedules and18independent evidence/hash checks retained. No repeated historical run needed.

Root integrated M3 gate atdcb442b55a12f4ffeee36ab32d5a3ef5ddc7cb63, committed4530ad7: `PYTHONPATH=src .venv/bin/python docs/evidence/coordinator-local-check.py docs/evidence/M3/coordinator-integration-round3`;2026-09-19T20:20:21.271153–20:21:51.392891UTC,exit0,**342passed89.73s**, outer90.122s.41source/test/config files unchanged, raw hashes matched. Existing driver is sufficient; don't create more wrappers.

### M3 real runtime

Exact API `docs/interfaces-M3.md`, report `docs/reports/M3.md`: DockerEngine(state_root,socket_path,policy), qualify_boundary then EnvironmentRuntime(store,engine,revision). create_click_recipe(B,pins,source_evidence); open_workspace baseline/reference/candidate with exact source-pair/allowed-change joins. execute_development edits current source without build prerequisite; build_snapshot offline hooks inside fresh worker with exact wheel/source byte map; execute uses a separate fresh installed-wheel worker. No candidate/hooks/generated code on host. SavedSource raw/tree/CAS/version/time; limit keeps last confirmed source. Typed failures and retry_publication preserve evidence; close/reset reject unverified owned cleanup under lock, then recover/retry. Full policy/receipts in interface.

One Linuxarm64/Python3.12.14/Click8.3.3/no-service profile.512MiB/noSwap,.5CPU+sampled60CPU-sec,64pids,128MiBwritable,120s+10scleanup,2MiBoutput,1MiBstdin,8MiBsource/16MiBarchive/2000members/32MiBstage. Nonroot/capsdrop/nnp/readonly/networknone/no private mounts; explicit seccompSHA005f6ae1a0f3f9d1a0c044f83289e2ea54180c97a105b2539422588eac2fde44. Caller budgets controller retention.

Original B health failed from missing less: all raw evidence retained. Neutral repair environment1/2,candidate1/4 used official Debianarm64 less590-2.1~deb12u2,128172B SHAeb430d92921f98b163031ee3ac81a96110d1f20bd84f67faab06dad50c75d744; COPY-only ELF/copyright. Current local manifest digest `feature-rl-m3-less@sha256:b28d3b1eeaa6251e82354fa61dc28a29afead9e2d7179436e77ca8ba612032fa`; distinct configID59cff2cafc7819fee5cdef4ab8f76fa5ec574d16d78a2661953dfd1498aaf185. Basepython@sha256:eb5be8e5b4d0a159c237946bbdd06356dda5d19c30fc4f7843e8046d3a590333. No registry push. Local retention/recreation limitation disclosed in M3 production evidence. Proposed schema widening CANCELLED before edits.

Repaired acceptance `docs/evidence/M3/production/click-repair1-complete.json`,19:27:54.637199–19:29:41.405313UTC: Bimport,3Bregression each1489passed23skipped30000deselected1xfailed,3freshHimports,3save/timeout/reset; cleanup/recovery[]. **H imports only, no feature semantics checked.** Six purepy wheels1753266B under `.feature-rl/research/M3/dependencies`: pytest9.0.2,iniconfig2.3.0,packaging26.0,pluggy1.6.0,pygments2.20.0,flit_core3.11.0. Exactpins runtime.WHEELS and sanitized M2 input.

## Remaining integration

M4 existing brief `docs/briefs/M4.md`, preparationaabce5d (`docs/evidence/M4/interface-preparation.md`). Implement generated untrusted adapter and closed typed controller comparisons/private complete seeded cases; source-only submission/fresh build/ordinary observations/external binary grading, exact joins and actual adversarial route. No eval/generated controller callbacks or public-test-as-reward shortcut. Execute real B/H after frozen M2 artifacts; don't alter semantics to fit H.

M5 `docs/briefs/M5.md`: actual M4-backed Babsence/H, semantic omission per mandatory requirement, plausible wrong/hardcoded/regression/forgery, independently authored valid alternative,3fresh+3interrupted resets; repair budgets, typed provisional and human attestation/revocation/replay. Signature preflight75ed88a (`docs/evidence/M5/attestation-preflight.md`)43synthetic SSHSIGN commands/16negative mechanisms is **NO_TASK_APPROVAL**, no actual human key. Missing human approval may prevent release, not complete independent code. Ask only after concrete reviewable task/control package exists.

M6 active core assignedf4819c9: `docs/briefs/M6-registry-core.md`, registry/**,test_registry*, own report/interface/evidence. Owner reports minimum attempt/cost/dependency state complete; final storage/recovery checks pending. No expanded admin API. Durability preflight8b68d99 SQLite3.50.4/APFS DELETE/EXTRA/fullfsync169assertions/16positions/3corrupt/19children is process-crash diagnostic, not physical power-loss proof. Future factory must reconcile actual M2 durable generation receipts/costs; no invented global transaction. Next owns real service orchestration, solver package, CLI, README.

`docs/lifecycle-admission.md` binds complete immutable BUILT T0 (solverview final,QNone) before review. Human authenticates T0+frozen evidenceP+policy/challenge. AcceptedQ.task=T0 with same reviewed evidence. Tn qualified/calibrated/released differs from T0 **only** state/qualification; all other fields/provenance/costs identical. Transition costs/evidence are OperationResult/ledger. Consumers resolve Tn→Q→T0 plus legal transitions/quarantine/revocation; no forged state string or duplicate packager.

M7 `docs/briefs/M7.md`, `docs/evidence/M7/compatibility.md`: SkyRLskyrl-v0.3.0f5bc3b78dfddfb352870d5d7430cd226e5785838; Harbor3de07a0e01f3368921766437fc7afece3ddec23d v0.13.1;59pinned source files under `.feature-rl/research/M7/`. Implement actual agent runner and SkyRL/Harbor/FSDP/vLLM adapter, plus actual SFT/protocol-warmstart path (dbea374). Exact contexts/tokens/masks/behavior probabilities/currentpolicy;4same-case rollouts,valid-onlygroups>=2,uniformzero,oneepochclipKL,64maxgroups/8mixedacross4tasks execution signal gate; checkpoint/reload/resume. All arms identical initialpolicy. No GPU-preflight-only stub completion. CPU diagnostics are not training evidence.

M8 `docs/briefs/M8.md`, `docs/evidence/M8/baseline-feasibility.md`: SWE-Bench++HFda364537055b9bb5091783af78a02b6a3bc0e130/harnessf938edd189049806fef7a76fdf01f0da55baa565 metadata only,500upstreamtestrows differ from papertrajectories; codeMIT/dataNCresearch/repositoryrights. No rows/weights acquired. Implement actual adaptation funnel, frozen families/lineages/fullassignedpairedtrials, A/B/C/D arms/task+familymetrics/uncertainty/auditweights+humanadjudication/quarantineconsumedupdates. Externaltrain assignment retains upstream_split=test and excludes related lockedtest. Missing empirical data stays unverified, never fabricated zero.

## Deferred setup, only when concretely needed

`docs/decisions/training-worker-endpoint.md`: Linuxx86 CUDA controller can use an explicitly preconfigured protected Unix socket forwarded to qualified arm64 Docker daemon; CLI+HTTP same endpoint, API1.47. No automatic SSH/provisioning. Actual forwarding/qualification unverified; expose socket in commands, don't assume local x86 daemon works.

`docs/decisions/training-dependency-overlay.md`: SkyRLlockPydantic2.13.4/core2.46.4 differs from project2.13.5/core2.46.5. Two-package exact hashed overlay is source-compatible, actual full stack unverified. Preserve upstream CUDA-specific wheels/sources; generic PyPIvLLM can select wrong CUDA. Install overlay offline--no-deps--require-hashes, then actualpipcheck/inventory; interpreter or uv--offline--no-sync afterward. No placeholder projectwheelhash.

M0 metadata prep6e27c37578efbd637db5a083169188f9fc0c5065, `docs/evidence/M0/cpu-training-dependency-preparation.md` SHA690d7365d543c6106d2e01c7146989882a022c8a8bd1570009d85eaaba4f6cc1. Torch2.11.0cp313macarm64+9dependencies exact10wheelclosure91095546B; metadata722898B only, no install/import/tensors. Root checked7sourcehashes/9requirements/sum. **`docs/briefs/M0-training-dependencies.md` undispatched/deferred**. When training code actually needs CPUtorch tests: authorized bounded110MiB acquisition into separate `.venv-training-cpu`, current MLX/core/dev unchanged; exact manifests already prepared, no further preparation branch.

## Evidence and recovery history

This ledger was consolidated from its exact committed predecessor **`0f47402:docs/progress.md`**; all detailed statuses/results remain in Git and their linked reports/raw artifacts. Earlier exact archived ledger `docs/history/progress-through-2190af0.md`,33880B SHA c7f84e1552c74e8bdbe2413e1e6493b40bc971c4d9b031dca85660e6867ebae0 plus JSON receipt, links older b3ce070/b516c63/840e114/8f97f83 archives. Preserve failures; do not repeat closed diagnostics after reset. Use current state above over obsolete active/pending statements in historical reports.

**Empirical status:** real historical runtime health/build/import/reset, bounded synthetic provider/runtime diagnostics, and one actual B-only discovery ran in stated scopes. No model-generated feature contract/scenarios/checker, qualified task, released solver episode, CUDA update or independent held-out result exists yet. The deliverable remains complete reviewed code and non-GPU verification, with GPU execution unverified.

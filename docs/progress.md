# Feature-RL progress and recovery

Read this first, then the current owner brief, actual interfaces and latest report/review. Both immutable user inputs were fully read: `feature_rl_pipeline.md` (architecture, SHA256954a6065c483c10bc4574945edc1523149b5968cec75ae896f788b53653beeea) and `codex_multi_agent_implementation_prompt.md` (execution). Do not stop while authorized code/review/integration remains.

## Current scope and coordination

Complete every module and integration, available non-GPU checks, and reproducible training/evaluation commands. GPU training and experimental results are deferred and explicitly unverified, not code blockers. All admission/evidence gates remain enforced. Latest user instruction: **finish current focused grading checks and confirmed fixes, then integration; consolidate evidence; handle M2 output-generation failure separately and continue without additional model calls**. No expanding administrative/dependency tooling without a concrete blocker, fabricated artifacts/approvals/results, weakened checks or silent fallback.

Workspace `/Users/mukeshreddypochamreddy/Desktop/project x`, branch `implementation/feature-rl`; no ancestor AGENTS.md found. Root owns coordination, interface decisions, review commits and integration checks. At most two disjoint product owners plus root/review slot under `docs/decisions/independent-code-slices.md`; reviewed actual dependencies, separate test state, no shared-config edits or child agents. Focused checks while owners edit, integrated checks at stable checkpoints. **git commit --only -- explicit owned paths** avoids capturing another owner's staging. Preserve `.DS_Store`, `.superpowers/` and ignored private state.

Applied skills already read: using-superpowers, brainstorming (spec approved design), writing-plans, worktrees, subagent-driven-development, dispatching-parallel-agents, TDD, requesting/receiving review, systematic-debugging, verification-before-completion, openai-docs. No goal-tool goal. User authorization supersedes routine skill confirmations. Actual human approval needs a concrete package and a human. Read `docs/briefs/review-policy.md`. No routine permission questions.

Host CPython3.13.7/macOS26.3/M4 arm64/16GiB; use `PYTHONPATH=src .venv/bin/python -m pytest -q` (unqualified python is unavailable). Docker Desktop27.4/Linuxarm64/10CPU/8217968640B. No remote compute/paid provisioning. Tool approval never; omit sandbox_permissions. Keep commentary current, waits at most60s.

| Module | Accountable owner | Current state |
| --- | --- | --- |
| M0 | `/root/m0_contracts` | Contracts/store/provenance/MLX/bounded reads reviewed and integrated |
| M1 | `/root/m1_sources` | Intake/history/splits reviewed and integrated |
| M2 | `/root/m2_authoring_recovery` | Authoring/scenarios d64f172, handoffd973d19; independent round3 ACTIVE |
| M3 | `/root/m3_runtime` | Runtime2190af0 reviewed PASS, integrated342tests PASS |
| M4 | `/root/m4_grading` | Core02aee54 focused checks complete; independent core review ACTIVE; checker finalization next |
| M5 | `/root/m5_qualification` | Signature preflight only; actual qualification implementation next |
| M6 | `/root/m6_factory` | Registryfa6a1b8 reviewed PASS; ACTIVE on BUILT root/final solver package |
| M7 | `/root/m7_learning` | Pinned compatibility audit only; agent runner, GRPO and SFT code required |
| M8 | `/root/m8_evaluation` | Corpus metadata audit only; evaluation and audit code required |

Old `/root/m2_authoring` acknowledged read-only transfer and FINAL, freeing stale capacity; never reactivate as owner. Reviews: `/root/review_m2_authoring`, `/root/review_m4_grading`, `/root/review_m6_registry`. PASS is scoped, not full-module or experimental completion.

## Immediate work

Finish bounded M2/M4 review fixes, then actual cross-module integration. M6 current packaging slice consumes actual frozen M0 and reviewed M3 inputs, no future API guesses. M5 implements actual qualification when M4 core clears; M4 completes generated-checker/finalization when M2 clears. M6 orchestration/admission/CLI and M7/M8 follow. Do not wait for new model calls or GPU execution. Root accepted M6 round2 review; no unchanged registry suite/hash rerun needed.

## M2 code and separate exhausted construction

Read `docs/interfaces-M2.md`, `docs/reports/M2-authoring.md`, authoring/review briefs and `docs/decisions/M2-proposal-finalization.md`, `M2-authoring-execution-budget.md`. Real M3 discovery, bounded B retrieval, store-backed source resolution, M0-derived proposals/finalizers, explicit scenario planning, immutable repair journals and provider/journal/final-artifact replay are implemented.

Round2 review `docs/reviews/M2-authoring-round2.md` retained B1-P1 exact recovered-result archive/cost binding, B2-P1 failed-generation publication recovery, B3-P2 valid frozen public-check joins. Same-owner fix **d64f172efb2d50ce9303725a08ed2fcfd9f4a65f**, handoff **d973d19801881b7eb1222973398abea1da4034eb**, **146focused passes1.01s**; integrated seed default is `m4-sha256-v1`, coverage described as structural ID coverage, not entailment. Same reviewer round3 ACTIVE on this delta only. No native/tokenizer/Docker/full suite in this fix. Historical137focused pass log is0.97s. Owner414full passes97.85s exceeded focused-only instructions and lacked source stability while M4 edited: not root integration evidence.

**Actual Click construction FAILED at contract generation.** One B-only M3 discovery succeeded; three contract calls consumed. No frozen contract/scenario, H feasibility authorization, checker, qualification or salvage. No further contract call.

| Call | Revision | Input/cap | Outputtokens | Wall/CPU seconds | Result |
| --- | --- | --- | --- | --- | --- |
| initial |c4ed70d|6919/9216|2469|120.085/45.626|deadline |
| repair1 |d267208|6948/9216|2520|120.113/46.228|deadline |
| repair2 |aa75086|6941/9216|1647|84.458/30.848|strict duplicate allowed_changes |

Final journal af7cee9c0af50eeddcf1a21f23b0e875964796648d20a34659e68a460806ace6; response08af74f2f8ec3a5b53e5a625de314eecb26999e92155a1a1d6c41e5125e807e8 in `.feature-rl/research/M2/authoring-production/store`. Output5573B SHA0557c089d3f53c16c46ef09b783f0cc841469b5148917e9aa7431460c9d7f49e; strict failure character5329,line142,column5; duplicate requirement IDs and ungrounded evidence also present. Exact `final-malformed-diagnostic.json` already independently checked. Root's earlier escaped-rendering interpretation as literal newline escapes was wrong; direct bytes123,10,32,32,34 corrected it. Do not repeat closed audit.

Candidate repairs3/4: prior M3neutral1 plus contract2; contract stage2/2 exhausted despite global1left. M6 owns aggregate accounting, not duplicate M2 registry. Driver preserves consumed failure. Two earlier setup failures happened before provider calls (strict ref transport; safe directory retrieval), single discovery reused after fixes.

Discovery75b5bf2609568199919c697601b92dbd0ab59a7ed192b4ec3b773058080974a0, recipefc3506e5c49a7f56da4c49f6bed326923a6e9fc8ead0300fbeeedac8cb09f960, build95609a5f73897bd0bb66eff1de654c62d064cb2efe198d8a165adba2032b489a, execution601a47a8ca7cc25a0e7bd6fab1001e3396b98f935ee333d7e5f7209ece258988. InstalledClick8.3.3, exactcommand exit0/ready,statuz exit2/no suchcommand. Request asks misspelled-subcommand suggestions, not new NoSuchCommand API.

Authoring setup ONLY `docs/evidence/M2/coordinator-authoring-input.json`, SHA6172bbb28f9cd9275db21bc47ea55af922fe3d295758f7c443881736d56aebc0. B retrieval8699B: core.py1532-1560,1878-1965; exceptions.py212-243; test_options.py136-160; test_commands.py8-41; docs/commands-and-groups.md72-100. License outside prompt. Owner had accidental SourcePair/changed-file and private H execution metadata/base64 exposures via M1 receipt and M3 run/receipt reads; exact commands disclosed in report. Owner says no H/archive/diff/history bytes decoded/read/used and prompts request+B+sanitized discovery only. **Owner not fully blinded.** No further H/private reads in M2/M4 core.

Provider e14f679 reviewedround5PASS; later recovery reviewed with authoring. MLX `mlx-community/Qwen3-4B-Instruct-2507-4bit` rev50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b,11files2278969697B, manifest697253a717e5857f1dfe3c14594f747c9c8118e6bc9c877bfc0c6faa6a7f50a0; depsd2db652d0634ff87b38ea93de0c54cb75560b209c783e6409937903a03f5a831. ALL old smokes consumed; do not rerun. Authoring fixed120s wall/CPU,4096output,1MiBIO,3.5GiBMLX/wired,cache0,4GiBsamplekill20ms+1GiBguard/5GiBdeclared. Greedy selected-model probabilities are not behavior probabilities; behavior_logprobs=None/nontraining.

## M4 focused checks completed

Product02aee54, `docs/interfaces-M4.md`, `docs/reports/M4.md`, consolidated `docs/evidence/M4/core/summary.json`. M0/M3 core: source-only submissions, clean B rebuild, fresh installed-worker probes, closed typed comparisons, complete deterministic manifests/grade ledgers, reward1/0/null, exact costs/receipt replay. `GradingService.grade`, `SubmissionService.create/from_saved`, `read_grade`. `m4-sha256-v1` includes every scenario family every seed. Only current inputs/adapter enter workers; expectations private. Mechanical BUILT grading grants no admission.

**45focused tests passed**. Real diagnostic B route **12tests passed192.71s/14grades**: baseline/shadow/state success, regression/hardcoded/earlyexit/crash/timeout/malformed/duplicate/forged/excessiveoutput zero, absent-socket null then same source+seed+manifest retry success. Eight hostile archives rejected before build. Confirmed Flit module/package conflict exposed omitted build cost: fixed, focused real regression passed6.72s with measured wall/CPU and complete not-run ledger. Two earlier invalid-build assumptions retained. Total26diagnostic grades/fivecontexts; raw refs/costs/identities in linked summary. No H/model or requested-feature qualification claim.

Stop expanding closed checks. Independent first core review ACTIVE, then integration. Same M4 owner still must implement M2-backed checker proposal/finalization and downstream qualification support. Current fixtures are diagnostic echo preservation, not the requested feature. Structural joins do not prove semantic validity.

## M6 reviewed registry and active packaging

Core9497b636548545d9fd54a1f06f1432134dc14cd6; fixesfa6a1b884365a8f89d37beaf6bc180b945d088fa. `docs/reviews/M6-registry-round2.md`: **specPASS/qualityPASS,0P0/P1/P2**. P1 result-evidence quarantine/descendant edges fixed; P2 same prospective record bounds before first SQLwrite preserve readable history on rejection. Actual36focused passes6.51s/outer6.6997825; receipt4685155b5f31de8d19bb60c2b8387a5d392fbf5dd42ca4a2cb85e3966e1fa07e. Reviewer source-bound existing RED/GREEN, no rerun; root accepted.

`docs/interfaces-M6-registry.md`: immutable typed closures, SQLite authoritative hashed events+verified JSONL projection, bounded jobs/claims/retries, cumulative actual-cost reconciliation, exact completion replay, quarantine/consumed-update trace. Process-crash evidence is not powerloss/externalexactlyonce. Historical replay and late accounting remain available after quarantine. Full orchestration/admission/CLI still due.

`docs/lifecycle-admission.md`: M6 freezes complete BUILT T0/final solver bytes,QNone before review; M5 freezes packageP; human authenticates exactT0+P+policy. AcceptedQ.task=T0. LaterTn differs only state/qualification; allother canonical fields/provenance/cost equal. Transition evidence/cost OperationResult+ledger. Released consumers resolveTn→Q→T0 + legaltransition/quarantine/revocation. No forgedstate, duplicatepackager or inventedapproval. M6 must reconcile actual M2 archives without globalatomicity claims or pretending a registry claim predated historical calls.

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

## Remaining code

M5 `docs/briefs/M5.md`: actual M4-backed Bhealth/featureabsence/H/independentalternative, semanticnegativepermandatoryrequirement plus wrong/hardcoded/regression/maliciouscontrols,3fresh+3interruptedresets, exact version/case/semanticreason joins, repairs/authenticatedreview/revocation. Signature75ed88a preflight43syntheticSSHSIGNcommands/16negative mechanisms is NO_TASK_APPROVAL. Missinghuman/controls must be explicit provisional, not codeblocker. Final M6package, no duplicatepackager.

M7 `docs/briefs/M7.md`: actual agentloop/M3savedsource/M4grade/M6admission, exactpolicycontext/tokens/masks/behaviorprobs, **GRPO and SFT/protocol-warmstart updates**, checkpoint/reload/resume.4samecaserollouts,validdenominator>=2,uniformrewardzero,oneepochclip/KL,bounded64groups+8mixedacross4taskssignal gate. CPUdiagnostics notfeaturetraining. PinnedSkyRLskyrl-v0.3.0f5bc3b78dfddfb352870d5d7430cd226e5785838,Harbor3de07a0e01f3368921766437fc7afece3ddec23d v0.13.1,59sourcefiles `.feature-rl/research/M7/`; actualCUDAstackunverified. CurrentMLXisnontraining.

M8 `docs/briefs/M8.md`: adaptationfunnel,frozenfamily/lineageexclusions,fullassignedpairedtrials,A/B/C/Dconfigs+identicalinitialpolicy/budget comparability,task/familymetrics/uncertainty/costs,weightedhumanvalidaudits/quarantineconsumedupdates. SWE-Bench++HFda364537055b9bb5091783af78a02b6a3bc0e130/harnessf938edd189049806fef7a76fdf01f0da55baa565 metadataonly,500upstreamtestrowsnotpapertrajectories,codeMIT/dataNCresearch+reporights. Norows/weightsacquired;trainingadaptationkeepsupstream_split=test/excludesrelatedlockedtest. Missingempiricaldata staysnull/unverified.

M6 later wires real construct/qualify/release/grade/run/train/evaluate/audit and README/runbook. No unimplemented CLI success. Whole-project review and final stable non-GPU verification follow all modules.

## Deferred setup, only when concretely needed

`docs/decisions/training-worker-endpoint.md`: Linuxx86 CUDA controller can use an explicitly preconfigured protected Unix socket forwarded to qualified arm64 Docker daemon; CLI+HTTP same endpoint, API1.47. No automatic SSH/provisioning. Actual forwarding/qualification unverified; expose socket in commands, don't assume local x86 daemon works.

`docs/decisions/training-dependency-overlay.md`: SkyRLlockPydantic2.13.4/core2.46.4 differs from project2.13.5/core2.46.5. Two-package exact hashed overlay is source-compatible, actual full stack unverified. Preserve upstream CUDA-specific wheels/sources; generic PyPIvLLM can select wrong CUDA. Install overlay offline--no-deps--require-hashes, then actualpipcheck/inventory; interpreter or uv--offline--no-sync afterward. No placeholder projectwheelhash.

M0 metadata prep6e27c37578efbd637db5a083169188f9fc0c5065, `docs/evidence/M0/cpu-training-dependency-preparation.md` SHA690d7365d543c6106d2e01c7146989882a022c8a8bd1570009d85eaaba4f6cc1. Torch2.11.0cp313macarm64+9dependencies exact10wheelclosure91095546B; metadata722898B only, no install/import/tensors. Root checked7sourcehashes/9requirements/sum. **`docs/briefs/M0-training-dependencies.md` undispatched/deferred**. When training code actually needs CPUtorch tests: authorized bounded110MiB acquisition into separate `.venv-training-cpu`, current MLX/core/dev unchanged; exact manifests already prepared, no further preparation branch.

## Evidence recovery and stopping rule

This consolidation replaces stale pending passages from exact predecessor `d973d19:docs/progress.md`; detailed failures/evidence remain in Git and linked reports. Earlier exact `0f47402:docs/progress.md` and `docs/history/progress-through-2190af0.md` (33880B SHA c7f84e1552c74e8bdbe2413e1e6493b40bc971c4d9b031dca85660e6867ebae0) preserve older recovery links. No evidence deleted. Temporary unpublished725de10 split preservingbytes intoM2d267208/M69497b63 aftersharedindexcapture: neveranchor725de10. M2hash typo correctedf010272; actual7b8974637e9ffc5bab79f9590b90c7021228d473. Derive identities mechanically.

Historical runtimehealth/build/import/reset, realBdiscovery and gradingdiagnostics ran in statedscopes. Actualfeatureconstruction failedatM2 exhaustedcontractstage. No frozenmodelcontract/scenario/checker,humanqualifiedtask,releasedsolverepisode,GPUupdate or heldoutresult exists. This does not block remainingcode. Do not finalize until all authorized code,review,integration and nonGPUchecks/commands are complete.

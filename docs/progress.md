# Feature-RL progress and recovery

Read this ledger, the active brief, actual upstream code/interfaces and latest report/review before dispatch. Both immutable inputs were read in full: `feature_rl_pipeline.md` (architectural authority) and `codex_multi_agent_implementation_prompt.md` (execution authority). Earlier detailed ledgers are preserved exactly, with adjacent JSON hash receipts:
- `docs/history/progress-through-b3ce070.md`
- `docs/history/progress-through-b516c63.md`, SHA256 `83b84ef740a093cadf9c7e363c0b4293b6f0eb942d6b44aaf2a925ebd731b6ef`
- `docs/history/progress-through-840e114.md`, 34402 bytes, SHA256 `44d62d9333d4460e554ffadd65688ee5262467b572e09c0359253c16a4583e5b`
Those contain all prior failures, superseded receipts and research details. Active-state statements here supersede their pending-state text; no evidence is deleted or relabeled.

## Authorization and operating rules

User: **No remote compute configured; finish all independent local work.** Continue actual implementation/review/integration, not planning or scaffolding. No paid provisioning or inferred compute budget. Do not finalize while independent local product work remains.

Workspace `/Users/mukeshreddypochamreddy/Desktop/project x`, branch `implementation/feature-rl`. Initial workspace held only the two inputs; no ancestor AGENTS.md. Root coordinates interfaces, scheduling, briefs, integration diagnostics, progress and plan. One product implementer edits at a time; same module owner handles fixes. Independent read-only review/research may run alongside. Four concurrency slots include root; no child spawning. Commit explicit owned paths with `git commit --only`. Preserve unrelated `.DS_Store` files and untracked `.superpowers/` review packages. Shared files/index are immediately visible to all agents.

Skills already applied/read: using-superpowers, brainstorming (approved supplied spec), writing-plans, worktrees, subagent-driven-development, TDD, requesting/receiving review, systematic-debugging, verification-before-completion, openai-docs. User's continuous execution and same-owner requirements take priority over skill approval/escalation suggestions. Current replacement M2 owner uses sol/high under skill model-selection guidance. No goal-tool goal was created. Tool execution must omit sandbox_permissions; approval policy never. Keep commentary current, waits no more than60s. Collaboration tools are direct calls.

Project checks: `PYTHONPATH=src .venv/bin/python -m pytest -q`. CPython3.13.7/macOS26.3/AppleM4 arm64/16GiB. Mac hides editable .pth; explicit PYTHONPATH required, normal built-wheel import independently verified. Package Python>=3.11. Current owner234 tests are not a fresh root suite; root's last full suite was155 at M1 integration.

Historical/candidate/generated source, adapters and build hooks execute only inside M3 Docker workers. Controller/host never imports them. Initial model draft gets request+B/public evidence only; freeze before privileged H review. No learner H/private cases/histories/future Git/solution caches. Ordinary limits grade saved source; candidate failure may be valid0, infra/corrupt trajectory null/nontraining. Human attestation cannot be supplied by an agent. Max2 repairs/stage and4/candidate; engineering provider fixes are not candidate attempts (none yet).

## Owners and status

| Module | Accountable owner | Current state / next gate |
| --- | --- | --- |
| M0 | `/root/m0_contracts` | Core, provenancev2 and optional MLX config reviewed/integrated; owns shared changes |
| M1 | `/root/m1_sources` | Reviewed/integrated reconstruction scope; Click task provisional |
| M2 | `/root/m2_authoring_recovery` | Round5 producte14f679 committed;98/253 owner tests; independent re-review next |
| M3 | `/root/m3_runtime` | Preparation reviewed; availability confirmed; actual runtime after provider gate |
| M4 | Unassigned (`m4_grading` planned) | Brief ready; no product |
| M5 | `/root/m5_qualification` | Signature mechanism preflight only; no product |
| M6 | `/root/m6_factory` | Durability preflight only; no product |
| M7 | `/root/m7_learning` | Pinned compatibility source audit only; no runner/training product |
| M8 | `/root/m8_evaluation` | External-corpus metadata audit only; no evaluation product |

Reviews: M0 slices PASS (`review_m0`), M1 round2 PASS (`review_m1`), M3 preparation PASS (`review_m3_preparation`); future product slices require their own independent verdicts. Current reviewer `/root/review_m2_provider` FINAL round4 FAIL. Dormant-agent send_message can produce pending_init/thread-limit occupancy; recover through a bounded availability followup asking FINAL, as already done for M3. Do not simulate delegation or repeatedly spawn at capacity.

## Active M2 handoff and next coordinator gate

**Current handoff:** owner FINAL round5 product **e14f67983db678813afcfeb191532e691b0b20e3**; final98 provider /253 full tests exit0 at2026-09-19T16:37:47.982782–16:38:08.148553UTC, no native/remote/download/install/historical calls. `provider-v3-fixround5-verification.json` preserves three RED runs, prior94/249 pre-extra logs and corrected final logs. Root mechanically matched24 source/doc/log identities against receipt and committed product (`coordinator-round5-binding.json`). Complete product/test/doc diff `.superpowers/sdd/implementation-plan/review-840e114..e14f679-M2-product.diff`,40451B,SHA9df907c52ffc5a44606663ef871d9dda8f92d903d4aada5ed17747cff88faef1; complete four-commit range also retained296911B. Independent same reviewer now receives final changed scope including R3/R4, Decimal and extra-allow boundaries. No product implementer edits while review runs. Later root suite/native remain unexecuted.

Assigned base **840e11407d6c0c15548678fef2f2102dd58d100e**; product under repair **44e9dcf1079a186a8a0432fa51333d1d07f6422f**. Same owner dispatched to `docs/briefs/M2-provider-round5.md`. Read final `docs/reviews/M2-provider-round4.md`, both full-provider reproducers under `docs/evidence/M2/review-round4/`, and `docs/decisions/M2-literal-validation.md`. Owner has exclusive generation/**, generation tests, M2 interface/report/new owner evidence; root coordinator-* / transfer evidence and briefs excluded. No native/load/download/install/remote/historical execution authorized in this fix. Required final mechanical source/doc/log/UTC/exit receipt, owned commit and FINAL.

Round4 specFAIL / qualityCHANGES REQUIRED, twoP2 findings (no scoped P0/P1):
- R1 scalar/model/nested union coercion is closed; R2 ID/recovery bounds remain closed.
- **R3** admitted after callbacks affect later validation. Chain str-after-int then int and list PydanticOmit with max_length1 both ordinary-JSON valid, but stripped callbacks falsely reject after a backend/runner call.
- **R4** inert model equality/hash differs from restored frozen models. `set[Leaf]`/`frozenset[Leaf]`, min_length2, two equal objects pass inert validation then collapse to size1; provider archives success and accepted usage, ordinary validation rejects. Distinct-object positives pass. Reviewer full-provider + real-store evidence confirms both.

Coordinator direction: actual M0 all46 Literal fields are strings/string-enums; numeric versions/rewards/bools use strict primitives (`docs/evidence/M0/literal-type-analysis.md/json`,92 cases, commitbae671a). Preserve original strict JSON validation where no numeric/Boolean Literal workaround is needed. For that workaround admit a small callback-free structural subset; refuse unsupported lifecycle/hash-sensitive forms before backend verification, preserving exact scalar/model/nested alternatives and patterned string-key maps. Do not approximate arbitrary callbacks or add an unrestricted second pass that reopens R1. Schema capability refusal is typed/attributable, not a relaxed task check or malformed response after execution. No task frozen.

Round5 in-progress coordinator diagnostic at provider working SHA6b246acc9372a281dbe150868c6ee2d98e272a0c7a249085d5240fab7518bebe found another admission edge: Literal[Decimal(1)] bypassed numeric classification, while ordinary JSON accepts1/1.0/true despite advertising string const. `coordinator-round5-literal-domain.py/json/stdout/stderr` retains exact trusted schema results at16:32:41UTC (exit0 means observation, no provider/runner/native call). Owner acknowledged and is adding explicit pre-execution refusal for unsupported Literal value domains. This does not expand generic capability or affect M0.

Round5 subsequent restoration check at working providerSHAf672d0e0ff4867aea11e0cd21002193a10427f32e7c56d66249655abe0c1181c found nested extra-allow models fail after admitted validation: ordinaryJSON retains item.y=2; inert fields_set includes y but direct attribute access raisesAttributeError. `coordinator-round5-model-extras.py/json/stdout/stderr` records16:35:57UTC/source unchanged/exit0/no provider or native calls. Owner will refuse extra-allow models in numeric guarded schemas, covering nested/root overrides and preserving ignore/forbid controls. Owner94/249 preliminary logs are explicitly pre-extra and not final round5 evidence.

Retain this available replacement owner under user's same-owner rule. Prior transfer was repeated service-capacity failure, not deliberate replacement for review findings. Transfer captured all seven uncommitted files and explicit brief/code/evidence at5cdd773 (`docs/briefs/M2-owner-transfer.md`, `owner-transfer-round3.diff/json`, diffSHA2ec8d04a14d8db27909a54729e5497b949b47f3e0218efef4a7aa337a04fb33a). Old owner notified read-only. Cost of retaining current owner if wrong: more rework; independent review continues to block unsound code.

Prior provider checkpoints (all immutable evidence retained):
- v2 8a463755:16focused171full; review five findings cleanup/JSON transport/identity/cost recovery/usage flag.
- Fix1 7239379:46/201, protocolv3; review numeric Literal coercion + oversized rejection accounting.
- Fix2 3f859ca:58/213; review union/patterned-map branch mismatch + unbounded IDs.
- Fix3 20af743:66/221; round3 closed R2, remaining scalar coercion and lifecycle semantics. Root caught stale/manual hash receipt before handoff; final mechanically generated12 identities match. Earlier220 count superseded.
- Fix4 44e9dcf:79/234, UTC2026-09-19T16:03:08.632333–16:03:28.203867; zero native. Final `provider-v3-fixround4-verification.json`, root12 identity matches `coordinator-round4-binding.json`. Pure graphs direct restore; callback branch original JSON once closed root ValidationInfo.mode issue; review R3/R4 above. Package `.superpowers/sdd/implementation-plan/review-c9bbd8f..44e9dcf.diff`,49178B,SHA4a6ab4f471799b63442c273ae7e9890894552f66d914d51af1e395ae53117a26.

After owner FINAL: complete scoped independent review using actual diff/evidence; substantive findings return same owner. After PASS root reconciles and executes **fresh full suite then exactly one current-source native call** using prepared unexecuted wrapper:
```
PYTHONPATH=src .venv/bin/python docs/evidence/M2/coordinator-provider-gate.py --product-revision FINAL_PRODUCT_SHA --review-report docs/reviews/M2-provider-roundN.md
```
Wrapper syntax-parsed only, creates exclusive coordinator-provider-gate.json plus stdout/stderr; suite failure stops before inference. Native driver `coordinator-v3-native.py` syntax-parsed only; permanent ignored one-call claim `.feature-rl/research/M2/coordinator-v3-native-claim.json` not created. It publishes actual synthetic M0 request-snapshot source, fresh nonce/BLUE enum/tuple tags, binds reviewed product/source/provenance/11 archives, exact token IDs/scores, cost and HOME/cache cleanup. No inference retry after failure without declared diagnosis/budget. Old coordinator-v3-binding hardcodes723 and older mixed-union script diagnoses old two-pass design; preserve, don't relabel as current evidence. After gate success dispatch actual M3; M2 remains partial until authoring/scenarios.

Provider stable API: LocalGenerationProvider(backend=BackendConfig(...),archive=store.put_bytes,runner=optionalBoundedProcessRunner).generate(request,StrictModelSubclass); GenerationResult.content/usage/cost/record. GenerationProviderError.record/cost/response/usage_observation/recovery/replay_publication. Write-only archives require downstream authoring to resolve source/quote/locator/provenance/frozen joins.11 normal archives: attempt/request/response/retrieval/schema/options/provenance/usage/cost/events/status. Oversize preflight bounded attempt/preflight/cost/status; max128ASCII IDs and forged-instance revalidation; unknown costs remain unknown; publication retry never re-infers.

Native selected backend: mlx-community/Qwen3-4B-Instruct-2507-4bit revision50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b,11 files2278969697B. Model under `.feature-rl/research/M2/model/mlx-community--Qwen3-4B-Instruct-2507-4bit/<revision>`, acquisitionmanifestSHA697253a717e5857f1dfe3c14594f747c9c8118e6bc9c877bfc0c6faa6a7f50a0. WeightsSHA2a73c6c248601ab904e035548abd8e6abb65ea27dcb5f342fb0a8910eb44173f; tokenizerSHAaeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4. Dependency manifestSHA d2db652d0634ff87b38ea93de0c54cb75560b209c783e6409937903a03f5a831. Exact allowlists, no custom/remote code, Python-I offline fresh caches/HOME preserved. Greedy selected_model_logprobs are not behavior-policy probabilities; behavior_logprobs=None, nontraining.

Budget: owner two native calls USED; no more owner calls. One coordinator call remains <=2048 actual input,128 emitted,120s wall/CPU,1MiB stdin/output/file.3.5GiB MLX allocator/wired guideline/cache0,4GiB physical-footprint kill sampled20ms with1GiB guard; observation failure rejects, no zero-transient5GiB claim. Larger envelopes explicitly unqualified;262144 architectural tokens not host measurement. Research and two v1 smokes remain historical only: CodexCLI cap ineffective/disqualified; MLX research26/16 truncated, oversizedpreload rejection,64MiB killcanary; two v1 prod383/83 EOS BLUE/M2-PROD-1 at5.6719/5.8112wall,3.6368/4.0428CPU with cleanup. Later source corrected HOME/inputIDs/raw scores/cost publication. Old UTC/source gaps not retroactively repaired.

## Completed upstream interfaces and artifacts

M0 core cb53f99 independentlyPASS; provenancev2 e16493b PASS; optional MLX config18bd8f34ad531ad7570ba1942e45bf9eaca518c5 PASS. Core/dev12 versions unchanged; opt-in34 wheels96889626B, MLX/Metal0.32.2,MLXLM0.31.3,Transformers5.17.0,NumPy2.5.3. Offline hash install/pipcheck/nativeimports155tests and root65 asset/config checks completed; no inference in those checks. Requirements lock files and manifests authoritative. Pydantic2.13.5/core2.46.5,pytest9.1.1,hatchling1.32.3.

M0 ArtifactStore(root,role).put_artifact/get_artifact/put_bytes/get_bytes, canonical SHA metadata+payload envelope, immutable atomic fsync/hardlink, O_NOFOLLOW/descriptor-relative access. Solver public; author public/authoring; controller all; trainer public/training; evaluator public/evaluation; reviewer readall/no writes. Raw bytes base64 JSON, not multigig weight blobs. StrictModel frozen/revalidate; HumanReview model is a claim only. CandidateRecord/SourcePair v2 mandatory provenance_label; SourceSnapshot explicit redirect_chain tuple/null; other nine typed/rawv1. Read actual models/docs/interfaces.md.

M1 producta658ae4d0ffba45d1fbde6c4edad71da33ea570e, M1-round2 reviewPASS; root155 tests17.294s and two real repeated Click ingestions2026-09-19T09:35:26.709993Z ~1.96/1.99s exit0. Artifact store `.feature-rl/research/M1/production-store`, git readonly `.feature-rl/research/M1/git/click.git`. Clickingestion1/2 outputSHAdeca99ae7076bef8636a8b755832ad6a8df2ab9b91503f1bf9e0a6cadb0b2ea7, Git inventory unchanged20files505392B.

First provisional feature ClickPR3228/issue3107 command-name suggestions. B19fd4d6e18bc9fce451f92f422696b11169faa57; H831c8f0948af519e45b90801d7430ff25451f972. B-rooted squash ancestry/source-head-tree proof archived; conservative topology scope. Request historical timing unproved => reconstructed_specification;24redirectsNone. Mixed exceptions.py/parser.py/test_options.py changes need privileged/human review later.

Exact refs from docs/evidence/M1/coordinator-round2-intake-1.json (do not infer kind):
- CandidateRecordv2 b0d7cfaa9d81aa1adbbdf6b76056c198b8f3250bd6e2fd40a79f1b604e6134d4
- SourcePairv2 74d7e0ee98012c0fffab451e1664a4446ffdec1ce1fa38ec406e554d3456045b
- B source-archive4ff1d8a5d478ffb12950ec2661f3b38be5b7c9925691726c27b83234c5814575 AUTHORING
- request authoring-request d8d8a97861a597c7bfe181dfd3bbd6dbf72b7854ee13d555add1b1e07f0d2c61 AUTHORING
- license source-response b84ace5d4d01f55ab2db4e25ffddb9239104176e99a73e4799dcadbd8e612ab9 AUTHORING
- H source-archivee4f10101549a77916c1898d3a4ed9ccc43dddbe1e6aa5c6c7cdcc896a232679c PRIVATE, excluded initial draft.
All rawv1. Prior guessed request-snapshot failed correct metadata checking; root caller error, not M1 defect.

## M3 next product and later independent preparations

M3 owner availability confirmed with tools; `docs/briefs/M3.md` and independent `docs/reviews/M3-preparation.md` mandatory. **No M3 product and no historical B/H executed yet.** DockerDesktop27.4/Linuxarm64 initially8.2GB/10CPU, remeasure capacity before product runs. Never alter unrelated containers/images/volumes or prune. Prepared Python3.12.14 platform image `python@sha256:eb5be8e5b4d0a159c237946bbdd06356dda5d19c30fc4f7843e8046d3a590333`; seccompSHA005f6ae1a0f3f9d1a0c044f83289e2ea54180c97a105b2539422588eac2fde44. Explicit profile required (daemon default unconfined), nonroot65534, capsdropall,nnp,privatePID/IPC/cgroup/mount/network,networknone,readonlyroot,boundednoexectmpfs,pids32,.5CPU,128Mnoswap,logsnone in research.11 trusted research attempts removed/absence verified, network/DNS/AFVSOCK/AFALG/ptrace/fork/disk/CPU/OOM/time/output denials observed; no source execution or production proof.

Six inert purepy wheels1753266B `.feature-rl/research/M3/dependencies`: pytest9.0.2,iniconfig2.3.0,packaging26.0,pluggy1.6.0,pygments2.20.0,flit_core3.11.0. First5 Buvlock; flit neutral reconstruction>=3.11<4, release2025-02-19.13requests incl failedobserver/repeats retained. Offlinepiptargetdeps; build each B/H/candidate own flit wheel offline/nobuildisolation, prove importpaths/controller source identities. Host Click8.5.0 irrelevant. Faithful baseline pytest-v--tbshort--basetemp; no suppressed warnings/assertions or fulltox claim.

Five M3 production reuse gates: bound entire stdin/execution/export/cleanup (EOF not exit); persist owned identity before launch and distinguish daemonerror from absence; machine-check denials; preserve source on ordinary limits via boundedfreeze/export or exact last-saved policy before destroy; actual offline staging/build/import/source bindings. `click.__file__` inside candidate is diagnostic only. Need actual Bhealth/Himports/3fresh3reset plus limits/no-orphans. Local services unsupported until actual isolation/reset; no service success placeholder.

Lifecycle/admission decision `docs/lifecycle-admission.md`: M6 owns complete built frozen T0 incl real solver view, stateBUILT/QNone. Human signature binds T0 plus frozen evidence packageP/policy/challenge. Q points exactT0; admittedTn differs only state/qualification; nested payload/provenance/costs equalT0. Transition costs OperationResult/M6ledger. Consumers resolveTn->Q->T0 plus revocation/quarantine/legaltransitions. M5 code may review beforeM6; actual qualification awaits M6 real package then sameM5 closes integration. No duplicate packager/placeholders.

M5 preflight75ed88a677a84d38a5b0244846008f045fc46c65: docs/evidence/M5/attestation-preflight.md, rawSHAced28e788d10d4a72e3f75c525fe4ea0b4a12d3d52f60f1f1d1c6f6a539a8d5e. Apple ssh-keygen10.2p1 syntheticNO_TASK_APPROVAL/example.invalid,43commands2.286s; valid0,16negatives255,identicalreplay0,check-novalidateunallowedkey0. Newignored0600diagnostickeys only, no human signing/enrollment. Owner323/root174 checks. Actual M5 needs external trust enrollment, exact reviewed tuple, replay/revocation; cryptography alone not human proof.

M6 preflight8b68d99bd7bf07b381f50e74ee84f7fe994b89fd: docs/evidence/M6/durability-preflight.md, rawSHA68871473cbf6010e4c080639860f9ae6d558b48021f0ea9350cce27f0b76f7b4,338213B. SQLite3.50.4/APFSDELETE/EXTRA/fullfsync,169assertions/16publicationpositions/3corruptcases/19children,2.770sdriver (2.817souter overlaps). ImmutableSQLiteevent/outbox/index authority, verifiedJSONLprojection watermark<=durableprefix. Partialappend recovery, lostackidempotence, distinctcosts and missingcostunknown; corruptsuffix/missingackbytes preservedblocked. Owner296/root9static checks, no powerloss/IOfault/diskfull/concurrency/pathattack/product proof. `.feature-rl/research/M6/durability-preflight-20260919` retained. Reconcile actual M2 internally publishes archives before generate returns; no assumed prototype upstreamatomicity.

M7 docs/evidence/M7/compatibility.md,59sourcehashes rootverified: SkyRLskyrl-v0.3.0 f5bc3b78dfddfb352870d5d7430cd226e5785838, Harbor3de07a0e01f3368921766437fc7afece3ddec23d v0.13.1. Linuxx86_64/Python3.12CUDA proposed Torch2.11cu128/vLLM.23cu129 patchedwheels SOURCE ONLY. Stockfake0/retry/limit/seed/oldGRPOdenominator/arbitrarytransfer/stalesync/checkpoint gaps need project replacement. No compatible stack imports/runner/update command yet. Max64signal groups,8mixed across4tasks; no CPU/MLX substitute for CUDA featuretraining.

M8 docs/evidence/M8/baseline-feasibility.md,15response/3pin-main rootchecks: SWE-Bench++ candidateC HFda364537055b9bb5091783af78a02b6a3bc0e130, harnessf938edd189049806fef7a76fdf01f0da55baa565.500publicupstreamtestrows notpapertrainingtrajectories; codeMIT,dataNCresearch/education and repository rights later. No rows/solutions/repos/models acquired. Authorized local trainassignment preservesupstream_split=test, excludesrelatedfamilieslockedtest; no invented blanketpermissiongate/leaderboardclaim. Live metadata timestampednotrevisionbound.

## Remaining execution and external gates

1. M2 correction/review/fresh root suite+one native gate. M3 actual runtime/build/lifecycle + independent review.
2. Same M2 owner B-only discovery/modeldraft/freeze then separate privileged H review/scenarios; actual generation budget declared from measurements, no handmade replacement. Independent review.
3. M4 actual adapters/source/rebuild/controller/private grading/B/H checks; M5 real semantic omissions per mandatory requirement/plausiblewrong/hardcoded/regression/forgery controls, blindalternative,3fresh3reset, humanpackage/code; M6 actual T0/factoryrequest-to-construction/grading; M5 resolves qualification through sameowner.
4. M7 real agentloop on released task; M6 integration. Missing human blocks real release/episode but finish independent code. Pilot~20 onlyafter firstpathusable, no broadening to evade blocked firstfeature.
5. M7 real pinned trainingadapter/localdiagnostics and future CUDA command; M8 actual evaluation/audit/full assigneddenominators/pairedfamilyuncertainty/A-B-C-Dconfig with qualifieddata; M6 actualCLI/runbook. Independent full integratedreview and fresh rootverification.

**CUDA execution blocked:** no Linux NVIDIA host/budget, latest user confirmsnone. Finish runnable code/precise command; no simulatedoptimizer/trainingclaim.
**Human qualification external:** no concrete task/controlpackage yet; prepare it first, then actual human review. Models cannot sign. Blindalternative is independent work, not automatically external.
**Empirical status:** no actual featurecontract/scenario/checker/qualification/reward/solverepisode/optimizerupdate/heldoutcomparison exists. M0/M1 and narrowly described provider/sandbox/preflights are their own evidence. No full module/project/mainstudy completion claim.

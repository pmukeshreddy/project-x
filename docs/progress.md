# SDD ledger — plan: docs/implementation-plan.md

Recovery: read this file, docs/interfaces.md if present, latest reports and git log before dispatching work. The two root input documents are immutable authority.

## Current state

- Workspace inspected: two user Markdown files only; no prior Git repository or AGENTS.md in ancestor directories.
- Both supplied documents read in full. M0 implemented/reviewed; later implementation and empirical gates remain open as listed below.
- Git initialized on `implementation/feature-rl`; sequential module edits will use the fresh project directory.
- Owner map and execution order: docs/implementation-plan.md. Owner agent IDs recorded below on dispatch.
- Preflight `/root/execution_preflight` complete (no product ownership).

| Module | Accountable owner | Latest state | Next gate |
| --- | --- | --- | --- |
| M0 | `/root/m0_contracts` | Implemented, independently reviewed, 103 tests at cb53f99 | Own downstream schema/config fixes |
| M1 | `/root/m1_sources` | Implemented at 3976698; 119 tests and real cached intake reproduced | Independent reviewer `/root/review_m1` active |
| M2 | `/root/m2_authoring` | Real provider protocol probe passed after one schema repair; no product yet | M1 review, provider implementation/review, later runtime-backed authoring |
| M3 | `/root/m3_runtime` | Trusted sandbox probe complete, no product code | M2 provider review then lifecycle/baseline implementation |
| M4 | Unassigned | Scoped brief prepared | Reviewed M2/M3 |
| M5 | Unassigned | Scoped brief prepared | Reviewed M4 and real controls |
| M6 | Unassigned | Scoped brief prepared | Reviewed M0-M5 |
| M7 | Unassigned | Scoped brief prepared; CUDA execution externally blocked | Real runner, training code/diagnostics; no paid provisioning |
| M8 | Unassigned | Scoped brief prepared | Reviewed upstream, independent data/checkpoints for empirical runs |

## Decisions

- User explicitly approved architectural scope and continuous real-subagent execution. Use supplied spec rather than reopen design/plan approval.
- Work in the empty project directory on an implementation branch. No unrelated code exists to isolate.
- Preserve ledger/reviews/evidence indefinitely as requested; do not follow skill scratch-directory deletion guidance.
- Python package uses `src/feature_rl/` consistently. Product code belongs to module agents, coordinator owns interfaces/scheduling/integration.

## Interface preflight

| Producers → consumers | Contract / overlap | Resolution |
| --- | --- | --- |
| M0 → all | Models, content IDs, errors/config, storage | M0 establishes actual documented API before downstream dispatch |
| M1 → M2/M3/M6/M8 | Split sources and B/H archives | Read-only source snapshots; authoring has B only |
| M2 provider → M3; M3 → M2 scenarios | Provider precedes runtime; discovery needs runtime | Split M2 into two reviewed slices, same owner |
| M2/M3 → M4 | Scenarios, adapters, bounded workers | Runtime invocation stays in worker; expectations stay controller |
| M4 → M5/M6/M7/M8 | Trusted grade receipt | Same grade path across qualification, rollout and evaluation |
| M5 → M6 | Qualification evidence | Missing human/alternative evidence forbids release |
| M6 ↔ M7/M8 | CLI/library operations | M6 alone edits CLI; M0 alone edits dependencies/config |
| M0-M8 self-consistency | Own product/test paths and scoped reports | Nonoverlapping except documented coordinator-approved interface files |

## Checkpoints

- Planning baseline committed `767e790`.
- Task 1 / M0: owner `/root/m0_contracts`, base `767e790`, implementation checkpoint `313a463`, review pending. Brief `docs/briefs/M0.md`; report `docs/reports/M0.md`; 65 tests and wheel import recorded in `docs/evidence/M0/verification.json`. Shared contracts not yet approved for downstream use.
- Preflight `/root/execution_preflight` complete: `docs/evidence/preflight.md`. Actual authenticated-home configuration was checked using a loopback capture; zero tool definitions after disabling request-user-input, no inference performed. Earlier empty-CODEX_HOME probe was explicitly corrected as nonrepresentative and must not be reused. Do not repurpose HOME/CODEX_HOME.
- M0 independent reviewer `/root/review_m0`: changes required, report `docs/reviews/M0.md`. Open: author-visible reference solution; invalid trajectory marked trainable; zero-measurement evaluation success. Owner fix round 1 pending.
- M0 fix round 1 checkpoint `583d963`: 99 tests reported. Coordinator found new stop-status regression before re-review: limit/malformed-action stops forced reward zero despite spec requiring grading the available artifact (which may pass). Returned to owner for supplemental focused fix; not approved.
- Task 1: complete (commits `767e790..cb53f99`, independent review clean). Fix round 1: all three original findings addressed plus root-discovered ordinary-stop regression corrected. `docs/reviews/M0-round1.md` passes spec/quality; coordinator independently ran 103 tests, exit0, at `cb53f99`. M0 owner remains `/root/m0_contracts` for shared interfaces/config changes.
- Task 2 / M1 implementation authorized against `cb53f99`; owner `/root/m1_sources`. Read-only proof recovered B `19fd4d6e18bc9fce451f92f422696b11169faa57`, H `831c8f0948af519e45b90801d7430ff25451f972`, squash proof with identical PR-head/H trees. Provenance remains reconstructed specification due unavailable historical body-edit proof. Actual implemented intake/reconstruction and review still pending.
- M1 actual dispatch base is `bd1361f` (docs-only advance after M0 `cb53f99`).
- M1 checkpoint `3976698` includes product commits `6e269cd` and `2c801f6`, report/interfaces and real Click evidence. Independent reviewer `/root/review_m1` is reviewing the full `bd1361f..3976698` diff; no product changes pending. Coordinator independently ran 119 tests (exit 0) and the cached real-intake command (exit 0), reproducing the candidate/source pair and idempotency. Exact receipts: `docs/evidence/M1/coordinator-verification.json`. Candidate remains provisional for three mixed-purpose paths; no qualification or runtime claim.
- M3 owner `/root/m3_runtime` assigned bounded trusted Docker/image/security probe research only; no product or historical execution yet. Report `docs/evidence/M3/investigation.md`; implementation awaits M1/M2 provider gates. Existing user Docker assets must remain untouched.
- M3 trusted investigation completed: explicit seccomp/nonroot/caps/namespaces/filesystem/network/PID/CPU/memory/tmpfs/timeout/output controls exercised; all 11 owned containers removed. Python arm64 pin `python@sha256:eb5be8e5b4d0a159c237946bbdd06356dda5d19c30fc4f7843e8046d3a590333` (3.12.14) and applied seccomp hash `005f6ae1a0f3f9d1a0c044f83289e2ea54180c97a105b2539422588eac2fde44`. Raw receipts/report in docs/evidence/M3. This is not M3 production implementation or baseline execution.
- M2 owner `/root/m2_authoring` assigned one bounded tiny real configured-subscription provider probe only; no product or Click contract yet. Report docs/evidence/M2/investigation.md. Implementation waits for M1 reviewed checkpoint.
- M2 probe actual result: gpt-5.6-luna, Codex0.154.0, tool-free configuration, first HTTP400 invalid JSON Schema then one typed-schema repair; second call exit0 in3.118s, input5821/output25 tokens, no forbidden events. Backend weight revision and billed USD not exposed and remain unknown. Docs/evidence/M2 contains full traces/config/status. This is provider feasibility, not feature construction or training.
- M1 owner `/root/m1_sources` assigned bounded read-only Click source investigation only, report `docs/evidence/M1/investigation.md`. Product implementation awaits approved M0.

## External gates

- No authoring API-key presence detected in environment. The existing authenticated Codex subscription route passed a bounded tool-free structured-output probe; product adapter implementation remains pending.
- Docker Desktop was stopped. Coordinator executed `open -a Docker` (exit 0); server is `27.4.0`. M3's explicit trusted isolation probe passed as scoped in `docs/evidence/M3/investigation.md`; production lifecycle and historical baseline remain unverified until implemented.
- Host Apple M4, 16 GiB unified RAM; Docker allocation about 8.2 GB. No CUDA runtime/GPU. User explicitly confirmed no remote compute configured and instructed finishing all independent local work. Do not provision paid compute; CUDA SkyRL/vLLM execution is blocked.
- Pilot human task review must be an actual human record; model code reviewers cannot sign it.

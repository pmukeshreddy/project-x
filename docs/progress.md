# SDD ledger — plan: docs/implementation-plan.md

Recovery: read this file, docs/interfaces.md if present, latest reports and git log before dispatching work. The two root input documents are immutable authority.

## Current state

- Workspace inspected: two user Markdown files only; no prior Git repository or AGENTS.md in ancestor directories.
- Both supplied documents read in full. All implementation and empirical gates pending.
- Git initialized on `implementation/feature-rl`; sequential module edits will use the fresh project directory.
- Owner map and execution order: docs/implementation-plan.md. Owner agent IDs recorded below on dispatch.
- Read-only preflight assigned `/root/execution_preflight` (no product ownership).

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

- Planning baseline: pending commit; M0 not yet dispatched.

## External gates

- No authoring API-key presence detected in environment. Configured CLI/provider availability under investigation.
- Darwin arm64 host; Docker CLI present; daemon/isolation availability under investigation.
- No CUDA device configuration detected; training compute gate under investigation.
- Pilot human task review must be an actual human record; model code reviewers cannot sign it.

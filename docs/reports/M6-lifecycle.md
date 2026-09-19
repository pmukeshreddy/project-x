# M6 lifecycle and released-task resolver checkpoint

`TaskLifecycle.resolve_released(ArtifactRef) -> TaskBundle` is implemented against
the actual M5 `QualificationService.verify_accepted` method. It verifies the whole
frozen T0 payload, exact Q, selected completed Registry transitions and current
quarantine/revocation. `qualify(T0,Q)` and `release(T1)` implement only the approved
BUILT→QUALIFIED→RELEASED sequence. No callback admission, second ledger, new solver
packager or state-string shortcut was introduced. Calibration remains separate.

Frozen transition receipts preserve original target bytes, attempt identity and
costs across publication failure. Opaque configuration/receipt dependencies are
declared explicitly. Current M5 admission is rechecked before pending successful
publication completes; revoked evidence cannot finish the transition. Incomplete
pre-freeze attempts retain unknown costs and require explicit reconciliation.

Focused checks: **37 passed in 26.00 seconds**, exit 0: 18 new lifecycle cases and
19 affected existing builder cases. The first test run failed all 15 then-defined
cases because the API was absent. The [receipt](../evidence/M6/lifecycle/receipt.json)
binds actual source hashes, observed Git revision, command and timestamps; full
[output](../evidence/M6/lifecycle/focused.txt) is retained.

Cases cover actual M5 missing-human denial; exact payload preservation; forged state
and unselected CAS; metadata drift; built/report/receipt/configuration quarantine
tracing; current revocation; idempotent reads/transitions; target publication outage;
lost CAS reply and exact retry; wrong retry configuration; unknown pre-freeze work;
and revocation during frozen recovery. Positive transition mechanics use a clearly
labeled TEST-ONLY substitution of M5's method in temporary state. No accepted
QualificationReport, HumanReview, signature, native execution, model call or actual
released task was created. These are mechanism checks, not human admission evidence.

The exact callable [interface](../interfaces-M6.md) unblocks runner wiring. Full
Factory authoring/control/qualification orchestration, global candidate/stage
accounting and CLI wiring remain the same M6 owner's next work. The prior actual
13-grade/39-case fixture was not repeated.

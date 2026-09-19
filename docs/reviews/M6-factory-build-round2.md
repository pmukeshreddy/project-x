# M6 BUILT root / solver package — round 2 closure

**Specification compliance: PASS. Implementation quality: PASS.**

The sole round 1 P2 is closed by
`71f0fa2c96c3bf3ebfac7539b52065743e0661ef` over the reviewed builder
`f70c406d7a876405060a43d61ba3db0893803971`. Open scoped findings:
**P0: 0, P1: 0, P2: 0**. This approves the bounded builder slice, not full M6
orchestration, task admission, CLI or experimental completion.

## Inventory-bound correction

[packaging.py:228](../../src/feature_rl/pipeline/packaging.py#L228) now serializes
the final inventory and checks the same 1 MiB cap used by its reader before
publishing the inventory or returning an assembly for freezing. Generated
instruction/runtime documents and the normalized workspace are likewise checked
against their existing reader limits. No cap is widened; publication recovery
continues to reuse exact frozen bytes.

An oversized inventory raises `BuildRejected` while still inside assembly, so
the existing builder path records a typed failed OperationResult and its costs.
It cannot reconcile an unreadable successful frozen receipt. Public components
published before this size check can remain unselected CAS objects, as disclosed;
they do not become a BUILT root.

The added regression uses the exact 1,902-file long-path trigger from round 1.
It checks `unsupported_semantics`, absence of inventory/successful-frozen
publication, measured construction wall time and unknown storage cost, then
unchanged accounting and failed-result replay through both `recover` and `build`
without new events. This directly addresses the reported failure and recovery
consequence.

The committed [fix receipt](../evidence/M6/factory-build-fix1/receipt.json) and
[output](../evidence/M6/factory-build-fix1/focused.txt) record **19 factory tests
passed, exit 0**, 2026-09-19 **22:29:57.258808–22:30:05.493384 UTC**,
pytest **8.10 s**, observer **8.234433 s**. I verified the changed product/test
hashes against the exact fix commit and receipt, inspected the passing output,
and confirmed the recorded before/after hash maps agree. The original failing
diagnostic remains in round 1 evidence. No test or Docker run was repeated for
this closure, and no unchanged-source inventory or registry re-audit was added.

## Limited integration inspection

I also inspected the TaskBuilder/M5 joins, labels and corresponding recorded
outcomes in `da26580abe88987c3a139b78e75f85712a3aa201`, specifically
[run_factory_click_fixture.py](../../tests/run_factory_click_fixture.py) and its
[consolidated receipt](../evidence/M6/fixture-runtime/receipt.json). No new defect
was found in that bounded inspection; it is not an independent review of the
entire M5 implementation.

The driver builds one explicitly synthetic helper feature on the real pinned
Click B, checks the frozen solver archive excludes that helper, derives exactly
the one-path synthetic H projection, and reuses the identical TaskBuilder revision
and T0 reference when instantiating M5. The recorded QualificationReport's task
reference equals that frozen root. The alternate positive is explicitly labeled
same-author and has empty independence evidence.

The receipt contains four initial grades with three completed cases each and
nine retained M5 grades with three completed cases each: **13 grades, 39 cases**.
The recorded rewards match the stated B/H/omission/reset outcomes. Runtime and
qualification phases are recorded as passed in **82.452668 s** and **195.194874 s**
respectively; the retained stdout records exit 0 for each phase. These are existing
actual Docker execution records, not executions performed by this reviewer.

The M5 result is explicitly **provisional**, has no human reviews, and retains
missing independence, authenticated human review, global repair history, control,
adversarial and C1-omission gates. The driver asserts that provisional status and
exact completed replay. Its report accurately distinguishes this test fixture
from model-generated historical feature construction and from qualification or
release. GPU training and experiments remain unverified. The next integration
and remaining-module work can proceed without reopening this closed builder issue.

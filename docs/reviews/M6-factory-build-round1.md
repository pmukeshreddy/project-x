# M6 BUILT root / solver package — independent round 1 review

**Specification compliance: FAIL. Implementation quality: FAIL.**

Frozen target: `f70c406d7a876405060a43d61ba3db0893803971`.
**P0: 0, P1: 0, P2: 1.** The single finding concerns accepted large inventories
becoming unreadable after freeze. It does not prevent the small labeled integration
fixture from progressing independently. The same M6 owner should correct it.

## P2 — Oversized inventory is published and frozen before its reader bound is checked

Locations: [packaging.py:219](../../src/feature_rl/pipeline/packaging.py#L219),
[packaging.py:235](../../src/feature_rl/pipeline/packaging.py#L235),
[build.py:111](../../src/feature_rl/pipeline/build.py#L111).

`assemble` serializes and publishes the complete `SolverInventory` without checking
its byte size. `inspect_package` later reads that same artifact through
`read_record`, whose `MAX_DOCUMENT` is 1 MiB. The frozen receipt is registered and
reconciled as accounting revision 2 before `_finish` performs that inspection.
Thus an input within the declared path/count/archive/request bounds can create
a frozen outcome that its own completion and recovery paths cannot read.

One narrow diagnostic reproduced this at the exact target using the existing
explicitly synthetic fixture and inert test-only wheel substitutions:

- 1,902 regular B files; source archive 3,901,440 bytes; serialized BuildInputs
  890,382 bytes. Paths have legal 150-byte components and remain below the path cap.
- The builder published an inventory of **1,124,568 bytes**, exceeding the reader's
  **1,048,576-byte** document limit, then published/reconciled a frozen receipt.
- `build` raised `BuildRecoveryRequired` caused by `ArtifactSizeLimitError`:
  `max_envelope_bytes=1402200 exceeded: observed at least 1499527 bytes`.
- The job remained `running` with frozen observation revision 2. `recover` raised
  the identical read-limit failure. Replaying immutable bytes cannot resolve this
  deterministic mismatch.

This is not missing upstream evidence or an external publication outage. All inputs
exist; the producer and consumer disagree about the size allowed for their own
generated record. The normal failed-prerequisite result path is bypassed after the
builder has declared the outcome frozen.

Required correction: serialize and validate the final inventory against the same
consumer byte limit before publishing/freezing a successful outcome. Check other
newly assembled components against their corresponding reader bounds at that same
boundary where needed. An oversized deterministic package should produce the
existing typed failed OperationResult with attributable costs, leaving no successful
frozen outcome that requires futile publication recovery. Do not silently widen
limits or reassemble during recovery. Add one focused boundary regression using
the observed long-path case, including failed-result replay/accounting.

The [diagnostic receipt](../evidence/M6/review-factory-build-round1/inventory-diagnostic.json)
records the actual values and execution interval, **2026-09-19
22:24:41.394300–22:24:44.156413 UTC**, measured diagnostic wall **2.761833 s**;
the tool reported **2.865428 s**, exit **0**. Exit 0 means the diagnostic completed
and captured the defect. A [reproducer](../evidence/M6/review-factory-build-round1/reproduce_inventory.py)
retains the triggering logic; its file form was not rerun. Temporary CAS/registry
state was isolated and cleaned by context managers. No source code, H, Docker,
native model, qualification or training execution occurred.

## Reviewed behavior and evidence

Review covered the four new pipeline modules, `tests/test_factory.py`, the frozen
interface/report/plan and relevant recorded evidence. Binding inputs were the M6
brief, lifecycle-admission protocol, and actual M0/M3 artifact, archive and runtime
interfaces. The already-approved registry was not re-audited. Concurrent new
integration files and other owners' work were excluded; no product, index, commit
or shared configuration was edited.

The inspected code builds a new archive from the complete explicit B file list,
selected public contract fields/checks and fixed runtime fields. It validates
paths and modes through M3's inert archive code, exact B/recipe and
contract/scenario/verifier joins, candidate lineage/partition, pinned dependencies
and raw bytes. H remains the exact private `SourcePair.reference` in T0, never the
workspace. Component and full-archive identities are bound by the inventory and
frozen receipt before the immutable `BUILT, qualification=None` TaskBundle.

The claim/intent sequence prevents automatic assembly redispatch after an uncertain
pre-freeze interruption. Reconciled frozen receipts drive exact post-freeze root
publication, selected result replay and fixed construction costs. `solver_package`
checks current task/dependency quarantine and compares root, component, package
and inventory bytes to the frozen inputs. The size mismatch above is the concrete
exception to that otherwise coherent publication/inspection flow.

The owner [verification receipt](../evidence/M6/factory-build/verification.json)
records **54 focused tests passed, exit 0**: 18 factory cases plus 36 existing
registry cases. Execution was **2026-09-19 22:15:50.559930–22:16:04.671396 UTC**,
pytest **13.98 s**, observer **14.107959 s**. Tests include two actual child-process
exit points before/after freeze, post-CAS lost reply, pending receipt retry,
changed-root/H joins and quarantine. Their synthetic fixtures and inert wheel
substitutions are explicitly labeled. I inspected this existing evidence and did
not rerun the suite. The four new modules and factory test are bound to the frozen
target by the receipt; unchanged upstream hash inventories were not repeated.

No additional concrete blocker was found in this scoped batch. Full authoring
orchestration, candidate/stage budgets, lifecycle admission and CLI remain later
M6 responsibilities. The passing diagnostic suite does not establish real Click
construction, qualification, release, sandbox compatibility, GPU training or
experimental results; those statuses remain separate.

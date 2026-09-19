# M6 lifecycle independent review — round 1

Coordinator review of the new `TaskLifecycle` slice at `8efdc89`, its selected-transition joins and focused tests. The previously reviewed Registry and TaskBuilder remain closed. Factory orchestration and the upcoming per-task service resolver are separate changes.

**Specification compliance: PASS. Implementation quality: PASS.** No confirmed finding in this slice.

Current admission requires the concrete same-store M5 service, exact accepted Q/T0 origin, complete canonical payload equality excluding only state/qualification, and the deterministically selected completed BUILT→QUALIFIED→RELEASED jobs. Receipt, target, configuration, revision, selected attempt and result are compared. Current quarantine and M5 external enrollment/revocation checks remain mandatory. No state-string or callback substitute grants production admission.

Transitions preserve every other original task field and the frozen solver package. New evidence/costs live in the transition result and Registry. Explicit opaque configuration/receipt dependencies preserve invalidation. Frozen publication recovery reuses the selected attempt/receipt and rechecks admission; unknown pre-freeze work is not repeated automatically. Historical completed recovery is readback, not current admission.

Independent command: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_factory_lifecycle.py -p no:cacheprovider` — **18 passed in 17.86s**, exit 0. Tests cover actual M5 missing-human denial, payload/selection drift, quarantine/revocation, frozen publication and pre-freeze uncertainty. Positive transition tests substitute only the M5 method in isolated diagnostic state; they create no HumanReview or accepted QualificationReport and prove no real admission. No Docker, model or native human-signature calls occurred. The owner's broader 37-test lifecycle/builder receipt is retained without repetition.

## Per-task resolver delta — `3845b7b`

**Specification and quality: PASS.** Independently inspected the new `ReleasedTaskResolver`, its M5/lifecycle configuration joins and nine focused tests. It selects a task's existing release configuration from the actual Registry graph, supports only explicitly supplied exact version profiles, constructs concrete M5/lifecycle services with the profile's current external trust, and invokes the reviewed admission path. Artifact contents cannot choose executable services or trust enrollment. Candidate-specific policy/config references are checked before reuse; unknown/ambiguous versions, payload drift and current quarantine/revocation reject.

Resolver configuration declares its profile dependencies. Successful reads reassert the identical already-selected CAS/Registry identities; the focused two-policy test checks no new events. Actual M5 rejects the diagnostic positive chain when its temporary test substitution is removed. Source and test files match the committed checkpoint. Retained owner command `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_factory_resolver.py` passed **9 tests in 29.09s**, exit 0; receipt and source hashes in `docs/evidence/M6/resolver/receipt.json`. No tests, original lifecycle matrix, Docker or human signatures were repeated for this source-level delta review.

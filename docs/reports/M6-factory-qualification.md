# Factory qualification orchestration

Factory now delegates qualification, acceptance and release to the actual M5
service and reviewed M6 lifecycle. The original BUILT root selects its completed
Factory construction receipt and child result; that exact history reference,
construction job and revision are bound into QualificationPolicy. Caller-supplied
history cannot replace the selection. Missing construction history stays missing,
and M5 retains its actual gate classification.

Acceptance loads the frozen review-request policy and uses the configured current
external verifier. Release resolves that report's actual M5 origin and executes
the legal BUILT → QUALIFIED → RELEASED transitions. It does not repackage the task
or grant validity from a state field. A nonaccepted diagnostic report remains
unable to release through actual M5.

`PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_factory_qualification.py`
passed 5 checks in 12.87 seconds. The receipt in
`docs/evidence/M6/factory-qualification/receipt.json` binds the observed HEAD and
source hashes captured after that check. Positive transition mechanics use an
explicit test-only M5 gate; no accepted qualification or human approval is made.
The actual M5 failure path, selected history, replay, caller substitution denial
and actual missing-human acceptance denial run without a worker or model.

This checkpoint adds concrete library wiring. Native generation, full authoring
budget/history accounting, command composition and remaining service joins are
still separate implementation work. Upstream retained M5/lifecycle publication
capabilities retain their existing concrete recovery APIs.

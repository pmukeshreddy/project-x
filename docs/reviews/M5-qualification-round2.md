# M5 qualification independent review — round 2

Reviewed only the two round-1 P1 fixes in `e3d868ac0fb95d71e616597ae5b85a5394b172bc`, their affected consumers and focused regressions. No unchanged upstream module was reopened.

**Specification compliance: PASS.** Both confirmed round-1 findings are closed.

**Implementation quality: PASS.** No remaining finding in this scoped delta.

1. **Semantic omission attribution — closed.** `qualification/controls.py:35` now invokes `require_semantic_execution` with the controller store at both qualification and admission. `qualification/evidence.py:166` requires the exact cleaned-up process adapter execution, normal exit zero and a successful runtime completion. The original syntax/import-error observations remain valid ordinary M4 comparisons but cannot supply targeted semantic coverage. Explicit missing-public-symbol observations with normal adapter completion remain supported; missing process evidence fails closed. The regressions exercise both original errors, a normally completed process omission, and an explicit JSON absence observation.

2. **Consumed dependency quarantine — closed.** `qualification/service.py:287` resolves the same baseline/delta boundary M4 consumes. New v2 run configurations register and check those leaves before grade-job enqueue; run bindings retain the dependency closure, and admission resolves/checks it again. `qualification/admission.py:50` checks both detached payload and signature before verification. New verification configuration/results and v2 accepted bindings include both leaves; accepted reuse and publication recovery check them before trust verification. Existing opaque Registry declarations remain immutable. Regressions verify preexisting quarantine, trace propagation and selected-result reuse for source deltas and failed-attestation leaves, including public submission envelopes. Legacy v1 admission bindings cannot satisfy the new gate.

Independent verification: `env PYTHONPATH=src .venv/bin/python -m pytest tests/test_qualification_review_fixes.py -q -p no:cacheprovider` returned **13 passed in 4.63s**, exit 0. All eight inspected fix/interface files match the reviewed commit, and all five evidence-log hashes match `docs/evidence/M5/review-fixes/receipt.json`. The owner's recorded broader result is 72 passes; it was not rerun.

Execution status is unchanged: these checks use synthetic failed observations and actual CAS/Registry paths. No Docker, retained-fixture matrix, native signature process, inference, key generation or human approval was run or created. Genuine external human acceptance remains unverified and required. This review closes code defects; it does not promote the provisional TEST fixture to an accepted feature task.

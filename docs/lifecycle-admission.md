# Immutable task admission protocol

Coordinator decision after read-only analysis by accountable M0 owner `/root/m0_contracts`, 2026-09-19. This specifies downstream use of the existing approved M0 API; no new schema or implemented admission claim. It expands the acyclic binding already documented in `docs/interfaces.md`. M5/M6 implement it and independent reviewers verify it.

## Qualification subject and human evidence

Freeze the complete built task `T0` with `state=BUILT` and `qualification=None`. Resolve every reference, including the final solver view and its inventory. Qualification and human approval bind this exact task. Packaging that changes the solver bytes after approval requires a new built task and requalification.

Before asking for human review, M5 freezes the concrete completed control/reset evidence and policy in a review package. A provisional `QualificationReport P` with `task=T0` and no human approval can serve as that package. An external human attestation must authenticate both the exact `T0` digest and the reviewed package digest and policy. Signing only the task digest must not let replacement control or alternative-validity evidence borrow that approval. A model cannot create the human attestation. `HumanReview.attestation` can refer to the authenticated payload; the existing schema is sufficient.

Accepted `QualificationReport Q` must resolve `Q.task` to the entire exact reference of `T0`, authenticate the human attestation, resolve its evidence package and policy, and verify that Q's actual execution/control/reset evidence matches what was reviewed. Its human-review subject remains `T0.sha256`. Nothing signs the future digest of Q, avoiding a cycle. M5 must explicitly constrain the human-approval/disposition/provenance/cost additions between P and Q; arbitrary replacement of reviewed evidence is forbidden.

## Lifecycle manifests

Later qualified, calibrated and released task manifests `Tn` refer to Q. Require exact equality of validated canonical payloads after excluding only `state` and `qualification`:

```python
payload(Tn, excluding={"state", "qualification"}) == payload(
    T0, excluding={"state", "qualification"}
)
```

This comparison includes kind, schema version, visibility, partition, family/lineage, source pair, baseline, all nested solver-view references, contract, environment, adapter, oracle, reference solution, provenance and costs. Do not use a handpicked subset of supposedly important fields or normalize away metadata/reference differences.

Task-manifest provenance and costs remain the original construction snapshot under this minimal rule. New transition evidence, provenance and incurred costs belong to the operation's result and M6's append-only event/cost ledger. Any future need to update manifest metadata requires a separately specified and reviewed extension to this equality rule.

## Admission and replay

Release and every released-task consumer resolve `Tn → Q → T0`, enforce the equality rule, verify accepted Q under the applicable qualification policy, and consult quarantine/revocation state. M6 also verifies the legal predecessor transition and its actual gate evidence. A caller-created `RELEASED` state string is insufficient; calibration never substitutes for qualification.

A semantic or packaging change creates a new built root. Comparing Q's task digest directly to Tn's digest is incorrect: Q signs T0. Repeated receipts or attestations cannot manufacture independent qualification runs. A new reviewed-evidence package or policy requires matching human approval even if T0 is unchanged. Old accepted evidence cannot resurrect a quarantined root, dependency or derivative; the registry must trace affected manifests, rollouts and checkpoints.

Required downstream regressions include every-field payload drift, changed evidence package or policy, forged released state, reused receipt counts, version/visibility mismatch, altered solver packaging, wrong built-root identity, revoked qualification and idempotent transition retries.

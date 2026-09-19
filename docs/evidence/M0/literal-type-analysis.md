# M0 Literal type analysis — read-only diagnostic

**Conclusion:** the reported Pydantic behavior reproduces under the shared StrictModel configuration, but the current M0 schemas have **no boolean or integer Literal fields**. No equivalent numeric-coercion bypass was found in the examined M0 schema versions, rewards or boolean gates. A narrower Python-input representation inconsistency exists for enum-valued Literals, described below. No product, tests, package configuration, schemas or real artifacts were changed.

Source revision: `39aac02e25d66464c3a44ecef7490f0be0cbd6d7`. Runtime: Pydantic **2.13.5**, pydantic-core **2.46.5**. The exact command, diagnostic source, UTC time, source digests, per-case inputs/outputs/errors and exit status are captured in [literal-type-diagnostic.json](literal-type-diagnostic.json). Command shape: `PYTHONPATH=src:tests .venv/bin/python -c '<captured diagnostic>'`; exit **0**. It performed 92 narrow synthetic validations in both JSON and Python modes, not the project test suite or any empirical pipeline run.

Source bindings:

| File | SHA-256 |
| --- | --- |
| src/feature_rl/contracts/models.py | e2103bd90901c58e2ce88fa0dad7e6dc87b1181c807ea1482f26d3713a54f3bd |
| src/feature_rl/artifacts/store.py | 75eba89bac5334dcf9ac04d026edcbda2e773fe2e63e85709e9ba18580a0cc63 |
| tests/test_contracts_examples.py | b1d777ab2a645c37c34bdb813342372a7f1f2de5a0af8d55f8ae32ccabde9d0f |
| tests/test_contracts.py | 0c83069837bfbf093893474b99032d291ac513c37215291c5b1f0748bd090f16 |
| pyproject.toml | d682f26759ea0f7df4c64d89f86429e2c9febe199c6e2c51d6adad0e8cfd5e53 |
| uv.lock | 23aed10cf14c548ca6a9791b7674e504af986a7bc1d0ba0c7941d368b7e7148c |

## Observed behavior

`StrictModel` sets strict=True, extra=forbid, frozen=True, allow_inf_nan=False and revalidate_instances=always (models.py:33). A diagnostic-only subclass using `Literal[True]`, `Literal[False]` and `Literal[3]` accepted respectively **1→True**, **0→False**, and **3.0→3** in both JSON and Python validation. This confirms that inheriting StrictModel does not itself prevent the M2-style numeric Literal behavior.

Inspection enumerated **46 actual Literal fields** in the shared models: string choices or string-backed Visibility/ActorRole choices, with no bool/int values. The full field inventory is preserved in the receipt.

Actual M0 fields exercised and results:

- **ArtifactRef.schema_version and all eleven artifact schema_version fields:** rejected boolean True and their matching floating-point versions (1.0 or 2.0), in both modes. They use strict constrained `int`, not Literal. CandidateRecord/SourcePair remain version 2; other typed artifacts remain version 1.
- **SeedPolicy.same_cases_within_group, PolicyConfig.require_token_probabilities, Requirement.mandatory, CaseDefinition.mandatory, ControlPatch.expected_valid, RunAssessment.passed, TokenTrace.assistant_loss_mask elements, RolloutRecord.training_eligible, TrialResult.resolved:** rejected the tested numeric 0/1 substitutes, in both modes. These are plain strict `bool`, nullable bool or tuple-of-bool fields. Validation errors identify the field/type, rather than merely failing a later success predicate.
- **RolloutRecord.reward:** rejected True and 1.0 in both modes on an otherwise valid measured synthetic episode. It is a strict bounded int, not Literal.
- **HumanReview.actor_type/decision:** rejected the tested numeric/bool substitutes. Their string Literals do not provide numeric synonyms for human/approval values. This does not authenticate the human record; that remains the external gate.
- **CandidateRecord.visibility and VerifierPermissions.controller_role:** rejected numeric/bool substitutes. Matching strings (`private`, `controller`) became the expected enum objects in both JSON and Python modes.

The last behavior is appropriate for JSON, whose enum representation is a string. For Python input it is more permissive than the module docstring's blanket statement that exact enum types are used. The same enum-Literal declaration pattern appears in CandidateRecord, SourcePair, ScenarioPlan, VerifierBundle, TaskBundle, QualificationReport, RolloutRecord, TrainingCheckpoint and EvaluationReport visibility fields, and VerifierPermissions.controller_role. The two representative whole-model paths above were directly executed; the remaining fields were inspected. This accepts the same declared label rather than converting an unrelated number into a privileged gate, and no visibility escalation was demonstrated.

## Minimal recommendation and compatibility

1. **No M0 product/schema change is required to address this particular numeric-Literal finding in current shared fields.** Existing constrained integer versions and plain strict booleans already reject the problematic inputs. Do not replace them with numeric/boolean Literals.
2. For M2's actual fixed bool/int Literal fields, the minimal remedy is a field-level pre-validation exact-type check (`type(value) is bool` or `type(value) is int`) before Literal matching. This preserves the fixed-value JSON schema while rejecting equal-valued objects of other types. A strict bounded int is also sufficient for a fixed numeric version and is M0's existing pattern. A broad StrictModel redesign is unnecessary for this finding.
3. If the coordinator wants exact Python enum-instance inputs as an additional contract, use narrowly scoped Python-mode guards on enum-Literal fields while retaining JSON strings. That would reject callers currently supplying matching Python strings, so it is an API tightening requiring downstream checks. The smaller compatibility-preserving alternative is to clarify the documentation's representation statement. Neither choice is needed to repair a demonstrated numeric gate bypass in M0.
4. No M0 artifact-version bump or retroactive requalification follows from this diagnostic: the examined shared numeric/boolean fields did not accept the coercions. M2's original-input provenance and protocol compatibility need its own review. Normalized stored values cannot establish what an earlier raw request originally contained.

This analysis establishes only local validation behavior. It does not establish task qualification, authenticity of evidence, model behavior or an empirical pipeline result. No model, historical repository code, real task or full test suite was executed.

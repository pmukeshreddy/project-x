# M0 provenance v2 independent review

Reviewed product range `6af01fd..e16493b`; later `52aaf16` is documentation-only. Inputs reviewed: the complete supplied diff, readable product/test/interface diff, actual contracts/storage and regression tests, `docs/briefs/review-policy.md`, the final M0 report section, shared interfaces, the requesting historical-provenance/redirect findings in `docs/reviews/M1.md`, and recorded evidence. Only this review report was written.

**Specification compliance: PASS for the M0 extension scope.** CandidateRecord and SourcePair require explicit v2 provenance labels, SourceSnapshot distinguishes known redirect chains from unavailable evidence, and legacy typed reads/required links fail without migration. Unrelated artifact kinds and raw-byte storage remain v1.

**Implementation quality: PASS for the M0 extension scope.** No substantive defect found in the changed code. Version checks preserve the existing canonical storage, reference integrity and role boundaries. The documented downstream producer break is intentional and does not represent a passing full-project integration.

## Reviewed behavior

| Requirement | Finding |
| --- | --- |
| Required provenance with no inferred default | **Satisfied.** `src/feature_rl/contracts/models.py:320` and `:341` require strict schema_version 2 and a required two-value provenance_label. Missing/invalid labels fail. Changing the label changes immutable identity in the recorded round-trip tests. |
| Explicit known versus unavailable redirects | **Satisfied.** `src/feature_rl/contracts/models.py:289` requires either a nonempty sequence or explicit null, and line 293 binds a known chain's first entry to the original URL. `(url,)` and null remain distinct in JSON and tests. Neither a default nor a synthetic no-redirect chain is introduced. |
| Per-kind version consistency | **Satisfied.** The version map at `src/feature_rl/contracts/models.py:826` advances only CandidateRecord and SourcePair. `require_ref` at line 813 checks kind, JSON encoding and current version, covering ConstructRequest, SourcePair.candidate and TaskBundle.source_pair as well as other existing required typed links. Generic evidence references can still represent old versions without admitting them as current typed dependencies. |
| CAS/version integrity | **Satisfied.** `src/feature_rl/artifacts/store.py:149` passes the validated model version to `_put`; lines 160–168 bind it into the envelope, digest and returned reference. Typed reads check the current per-kind version at line 241, then retain digest, canonical form, metadata and payload/envelope checks. Merely relabeling an old reference as v2 cannot match its stored envelope. |
| Legacy rejection and retention | **Satisfied.** Unsupported typed versions fail before deserialization; no rewrite/migration path exists. Regression tests create digest-valid old v1 envelopes, assert explicit rejection, and verify unchanged file bytes. Dependent manifests with obsolete required source/candidate links also fail current validation. Preserved receipts are not silently relabeled. |
| Unrelated v1 storage and prior protections | **Satisfied.** Raw writes still use version 1 and reads explicitly reject other raw-byte versions at `src/feature_rl/artifacts/store.py:226`. Other typed models retain their v1 constraints. Typed/opaque separation, model revalidation, authorization, public/authoring exposure checks, privileged H visibility, exclusive atomic publication, duplicate comparison, durability and read-time tamper checks are unchanged. The prior rollout and evaluation guards are also unchanged. |

## Evidence inspected

- All changed files in this range match their contents at `e16493b`; all ten hashes in `docs/evidence/M0/provenance-v2-tested-files.json` match the reviewed files.
- All **61** published model schemas match their actual production models. The complete example catalog matches its executable fixture builder. The current map contains two v2 artifact kinds and nine v1 artifact kinds.
- Preserved red receipts record **9 failed, 6 passed** for the initial provenance/version cases and **4 failed, 2 passed** for redirect cases, both exit 1.
- `docs/evidence/M0/provenance-v2-verification.json` records the focused M0 suite at **124 passed**, exit 0, and a clean scoped whitespace check. Its full-suite receipt records **138 passed, 2 failed**, exit 1. I did not repeat either suite or execute new acquisition/runtime work.

The two full-suite failures are the recorded M1 `archive_source` producers omitting required redirect_chain in `test_connected_intake_builds_private_source_pair_and_safe_authoring_view` and `test_source_archive_preserves_edit_status_and_detects_store_corruption`. Once those constructors are reached, M1 also needs explicit v2 versions and validated labels in its candidate/source-pair producers. These are pending downstream changes, not requests for M0 to edit M1-owned paths.

## Downstream obligations

M1 must validate historical edit/cutoff evidence and chronology, assign and preserve the same supported provenance decision in CandidateRecord, SourcePair and its result interfaces, enforce actual acquisition URL/hop/deadline policy, and propagate observed redirect chains. Null must remain unavailable evidence. M0 deliberately does not dereference a candidate to authenticate its label or establish equality with a SourcePair label; those are M1 admission joins, as documented.

M1 must revalidate archived evidence and publish new v2 artifacts and dependent references while retaining old bytes and receipts. This review does not clear the seven M1 findings, approve historical-request claims, or establish a corrected real Click intake. Full integration requires the M1 owner changes, focused re-review and coordinator verification. The existing worker, qualification, human-attestation, training and evaluation gates remain unchanged.

The shared extension is approved to unblock those M1 producer updates; no further M0 correction is requested by this scoped review.

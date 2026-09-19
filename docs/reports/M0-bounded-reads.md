# M0 bounded artifact reads

Status: implemented and verified; independent review and downstream M3 adoption pending. Assigned base `0a08c916f68a82fb865696a355cfb63965b86f30`. Read the scoped M0 brief, current artifact APIs/tests/interfaces and `docs/evidence/M3/production-interface-handoff.md`. Only artifact implementation/exports, dedicated artifact tests and M0 documentation/evidence were changed. No dependencies, schema versions, canonical identities, M2/M3 product code or existing receipts were changed. Finder files and `.superpowers/` were preserved.

## Public API and behavior

```python
get_artifact(ref: ArtifactRef, *, max_envelope_bytes: int | None = None) -> ArtifactModel
get_bytes(ref: ArtifactRef, *, max_envelope_bytes: int | None = None,
          max_payload_bytes: int | None = None) -> bytes
```

Caps are inclusive and keyword-only. None retains uncapped compatibility; zero is valid. Boolean/noninteger caps raise TypeError; negative caps raise ValueError. Public `ArtifactSizeLimitError(ArtifactError)` exposes `limit_name`, `limit`, and `observed_bytes`. Observed size is a lower bound: an initial stat supplies its observed file size; growth detection supplies cap+1; valid base64 structure supplies its decoded length.

The serialized-envelope cap checks the opened regular file's fstat size before reading, then reads in chunks of at most 65,536 bytes with at most cap+1 total bytes. This rejects growth after the stat without a one-shot allocation driven by an arbitrarily large cap. Oversized envelopes are rejected before digest/JSON parsing. Normal successful reads retain authorization, descriptor-relative/O_NOFOLLOW and regular-file checks, digest/ref/canonical validation, typed schema validation and public/authoring exposure rules.

The independent decoded-payload cap validates base64 shape/padding and computes decoded size before calling base64 decoding. Standard alphabet validation and canonical re-encoding remain mandatory. Empty, one-/two-byte padded and unpadded exact boundaries are accepted. The cap bounds decoded result size, not envelope/JSON/string allocations; callers requiring both controls must pass both caps. JSON parsing/model construction and byte buffering have additional allocation overhead, so the serialized cap is not an exact process-memory ceiling.

Duplicate writes share the read helper and now compare an existing object with a cap equal to the expected serialized length. Larger corrupt/colliding existing objects still raise ArtifactIntegrityError and are never overwritten. This prevents duplicate handling from retaining an unbounded read while preserving its existing failure semantics.

M3 must supply its own per-object limits, then enforce aggregate staging, archive expansion, file counts and other runtime bounds. No store read method alone establishes those downstream protections. Exact interfaces and semantics are published in `docs/interfaces.md`.

## Evidence and verification

`docs/evidence/M0/bounded-reads/red.json` preserves exact command, UTC, base revision, source/test hashes, stdout/stderr and exit status for the test-first run:

- `.venv/bin/python -m pytest -q tests/test_artifacts_bounded.py`: **30 failed, 16 passed**, exit **1**. The absent keyword API/typed exception caused the new behavior failures; existing argument-TypeError/corruption behavior already satisfied some cases.

The new test file contains 46 cases covering opaque and typed exact envelope boundaries, all decoded padding boundaries and empty data, argument type/range errors, huge integer bounds, stat rejection before any read/JSON decode, real append-after-stat with instrumented bounded reading, rejection before base64 decoding, invalid/noncanonical base64, bounded visibility/ref/hash/symlink/exposure checks, unchanged uncapped reads and duplicate corruption handling. Test doubles are confined to the test file; filesystem objects and publication are real temporary-store operations.

`docs/evidence/M0/bounded-reads/verification.json` preserves fresh exact commands, UTC, revision, source hashes and outputs:

- `.venv/bin/python -m pytest -q tests/test_artifacts_bounded.py tests/test_artifacts.py tests/test_contracts.py tests/test_contracts_examples.py`: **170 passed**, exit **0**.
- `.venv/bin/python -m pytest -q`: **299 passed**, exit **0**; full suite run once for this completed implementation.
- `git diff --check -- src/feature_rl/artifacts tests/test_artifacts_bounded.py docs/interfaces.md`: clean, exit **0**.

No model inference, native MLX probe, real runtime/historical task, network operation, package download or install was performed. The full suite is project regression evidence, not empirical task qualification. Independent review and actual M3 consumption remain required before claiming the downstream allocation gap closed.

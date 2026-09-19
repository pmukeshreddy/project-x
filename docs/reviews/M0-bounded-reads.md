# M0 bounded-read extension independent review

Reviewed exact product `bf065ec738f29f13cf0cc783b53353adcbe6fab8` against assigned base `0a08c916f68a82fb865696a355cfb63965b86f30`. Read the M0 bounded-read brief first, M3's production interface handoff, the complete owned review diff, actual artifact implementation/exports and tests, published interfaces, owner report and retained receipts. Adjacent root planning/handoff changes are context only. Only this report was written; no product, index or commit changes occurred.

**Specification compliance: PASS for this extension scope.** The authoritative public store APIs provide explicit inclusive envelope and decoded-byte caps, reject size excess at the required allocation boundaries, preserve uncapped compatibility and expose a typed size failure. No schema, identity or dependency change is introduced.

**Implementation quality: PASS for this scope.** No actionable substantive defect found. The read loop handles stat underestimates/file growth, argument checks reject coercion, decoded-size arithmetic accounts for canonical padding, and duplicate-write comparison retains its integrity semantics while gaining a bound.

## Reviewed behavior

| Concern | Conclusion and code evidence |
| --- | --- |
| Public APIs and argument validation | `src/feature_rl/artifacts/store.py:257` and `:285` add keyword-only `max_envelope_bytes=None` to both reads and `max_payload_bytes=None` to get_bytes. `_validate_limit` at line 50 accepts exact nonnegative int or None, rejects booleans/nonintegers with TypeError and negatives with ValueError. Zero and arbitrarily large integer caps remain representable. `ArtifactSizeLimitError` at line 40 is publicly exported and exposes limit_name, limit and observed_bytes. |
| Envelope bound before JSON | `_read_file` at line 213 retains descriptor-relative no-follow opening and regular-file validation. The fstat size check precedes reading; the loop at lines 230–237 obtains at most cap+1 returned bytes in requests no larger than 65,536. It therefore catches growth after stat without passing the full caller cap to a one-shot read. `_get` calls this helper before digest verification and JSON decoding. Both opaque and typed public reads use that path. |
| Decoded cap before base64 allocation | Lines 269–277 require a string, ASCII, a multiple-of-four length and correctly located terminal padding, then compute `3 * (encoded_length // 4) - padding` before calling the decoder. The empty case computes zero; one-/two-byte padding and unpadded boundaries are handled. Size exceptions remain typed rather than being caught as encoding errors. Alphabet validation and canonical re-encoding at lines 278–280 still reject malformed/noncanonical encodings on successful-size paths. |
| Existing security/integrity checks | Authorization still precedes file access. Reference/kind/version checks, no-follow descriptor traversal, nonregular-file refusal, SHA-256 verification, canonical JSON/duplicate-key validation, envelope metadata equality, strict typed payload validation and public/authoring exposure checks remain on successful reads. Oversize may reject before content validation, as documented, but never returns unverified data. |
| Defaults and immutable identity | Uncapped `_read_file` retains its existing behavior and both public caps default to None. The new base64 shape checks do not admit noncanonical data or exclude canonical payloads. Serialization, hashing, schema versions, put API signatures and access policy are unchanged. |
| Duplicate-write safety | Lines 199–205 cap the existing-file comparison at the expected serialized length. An oversized existing file becomes the existing ArtifactIntegrityError corruption/collision outcome; equal data remains idempotent and differing data is never overwritten. Temporary publication, fsync and cleanup behavior are unchanged. |

## Tests and evidence inspected

The new `tests/test_artifacts_bounded.py` contains **46 cases**. I inspected its real temporary-store round trips and test-local fault instrumentation: exact envelope/payload boundaries, empty and all padding cases, typed oversize, invalid caps, huge integer caps, rejection before read/JSON/decode, real append after fstat with bounded read accounting, malformed/noncanonical base64, bounded access/ref/hash/symlink/exposure checks, uncapped compatibility and oversized duplicate corruption. Existing artifact tests continue to cover canonical/version/path handling and concurrent duplicate publication.

- The owned review diff is exactly **76,393 bytes**, SHA-256 `6cf10681b11de9c9ae2bb6301c54d40366697aa9ff4b3dc2395d85a0474cfb8a`, matching the dispatch and coordinator binding.
- Independently checked **15/15** source/test/interface bindings across the three verification receipts against current bytes and the exact product commit. All match; the verification receipt's digest also matches `docs/evidence/M0/coordinator-bounded-read-binding.json`.
- The preserved red receipt records **30 failed, 16 passed**, exit 1, for the new bounded tests before implementation. This includes some already-passing error/compatibility cases, accurately disclosed by the owner.
- `docs/evidence/M0/bounded-reads/verification.json` records **170 focused tests passed**, **299 full-suite tests passed**, and a clean scoped whitespace check, all exit 0. The receipt records the precommit source revision plus hashes that bind its tested changes to the final commit.

Review verification was limited to read-only source/diff/receipt inspection and identity checks. No concrete uncovered concern warranted another reproducer. I did not rerun the suites or perform native MLX, Docker, historical-source, download, installation, network or model work. These unit/regression results do not establish runtime isolation or task qualification.

## Handoff and limits

The implementation closes the missing **M0 API capability** identified by M3. M3 still must actually supply both caps for opaque inputs and an envelope cap for typed inputs, then separately enforce aggregate staged bytes, archive expansion, file count and lifecycle/resource limits. An uncapped call remains intentionally uncapped. A payload-only cap does not constrain prior envelope/JSON/encoded-string allocation; even an envelope cap is not an exact process-memory ceiling because buffering, parsing, canonicalization and model construction add overhead. These limits are stated explicitly in the interfaces and owner report.

No M0 correction is requested. The extension is approved for coordinator integration verification and M3 adoption; actual downstream consumption and runtime evidence remain required before declaring the complete M3 allocation boundary verified.

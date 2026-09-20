# M8 external-row adapter checkpoint

This CPU-only checkpoint implements bounded ingestion for already supplied private SWE-Bench++ rows. It acquired no dataset payload, repository, model, task or human approval.

The adapter:

- enforces the exact visible pinned row schema and immutable dataset/harness revisions;
- requires an explicit origin mapping to actual M1 `CandidateRecord`/`SourcePair` provenance because the release lacks PR/H metadata;
- authenticates the shared M1 source-inspection log, actual authoring request/B/license refs, B/reference archive tree digests, reconstructed patch digest, family, lineage, local assignment, rights/use classification and private row fingerprints;
- validates the supplied locked-evaluation roster and rejects any external source sharing a repository family or request lineage;
- keeps solution/test patches, native test names, H and environment hints private while emitting a positive PUBLIC/AUTHORING source allowlist;
- invokes actual `Factory.screen_source`, then actual `Factory.construct` with the aligned supplied `BuildInputs` (or records M6's real missing-input BLOCKED result), preserving both selected results and costs in a four-stage funnel; and
- registers the opaque configuration, frame, mappings, row leaves, M6 receipts and private batch in the existing Registry.

Focused command:

```text
PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_external_adaptation.py -q
```

Result before commit: `6 passed in 2.79s`.

The combined owned diagnostic set was also observed as `23 passed in 1.27s`; no broad suite or Docker matrix was repeated. These are labeled synthetic row/mechanism checks, not corpus coverage or task results.

No actual corpus row was adapted and no item is claimed runnable or qualified. Labeled diagnostics exercise missing and supplied real `BuildInputs`; they do not establish that a SWE-Bench++ row has authentic origin mapping or usable construction inputs. The adapter never manufactures them from `test_patch`, native tests or untrusted `environment_config`. Real corpus construction, fresh-worker validation, external observations, human qualification, release, training and GPU evaluation remain unexecuted.

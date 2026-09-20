# M8 external-row adapter checkpoint

This CPU-only checkpoint implements bounded ingestion for already supplied private SWE-Bench++ rows. It acquired no dataset payload, repository, model, task or human approval.

The adapter:

- enforces the exact visible pinned row schema and immutable dataset/harness revisions;
- requires an explicit origin mapping to actual M1 `CandidateRecord`/`SourcePair` provenance because the release lacks PR/H metadata;
- authenticates the shared M1 source-inspection log, its Git object-tree IDs, actual authoring request/B/license refs, reconstructed patch digest, family, lineage, local assignment, rights/use classification and private row fingerprints; Git object IDs remain distinct from the archive format's SHA-256 tree encoding;
- validates the supplied locked-evaluation roster and rejects any external source sharing a repository family or request lineage with a `LOCKED_TEST` member;
- keeps solution/test patches, native test names, H and environment hints private while emitting a positive PUBLIC/AUTHORING source allowlist;
- invokes actual `Factory.screen_source`, then actual `Factory.construct` with the aligned supplied `BuildInputs` (or records M6's real missing-input BLOCKED result), and authenticates the selected `ConstructionResult.build_job/build_result` so source, Factory-controller and TaskBuilder child costs each remain in their original scope exactly once in the four-stage funnel; and
- registers the opaque configuration, frame, mappings, row leaves, M6 receipts and private batch in the existing Registry.

Focused command:

```text
PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_external_adaptation.py -q
```

Result before commit: `8 passed in 5.28s`.

One diagnostic builds a bounded local Git history and sends it through the actual M1 intake before adaptation. It confirms that the authenticated M1 proof carries Git tree object IDs while `SourceArchive` carries a different SHA-256 tree encoding. All fixtures remain labeled local mechanism checks, not corpus coverage or task results; no broad suite or Docker matrix was repeated.

No actual corpus row was adapted and no item is claimed runnable or qualified. Labeled diagnostics exercise missing and supplied real `BuildInputs`; they do not establish that a SWE-Bench++ row has authentic origin mapping or usable construction inputs. The adapter never manufactures them from `test_patch`, native tests or untrusted `environment_config`. Real corpus construction, fresh-worker validation, external observations, human qualification, release, training and GPU evaluation remain unexecuted.

# M1 coordinator integration closure

The M1 round-two source/test implementation `a658ae4` passed independent specification and quality re-review in `docs/reviews/M1-round2.md`. At full revision `c2bbd3d8b6c3303402bf63aae6bd4309ab81e2eb`, the coordinator independently executed the full local suite: **155 passed**, exit 0, pytest 17.16 seconds and externally measured 17.294 seconds. Exact command, UTC times, revisions before/after and output hashes are in `coordinator-round2-suite.json`; stdout and stderr are retained separately.

The coordinator then ran this command twice through the actual cached intake implementation:

```sh
PYTHONPATH=src .venv/bin/python docs/evidence/M1/run_real_intake.py \
  --workspace . \
  --factory-revision c2bbd3d8b6c3303402bf63aae6bd4309ab81e2eb \
  --recorded-at 2026-09-19T09:35:26.709993Z
```

Both attempts exited 0 with empty stderr, in 1.961 and 1.989 seconds. Their exact stdout bytes match, SHA-256 `deca99ae7076bef8636a8b755832ad6a8df2ab9b91503f1bf9e0a6cadb0b2ea7`. Each invocation also tested an internal identical retry. The before/after file inventory of the Git object database remained equal, digest `39b3cd91625845754138380506e5af9357ecd227f9b878646ece96f7ceba6f05` (20 files, 505392 bytes). No network or historical application execution occurred. Per-attempt receipts, raw JSON, stderr and inventories use the `coordinator-round2-` prefix.

Current intake identities:

| Artifact | Current SHA-256 |
| --- | --- |
| CandidateRecord, v2 private | `b0d7cfaa9d81aa1adbbdf6b76056c198b8f3250bd6e2fd40a79f1b604e6134d4` |
| SourcePair, v2 private | `74d7e0ee98012c0fffab451e1664a4446ffdec1ce1fa38ec406e554d3456045b` |
| Authoring B archive, v1 bytes | `4ff1d8a5d478ffb12950ec2661f3b38be5b7c9925691726c27b83234c5814575` |
| Authoring request, v1 bytes | `d8d8a97861a597c7bfe181dfd3bbd6dbf72b7854ee13d555add1b1e07f0d2c61` |
| Authoring license, v1 bytes | `b84ace5d4d01f55ab2db4e25ffddb9239104176e99a73e4799dcadbd8e612ab9` |

The coordinator checked B `19fd4d6e18bc9fce451f92f422696b11169faa57`, H `831c8f0948af519e45b90801d7430ff25451f972`, equal `reconstructed_specification` labels in the result and both v2 models, 24 explicit unavailable redirect chains, every author-view check, internal idempotency, and provisional screening. The source metadata/version changes give new intake artifact identities; the older accepted and invalid receipts remain untouched.

This closes M1 implementation and integration for its documented conservative topology. The Click candidate still has three mixed-purpose paths requiring downstream scope review. No contract, runtime, checker, qualification, human approval, solver success or learning result is claimed. The previous owner report's pending coordinator gate is superseded by this actual integration record.

To reproduce these precise artifact identities, use the stated revision and immutable cached inputs. Later producer/schema changes must produce their own versioned receipts.

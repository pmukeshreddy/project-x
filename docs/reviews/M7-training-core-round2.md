# M7 training-core independent review — round 2

Reviewed `f9f1c2f..17e43f4`, limited to the three round-1 P1 findings and affected data/native-conversion interfaces. Read the fix report, complete scoped code/test diff, updated interface contract and recorded evidence. No additional module audit or product changes.

**Specification compliance: PASS for this scoped correction.** All three round-1 findings are closed.

**Implementation quality: PASS for this scoped correction.** No residual defect found in the reviewed changes.

| Prior finding | Closure and evidence |
| --- | --- |
| P1: authentic source-rejection zeros rejected | `data.py:59–98` now binds M4 task, submission, case seed, disposition, reward, revision and publication evidence. It accepts the actual pre-execution source-rejection zero while retaining `source_inspection`, and rejects promotion to `real_integration`. The new data tests use actual M4 malformed-submission grading and exercise scope/identity tampering. |
| P1: null candidate outcomes silently excluded | `data.py:66–78,123` requires INVALID/INFRASTRUCTURE disposition and a matching M4 receipt for null outcomes. Ungraded candidate failures and bare infrastructure labels reject. The missing-source fixture obtains an actual M4 null receipt; accepted groups preserve all four original records, evidence scopes and costs. |
| P1: eligible-group shrink breaks native batch shape | `skyrl_bridge.py:95–131,195` converts through a trainer view with an independent configuration using the eligible prompt count, then coalesces native prompt boundaries into configured minibatches, including a final remainder. It preserves the original trainer configuration and assigned-group accounting. Pinned trainer `_execute_training_step` consumes these boundary metadata directly. Tests cover shrink, remainder, undersized/full batches, noncontiguous IDs, conversion failure and omitted singleton rows. |

Recorded validation: `docs/evidence/M7/training-core-round1/focused-tests.txt` reports **38 passed in 2.07s** for the five focused training test files. I inspected the actual regressions and reused that evidence rather than rerunning the suite. Fresh read-only verification confirmed both test source snapshots byte-for-byte match the retained SkyRL `f5bc3b78dfddfb352870d5d7430cd226e5785838` source: complete `preprocess.py` SHA-256 `9f7667890b9d789e6cc9875dca666627f493ceb3db55fc97599c244c6f25e205`, and `trainer.py:848–967` SHA-256 `b3922d64c81080d99286fd5d1e6acaf41feadbca605eee2a129e14c18fc7a131`. The reviewed product/test files matched `17e43f4`.

The native-conversion diagnostics execute exact tensor conversion and boundary functions with real CPU Torch; trainer resources, batch container and zero-size DP padding remain explicit doubles. They establish no native SkyRL/Ray/FSDP/vLLM runtime result. GPU execution, distributed padding/normalization/optimizer/sync, feature trajectories and signal, native checkpoint/resume, and full runner/service integration remain unverified or upcoming as already declared. In particular, the runner must integrate authenticated pre-grading invalidation receipts before those outcomes can be excluded. These are separate downstream gates, not reopened findings in this slice. This review does not declare full M7 completion or feature learning.

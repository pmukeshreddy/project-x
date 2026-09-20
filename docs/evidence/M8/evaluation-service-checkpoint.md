# M8 evaluation-service checkpoint

This checkpoint implements the real service composition and recovery boundary without running CUDA, downloading a model, or claiming an experiment result.

The production path freezes the inert M7 `NativeSessionFactory` configuration in the selected evaluate job, JSON-revalidates the request, and authenticates current released task and selected training-checkpoint joins. Selected TrainingConfig/NativeSettings controls, the implementation's public tool/action/harness/optimizer protocol, declared update/rollout/token bounds, and distinct C/D dataset roots are checked before startup. C's frozen batch is revalidated through the existing inert external-row origin path; each admitted released Tn resolves back to the exact selected construction BUILT T0. The service starts the session only after the claim, records each sequential arm activation, revalidates live activation before every runner call, passes the frozen case seed, and consumes the selected M7/M4 receipt without regrading. It freezes all trial outputs before shutdown; retained shutdown recovery completes before report publication and does not resample an episode. `close()` performs cleanup-only retry of either a retained handle or an unpublished startup for a CLI `finally` block. A simultaneous evaluation and cleanup failure preserves the evaluation error and attaches the cleanup error.

Focused command:

```text
PYTHONPATH=src:tests .venv/bin/python -m pytest -q tests/test_evaluation_core.py tests/test_evaluation_service.py
```

Result before commit: `11 passed in 54.29s`.

The native composition diagnostic uses actual ArtifactStore/Registry, actual selected train jobs/checkpoint records, actual AgentRunner, actual Factory receipts, distinct diagnostic BUILT/RELEASED refs, and actual NativeSessionFactory accounting with a labeled in-process session boundary double. It confirms admission joins, origin relabel rejection, startup cleanup, activation/shutdown ordering and report recovery mechanics only. The diagnostic provisional lifecycle records are not task qualification or admission evidence. No native framework import, GPU work, model generation or empirical comparison occurred.

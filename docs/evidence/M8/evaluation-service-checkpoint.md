# M8 evaluation-service checkpoint

This checkpoint implements the real service composition and recovery boundary without running CUDA, downloading a model, or claiming an experiment result.

The production path freezes the inert M7 `NativeSessionFactory` configuration in the selected evaluate job, authenticates current released task and selected training-checkpoint joins, starts the session only after the claim, records each sequential arm activation, revalidates live activation before every runner call, passes the frozen case seed, and consumes the selected M7/M4 receipt without regrading. It freezes all trial outputs before shutdown; retained shutdown recovery completes before report publication and does not resample an episode.

Focused command:

```text
PYTHONPATH=src:tests .venv/bin/python -m pytest -q tests/test_evaluation_service.py
```

Result before commit: `4 passed in 28.19s`.

The native composition diagnostic uses actual ArtifactStore/Registry, actual selected train jobs/checkpoint records, actual AgentRunner and actual NativeSessionFactory accounting with a labeled in-process session boundary double. It confirms startup/activation/shutdown ordering and report recovery mechanics only. No native framework import, GPU work, task qualification, model generation or empirical comparison occurred.

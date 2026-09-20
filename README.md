# Feature RL

This project constructs immutable feature tasks from retained source evidence,
grades source implementations in a pinned Docker boundary, and checks current
qualification before release, agent execution or learning. It includes native
agent runs, SFT/GRPO training integration, frozen evaluation and historical audits.

Install the package with Python 3.11 or newer using `python -m pip install .` in
the intended environment. In this workspace, use the existing environment:

```sh
PYTHONPATH=src .venv/bin/python -m feature_rl --help
PYTHONPATH=src .venv/bin/python -m feature_rl config-schema
```

The CLI exposes `construct`, `qualify`, `release`, `run`, `grade`, `audit`, `train`
and `evaluate`, plus source screening, authoring, retained-history import,
acceptance, resolution and publication recovery. Commands use strict JSON inputs
and the actual library services. See [the runbook](docs/runbook.md) for commands,
configuration and recovery, and [the native launch contract](docs/reports/M7-native-launch.md)
for pinned Linux/CUDA setup, training and checkpoint resume.

The labeled Click fixture executed 13 real grades and 39 cases through M3–M6. It
remains provisional: it is a diagnostic feature, has no human approval, and does
not replace the original exhausted feature-generation attempt. See
[the retained fixture report](docs/reports/M6-click-fixture.md).

[Final CPU/CLI verification](docs/evidence/integration/final/receipt.json) and
[progress](docs/progress.md) record the executed checks and current limitations.
The Docker runtime supports pinned Python wheel profiles, including the legacy
Click profile, with [construction-time cached runtime images](docs/interfaces-M3.md).
New construction requires a publishing registry and complete pinned inputs.
GPU training,
native GPU inference, new task generation and experimental results remain
deferred and unverified. No real task has been human-approved or released.

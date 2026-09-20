# Feature RL

This project constructs immutable feature tasks from retained source evidence,
grades implementations in a pinned Docker boundary, and requires exact current
qualification before release or learning. A built task is not an admitted task.

Install the package with Python 3.11 or newer using `python -m pip install .` in
the intended environment. In this workspace, use the existing environment with
`PYTHONPATH=src` because its editable `.pth` installation is not effective:

```sh
PYTHONPATH=src .venv/bin/python -m feature_rl --help
PYTHONPATH=src .venv/bin/python -m feature_rl config-schema
```

The current command checkpoint supports `screen-source`, `construct`, `qualify`,
`accept`, `release` and read-only `resolve`. Commands use strict JSON requests and
actual services. The setup, identities, prerequisite failures and private recovery
output are documented in [the runbook](docs/runbook.md). Library interfaces are in
[M6 interfaces](docs/interfaces-M6.md); actual implementation status and remaining
agent/training/evaluation command joins are in [progress](docs/progress.md).

The labeled Click fixture executed 13 real grades and 39 cases through M3–M6. It
remains provisional: it is a diagnostic feature, has no human approval, and does
not replace the original exhausted feature-generation attempt. See
[the consolidated execution report](docs/reports/M6-click-fixture.md).

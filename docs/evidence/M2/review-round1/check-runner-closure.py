"""Repeat only the original F1 monitor failures using trusted bounded sleepers."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import sys

import feature_rl.generation.runner as runner_module
from test_generation import limits

HERE = Path(__file__).resolve().parent
results = {}
for name, options in (
    ("watchdog_exception", {"side_effect": OSError("reviewer injected watchdog error")}),
    ("missing_observation", {"return_value": None}),
):
    with tempfile.TemporaryDirectory(prefix="runner-closure-", dir=HERE) as directory:
        with patch.object(runner_module, "_footprint", **options):
            outcome = runner_module.BoundedProcessRunner().run(
                command=(sys.executable, "-I", "-c", "import time; time.sleep(3)"),
                stdin=b"", cwd=Path(directory), environment={"PATH": "/usr/bin:/bin"},
                limits=limits(wall_seconds=0.25),
            )
    assert outcome.termination == "monitoring_failure"
    assert outcome.monitoring_failures == 1
    assert outcome.process_group_cleanup_verified
    assert outcome.exit_status is not None
    results[name] = {
        "termination": outcome.termination,
        "monitoring_failures": outcome.monitoring_failures,
        "monitor_error_type": outcome.monitor_error_type,
        "exit_status": outcome.exit_status,
        "cleanup_verified": outcome.process_group_cleanup_verified,
        "wall_seconds": outcome.wall_seconds,
        "cpu_seconds": outcome.cpu_seconds,
    }
print(json.dumps({
    "producer": "independent M2 reviewer /root/review_m2_provider",
    "recorded_at": datetime.now(timezone.utc).isoformat(),
    "reviewed_revision": "7239379ebf6921fd816b1bded9bb07a685b84c1a",
    "scope": "unit_diagnostic; two trusted short sleepers; no model/inference",
    "command": ["env", "PYTHONPATH=src:tests", ".venv/bin/python", "-B", "docs/evidence/M2/review-round1/check-runner-closure.py"],
    "results": results,
}, indent=2, sort_keys=True))

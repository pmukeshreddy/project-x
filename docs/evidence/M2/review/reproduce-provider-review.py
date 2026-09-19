"""Narrow independent diagnostics; no model import, download, or inference.

Run from the repository root with PYTHONPATH=src:tests .venv/bin/python -B.
The only executed child is trusted Python sleeping for a bounded cleanup probe.
The injected event streams and backend/runner doubles are unit diagnostics only.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from unittest.mock import patch

from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole, StrictModel, Visibility
from feature_rl.generation import GenerationProviderError, LocalGenerationProvider
import feature_rl.generation.runner as runner_module
from test_generation import FakeBackend, FakeRunner, SmokeContent, limits, request, worker_events


ROOT = Path(__file__).resolve().parents[4]
EVIDENCE = Path(__file__).resolve().parent


class StructuredContent(StrictModel):
    values: tuple[str, ...]
    visibility: Visibility


def encoded(events):
    return ("\n".join(json.dumps(item) for item in events) + "\n").encode()


def fixture_events():
    return [json.loads(line) for line in worker_events().splitlines()]


def provider_diagnostic(events, schema=SmokeContent):
    with tempfile.TemporaryDirectory(prefix="provider-diagnostic-", dir=EVIDENCE) as directory:
        store = ArtifactStore(Path(directory) / "objects", ActorRole.AUTHOR)
        provider = LocalGenerationProvider(
            backend=FakeBackend(), archive=store.put_bytes,
            runner=FakeRunner(stdout=encoded(events)),
        )
        try:
            result = provider.generate(request(), schema)
            record = result.record
            detail = {"provider_accepted": True, "content": result.content.model_dump(mode="json")}
        except GenerationProviderError as error:
            record = error.record
            detail = {"provider_accepted": False, "error": str(error)}
        detail["record_success"] = record.success
        detail["usage_archive"] = json.loads(store.get_bytes(record.archives["usage"]))
        detail["status_archive"] = json.loads(store.get_bytes(record.archives["status"]))
        detail["options_archive"] = json.loads(store.get_bytes(record.archives["options"]))
        return detail


def strict_json_transport():
    events = fixture_events()
    content = {"values": ["valid"], "visibility": "authoring"}
    envelope = json.loads(events[-1]["output_text"])
    envelope["content"] = content
    events[-1]["output_text"] = json.dumps(envelope)
    independently_valid = StructuredContent.model_validate_json(json.dumps(content))
    result = provider_diagnostic(events, StructuredContent)
    result["json_transport_accepts_same_content"] = independently_valid.model_dump(mode="json") == content
    result["submitted_content"] = content
    assert result["json_transport_accepts_same_content"] and not result["provider_accepted"]
    return result


def permissive_event_protocol():
    results = {}
    minimal = fixture_events()
    results["missing_identity_and_measurement_fields"] = provider_diagnostic(minimal)
    assert results["missing_identity_and_measurement_fields"]["provider_accepted"]

    changed_identity = fixture_events()
    changed_identity[0].update({
        "config_sha256": "0" * 64,
        "tokenizer_sha256": "0" * 64,
        "weights_sha256": "0" * 64,
        "dependency_versions": {"mlx": "0.0.0"},
        "local_files_only": False,
        "remote_code": True,
        "unexpected": "not in the worker protocol",
    })
    results["contradictory_backend_identity"] = provider_diagnostic(changed_identity)
    assert results["contradictory_backend_identity"]["provider_accepted"]
    results["contradictory_backend_identity"]["injected_identity_event"] = changed_identity[0]

    response = fixture_events()
    response[-1]["response_id"] = "DIFFERENT_RESPONSE"
    results["different_completion_response_id"] = provider_diagnostic(response)
    assert results["different_completion_response_id"]["provider_accepted"]

    positive_score = fixture_events()
    positive_score[-2]["selected_model_logprob"] = 2.0
    results["impossible_positive_log_probability"] = provider_diagnostic(positive_score)
    assert results["impossible_positive_log_probability"]["provider_accepted"]
    return results


def failed_envelope_usage():
    events = fixture_events()
    envelope = json.loads(events[-1]["output_text"])
    envelope["requirement_ids"] = ["UNKNOWN_REQUIREMENT"]
    events[-1]["output_text"] = json.dumps(envelope)
    result = provider_diagnostic(events)
    assert not result["provider_accepted"]
    assert result["usage_archive"].get("accepted_response_usage") is not False
    return result


def monitor_exception_cleanup():
    actual_popen = subprocess.Popen
    children = []

    def capture_popen(*args, **kwargs):
        process = actual_popen(*args, **kwargs)
        children.append(process)
        return process

    result = {}
    try:
        with tempfile.TemporaryDirectory(prefix="runner-diagnostic-", dir=EVIDENCE) as directory:
            with patch.object(runner_module.subprocess, "Popen", capture_popen), patch.object(
                runner_module, "_footprint", side_effect=OSError("injected watchdog observation exception")
            ):
                try:
                    runner_module.BoundedProcessRunner().run(
                        command=(sys.executable, "-I", "-c", "import time; time.sleep(3)"),
                        stdin=b"", cwd=Path(directory), environment={"PATH": "/usr/bin:/bin"},
                        limits=limits(wall_seconds=0.25),
                    )
                except OSError as error:
                    result["exception"] = str(error)
                    result["child_pid"] = children[0].pid
                    result["child_alive_after_runner_raised"] = children[0].poll() is None
                    result["process_group_absent_after_runner_raised"] = runner_module._group_absent(children[0].pid)
    finally:
        for process in children:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except PermissionError:
                process.kill()
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
            result["reviewer_cleanup_exit_status"] = process.returncode
            result["reviewer_cleanup_group_absent"] = runner_module._group_absent(process.pid)
    assert result["child_alive_after_runner_raised"] is True
    assert result["process_group_absent_after_runner_raised"] is False
    assert result["reviewer_cleanup_group_absent"] is True
    return result


def recovered_monitor_loss():
    actual_footprint = runner_module._footprint
    failures = 0

    def fail_once(library, pid):
        nonlocal failures
        if failures == 0:
            failures += 1
            return None
        return actual_footprint(library, pid)

    with tempfile.TemporaryDirectory(prefix="monitor-diagnostic-", dir=EVIDENCE) as directory:
        with patch.object(runner_module, "_footprint", fail_once):
            outcome = runner_module.BoundedProcessRunner().run(
                command=(sys.executable, "-I", "-c", "import time; time.sleep(0.08)"),
                stdin=b"", cwd=Path(directory), environment={"PATH": "/usr/bin:/bin"},
                limits=limits(wall_seconds=1.0),
            )
    result = {
        "injected_observation_failures": failures,
        "termination": outcome.termination,
        "exit_status": outcome.exit_status,
        "memory_samples": outcome.memory_samples,
        "cleanup": outcome.process_group_cleanup_verified,
    }
    assert failures == 1 and outcome.termination == "process_exit" and outcome.memory_samples > 0
    return result


def main():
    paths = sorted((ROOT / "src/feature_rl/generation").glob("*.py"))
    paths.append(ROOT / "tests/test_generation.py")
    result = {
        "scope": "unit_diagnostic; synthetic events and injected monitor failures; no inference",
        "producer": "independent M2 reviewer /root/review_m2_provider",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "reviewed_revision": "8a4637552b87dbf5e7bfe57b0a843c3a59327dad",
        "command": ["env", "PYTHONPATH=src:tests", ".venv/bin/python", "-B", "docs/evidence/M2/review/reproduce-provider-review.py"],
        "product_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
        "strict_json_transport": strict_json_transport(),
        "event_protocol": permissive_event_protocol(),
        "failed_envelope_usage": failed_envelope_usage(),
        "monitor_exception_cleanup": monitor_exception_cleanup(),
        "recovered_monitor_loss": recovered_monitor_loss(),
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

"""Unit diagnostic for publication failure after an observed provider response.

Uses the existing explicit test doubles. No subprocess, model, or inference runs.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile

from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole
from feature_rl.generation import LocalGenerationProvider
from test_generation import FakeBackend, FakeRunner, SmokeContent, request


EVIDENCE = Path(__file__).resolve().parent
ROOT = EVIDENCE.parents[3]


def main():
    runner = FakeRunner()
    published = []
    with tempfile.TemporaryDirectory(prefix="archive-diagnostic-", dir=EVIDENCE) as directory:
        store = ArtifactStore(Path(directory) / "objects", ActorRole.AUTHOR)

        def failing_archive(data, kind, visibility):
            if kind == "generation-response":
                raise OSError("injected archive publication failure")
            ref = store.put_bytes(data, kind, visibility)
            published.append((kind, ref))
            return ref

        provider = LocalGenerationProvider(backend=FakeBackend(), runner=runner, archive=failing_archive)
        try:
            provider.generate(request(), SmokeContent)
        except OSError as error:
            result = {
                "scope": "unit_diagnostic; existing test doubles; no inference or subprocess",
                "producer": "independent M2 reviewer /root/review_m2_provider",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "reviewed_revision": "8a4637552b87dbf5e7bfe57b0a843c3a59327dad",
                "provider_sha256": hashlib.sha256((ROOT / "src/feature_rl/generation/provider.py").read_bytes()).hexdigest(),
                "command": ["env", "PYTHONPATH=src:tests", ".venv/bin/python", "-B", "docs/evidence/M2/review/reproduce-archive-failure.py"],
                "runner_returned_complete_response": len(runner.calls) == 1,
                "exception_type": type(error).__name__,
                "exception_message": str(error),
                "exception_has_call_record": hasattr(error, "record"),
                "published_artifact_kinds": [kind for kind, _ in published],
                "known_diagnostic_measurements_lost_from_api": {
                    "wall_seconds": 1.5, "cpu_seconds": 1.0,
                    "input_tokens": 41, "output_tokens": 1,
                },
            }
            assert result["runner_returned_complete_response"]
            assert not result["exception_has_call_record"]
            assert result["published_artifact_kinds"] == ["generation-request"]
        else:
            raise AssertionError("diagnostic expected publication to fail")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

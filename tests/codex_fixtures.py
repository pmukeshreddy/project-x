"""Explicit Codex subprocess doubles. Never used outside tests."""

import json
from pathlib import Path

from feature_rl.generation import CodexConfig, ProcessOutcome


def config(root):
    executable = root / "test-codex"
    executable.write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        'print("codex-cli test" if "--version" in sys.argv else "Logged in using ChatGPT")\n'
    )
    executable.chmod(0o700)
    return CodexConfig(executable=str(executable))


def events(text, *, input_tokens=41, output_tokens=7):
    return (
        "\n".join(
            json.dumps(event)
            for event in (
                {"type": "thread.started", "thread_id": "test-thread"},
                {"type": "turn.started"},
                {
                    "type": "item.completed",
                    "item": {"id": "item_0", "type": "agent_message", "text": text},
                },
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": input_tokens,
                        "cached_input_tokens": 0,
                        "output_tokens": output_tokens,
                    },
                },
            )
        )
        + "\n"
    ).encode()


def response(request, content):
    return json.dumps(
        {
            "response_id": request.response_id,
            "source_ids": [ctx.context_id for ctx in request.contexts],
            "requirement_ids": list(request.allowed_requirement_ids),
            "content": content,
        }
    )


class CodexRunner:
    def __init__(self, stdout, termination="process_exit", exit_status=0):
        self.stdout, self.termination, self.exit_status = (
            stdout,
            termination,
            exit_status,
        )
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        try:
            texts = [
                event["item"]["text"]
                for event in map(json.loads, self.stdout.splitlines())
                if event.get("type") == "item.completed"
                and event.get("item", {}).get("type") == "agent_message"
            ]
            if texts:
                args = kwargs["command"]
                Path(args[args.index("--output-last-message") + 1]).write_text(
                    texts[-1]
                )
        except (ValueError, KeyError):
            pass  # Malformed transport fixtures deliberately lack a final response file.
        return ProcessOutcome(
            termination=self.termination,
            exit_status=self.exit_status,
            wall_seconds=1.5,
            cpu_seconds=1.0,
            stdout=self.stdout,
            stderr=b"",
            memory_samples=5,
            max_sampled_physical_footprint_bytes=1000,
            max_reported_lifetime_physical_footprint_bytes=1000,
            breach_sample=None,
            process_group_cleanup_verified=True,
        )

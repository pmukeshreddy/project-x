"""Validate the documented Codex exec JSONL completion and its reported usage."""

from feature_rl.artifacts import canonical_json
from .codex import CodexUnavailable
from .models import GenerationUsage
from .schema import _json_no_duplicates


def observed_usage(raw: bytes) -> GenerationUsage | None:
    """Retain one valid completion's reported counts even when the call is rejected."""
    try:
        events = [
            _json_no_duplicates(line) for line in raw.decode("utf-8").splitlines()
        ]
        completed = [
            event
            for event in events
            if isinstance(event, dict) and event.get("type") == "turn.completed"
        ]
        if len(completed) == 1:
            return GenerationUsage.model_validate_json(
                canonical_json(completed[0].get("usage")), strict=True
            )
    except (ValueError, UnicodeError):
        # No trustworthy count is available from malformed/ambiguous telemetry.
        return None
    return None


def parse_events(raw: bytes) -> tuple[str, GenerationUsage]:
    events = [_json_no_duplicates(line) for line in raw.decode("utf-8").splitlines()]
    if not events or any(not isinstance(event, dict) for event in events):
        raise ValueError("Codex emitted no valid JSONL events")
    for event in events:
        if event.get("type") in {"error", "turn.failed"}:
            raise CodexUnavailable(
                f"Codex/Astra failed: {event.get('message', event.get('error'))}"
            )
    if [event.get("type") for event in events[:2]] != [
        "thread.started",
        "turn.started",
    ]:
        raise ValueError("Codex thread/turn start is missing")
    if not isinstance(events[0].get("thread_id"), str) or not events[0]["thread_id"]:
        raise ValueError("Codex thread ID is missing")
    messages = []
    for event in events[2:-1]:
        kind = event.get("type")
        if kind not in {"item.started", "item.updated", "item.completed"}:
            raise ValueError(f"Unexpected Codex event: {kind}")
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") not in {
            "agent_message",
            "reasoning",
        }:
            raise ValueError("Codex authoring attempted a tool or unsupported item")
        if kind == "item.completed" and item["type"] == "agent_message":
            if not isinstance(item.get("text"), str):
                raise ValueError("Codex final message is missing text")
            messages.append(item["text"])
    completed = events[-1]
    if completed.get("type") != "turn.completed":
        raise ValueError(f"Codex/Astra did not complete the turn: {completed}")
    if len(messages) != 1 or not messages[0].strip():
        raise ValueError("Codex must emit exactly one nonempty artifact response")
    usage = GenerationUsage.model_validate_json(
        canonical_json(completed.get("usage")), strict=True
    )
    return messages[0], usage

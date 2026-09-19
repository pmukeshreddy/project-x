"""Validated Click public-interface discovery through the reviewed M3 runtime."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Annotated, Literal

from pydantic import Field, model_validator

from feature_rl.artifacts import canonical_json
from feature_rl.contracts import ArtifactRef, CommandSpec, CostRecord, StrictModel, Text, Visibility
from feature_rl.environments import ExecutionRequest

from .models import GroundedSource


DISCOVERY_CODE = r'''import importlib.metadata, inspect, json
import click
from click.testing import CliRunner

@click.group()
def cli():
    pass

@cli.command()
def status():
    click.echo("ready")

runner = CliRunner()
exact = runner.invoke(cli, ["status"])
unknown = runner.invoke(cli, ["statuz"])
value = {
    "click_version": importlib.metadata.version("click"),
    "module_file": click.__file__,
    "entry_points": ["click.Group", "click.group", "click.command", "click.testing.CliRunner"],
    "supported_observables": ["CLI exit code", "combined terminal output"],
    "resolve_command_signature": str(inspect.signature(click.Group.resolve_command)),
    "exact_command": {
        "exit_code": exact.exit_code,
        "output": exact.output,
        "exception_type": type(exact.exception).__name__ if exact.exception else None,
    },
    "unknown_command": {
        "exit_code": unknown.exit_code,
        "output": unknown.output,
        "exception_type": type(unknown.exception).__name__ if unknown.exception else None,
    },
    "no_such_command_exported": hasattr(click, "NoSuchCommand"),
}
print(json.dumps(value, sort_keys=True, separators=(",", ":")), end="")
'''


class CommandObservation(StrictModel):
    exit_code: int
    output: str
    exception_type: str | None


class ClickProbeObservation(StrictModel):
    click_version: Literal["8.3.3"]
    module_file: Literal["/workspace/site/click/__init__.py"]
    entry_points: tuple[
        Literal[
            "click.Group",
            "click.group",
            "click.command",
            "click.testing.CliRunner",
        ],
        ...,
    ]
    supported_observables: tuple[Literal["CLI exit code", "combined terminal output"], ...]
    resolve_command_signature: Text
    exact_command: CommandObservation
    unknown_command: CommandObservation
    no_such_command_exported: bool

    @model_validator(mode="after")
    def expected_public_boundary(self):
        if self.entry_points != (
            "click.Group",
            "click.group",
            "click.command",
            "click.testing.CliRunner",
        ):
            raise ValueError("discovery entry points differ from the fixed public probe")
        if self.supported_observables != ("CLI exit code", "combined terminal output"):
            raise ValueError("discovery observables differ from the fixed worker adapter boundary")
        if self.exact_command.exit_code != 0 or self.exact_command.output != "ready\n":
            raise ValueError("baseline exact command probe failed")
        if self.unknown_command.exit_code != 2 or "No such command 'statuz'." not in self.unknown_command.output:
            raise ValueError("baseline unknown-command probe did not produce the expected public failure")
        if not all(marker in self.resolve_command_signature for marker in ("self", "ctx", "args")):
            raise ValueError("Group.resolve_command signature is incompatible")
        return self


class ClickDiscoveryObservation(ClickProbeObservation):
    baseline: ArtifactRef
    recipe: ArtifactRef
    build_evidence_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    execution_evidence_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def exact_execution_binding(self):
        if self.baseline.kind != "source-archive" or self.baseline.visibility is not Visibility.AUTHORING:
            raise ValueError("discovery must bind the authoring baseline")
        if self.recipe.kind != "EnvironmentRecipe" or self.recipe.visibility is not Visibility.AUTHORING:
            raise ValueError("discovery must bind the authoring environment recipe")
        return self


class ClickDiscoveryError(RuntimeError):
    """M3 execution or its untrusted discovery output was not admissible."""


@dataclass(frozen=True)
class ClickDiscoveryResult:
    observation: ClickDiscoveryObservation
    context: GroundedSource
    private_evidence: tuple[ArtifactRef, ...]
    costs: tuple[CostRecord, ...]


def _json_no_duplicates(data: bytes) -> object:
    def pairs(items):
        value = {}
        for key, member in items:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = member
        return value

    return json.loads(
        data.decode(),
        object_pairs_hook=pairs,
        parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
    )


class ClickDiscoveryService:
    def __init__(self, *, runtime):
        self.runtime = runtime

    def discover(self, prepared) -> ClickDiscoveryResult:
        handle = self.runtime.open_workspace(prepared, role="baseline")
        try:
            recipe = self.runtime.recipe(prepared)
            build = self.runtime.build_snapshot(handle)
            execution = self.runtime.execute(
                handle,
                ExecutionRequest(
                    command=CommandSpec(
                        argv=("python", "-c", DISCOVERY_CODE),
                        working_directory="/workspace",
                        timeout_seconds=30.0,
                    ),
                    stdin=b"",
                    save_source=False,
                ),
                build=build,
            )
            if (
                execution.reason != "completed"
                or execution.failure_category != "none"
                or execution.exit_code != 0
                or not execution.cleanup_verified
                or execution.oom_killed
            ):
                raise ClickDiscoveryError("installed baseline discovery execution failed")
            if len(execution.stdout) > 64 * 1024:
                raise ClickDiscoveryError("installed baseline discovery output exceeds its cap")
            try:
                decoded = _json_no_duplicates(execution.stdout)
                if canonical_json(decoded) != execution.stdout:
                    raise ValueError("discovery output is not canonical JSON")
                probe = ClickProbeObservation.model_validate_json(execution.stdout)
            except (ValueError, UnicodeError) as error:
                raise ClickDiscoveryError(f"invalid installed baseline discovery output: {error}") from error
            observation = ClickDiscoveryObservation(
                **probe.model_dump(),
                baseline=recipe.baseline,
                recipe=prepared.recipe,
                build_evidence_sha256=build.evidence.sha256,
                execution_evidence_sha256=execution.evidence.sha256,
            )
            payload = canonical_json(observation.model_dump(mode="json"))
            source = self.runtime.publish(
                observation.model_dump(mode="json"),
                "click-runtime-discovery",
                Visibility.AUTHORING,
            )
            context = GroundedSource(
                context_id="M3_CLICK_DISCOVERY",
                role="baseline",
                source=source,
                locator="m3:click-runtime-discovery-v1",
                text=payload.decode(),
                provenance_label="existing_obligation",
            )
            return ClickDiscoveryResult(
                observation=observation,
                context=context,
                private_evidence=(build.evidence, execution.evidence),
                costs=(build.cost, execution.cost),
            )
        finally:
            self.runtime.close(handle)

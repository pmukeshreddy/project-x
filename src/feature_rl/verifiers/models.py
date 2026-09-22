"""Frozen commands, inputs and construction evidence; no generated verifier code."""
from typing import Annotated, Literal
from pydantic import Field, model_validator
from feature_rl.contracts import StrictModel, ArtifactRef, CommandSpec, Costs

Name = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$', max_length=128)]
Names = Annotated[tuple[Name, ...], Field(min_length=1, max_length=128)]
MAX_OUTPUT_BYTES = 262144


def unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError('duplicate '+label)


class BehavioralInput(StrictModel):
    mode: Literal['tests', 'output']
    command: CommandSpec
    stdin: ArtifactRef
    origin: ArtifactRef

    @model_validator(mode='after')
    def test_command(self):
        if self.mode == 'tests' and (self.command.argv[:4] != ('/usr/local/bin/python', '-m', 'pytest', '-q')
                or self.command.working_directory != '/workspace/checks'
                or self.stdin.kind != 'repository-tests'):
            raise ValueError('direct tests require the frozen pytest command and repository files')
        return self


class ProcessObservation(StrictModel):
    exit_code: int
    stdout_hex: str
    stderr_hex: str


class RealizedCase(StrictModel):
    case_id: Name
    requirement_ids: Names
    input_plan: ArtifactRef
    expected: ArtifactRef
    mandatory: bool


class CaseManifest(StrictModel):
    version: Literal['repository-manifest-v1'] = 'repository-manifest-v1'
    task: ArtifactRef
    verifier: ArtifactRef
    case_seed: Annotated[int, Field(ge=0, lt=2**63)]
    cases: tuple[RealizedCase, ...]


class CapturedRun(StrictModel):
    source: ArtifactRef
    build: ArtifactRef
    executions: tuple[ArtifactRef, ...]
    outputs: tuple[ArtifactRef, ...]
    cleanup_verified: Literal[True]


class ReferenceValidation(StrictModel):
    version: Literal['reference-validation-v1'] = 'reference-validation-v1'
    source_pair: ArtifactRef
    environment: ArtifactRef
    inputs: tuple[ArtifactRef, ...]
    expected: tuple[ArtifactRef, ...]
    baseline: CapturedRun
    reference: CapturedRun
    repeated_reference: CapturedRun
    baseline_matches: tuple[bool, ...]
    costs: Costs

"""Closed inert M4 payloads stored through M0's opaque-byte extension points."""
from typing import Annotated, Literal
from pydantic import Field, model_validator
from feature_rl.contracts import StrictModel, EvidenceLink, ArtifactRef

Name = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$', max_length=128)]
String = Annotated[str, Field(max_length=16384)]
Integer = Annotated[int, Field(ge=-(2**63), le=2**63-1)]
Scalar = String | Integer | bool | None
Value = Scalar | Annotated[tuple[Scalar, ...], Field(max_length=256)]
Names = Annotated[tuple[Name, ...], Field(min_length=1, max_length=128)]


def unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError('duplicate '+label)


class Constant(StrictModel):
    kind: Literal['constant']
    value: Value


class Choice(StrictModel):
    kind: Literal['choice']
    values: Annotated[tuple[Scalar, ...], Field(min_length=1,max_length=256)]


class IntegerDomain(StrictModel):
    kind: Literal['integer']
    low: Integer
    high: Integer

    @model_validator(mode='after')
    def ordered(self):
        if self.low>self.high:raise ValueError('reversed integer domain')
        return self


class InputField(StrictModel):
    name: Name
    domain: Annotated[Constant | Choice | IntegerDomain, Field(discriminator='kind')]


class InputPlan(StrictModel):
    version: Literal['m4-input-v1']
    scenario_id: Name
    requirement_ids: Names
    fields: Annotated[tuple[InputField,...],Field(max_length=64)]

    @model_validator(mode='after')
    def distinct(self):
        unique([f.name for f in self.fields],'input names')
        unique(self.requirement_ids,'requirements')
        return self


class ObservationField(StrictModel):
    name: Name
    type: Literal['string','integer','boolean','null','string_list','integer_list','boolean_list']


class LiteralOperand(StrictModel):
    kind: Literal['literal']
    value: Value


class InputOperand(StrictModel):
    kind: Literal['input']
    name: Name
    prefix: String = ''
    suffix: String = ''


class ObservationOperand(StrictModel):
    kind: Literal['observation']
    name: Name


Operand = Annotated[LiteralOperand | InputOperand | ObservationOperand,Field(discriminator='kind')]


class Assertion(StrictModel):
    assertion_id: Name
    requirement_ids: Names
    oracle_origin: EvidenceLink
    actual: Name
    operator: Literal['equal','contains','member']
    expected: Operand

    @model_validator(mode='after')
    def distinct(self):
        unique(self.requirement_ids,'assertion requirements')
        return self


class CaseComparison(StrictModel):
    version: Literal['m4-comparison-v1']
    scenario_id: Name
    requirement_ids: Names
    mode: Literal['json','process']
    observations: Annotated[tuple[ObservationField,...],Field(min_length=1,max_length=64)]
    assertions: Annotated[tuple[Assertion,...],Field(min_length=1,max_length=128)]
    timeout_seconds: Annotated[float,Field(gt=0,le=60)]

    @model_validator(mode='after')
    def distinct(self):
        unique(self.requirement_ids,'comparison requirements')
        unique([x.name for x in self.observations],'observations')
        unique([x.assertion_id for x in self.assertions],'assertion IDs')
        names={x.name for x in self.observations}
        if names & {'passed','reward','verdict','skip'}:raise ValueError('verdict observation forbidden')
        if self.mode=='process' and any((x.name,x.type) not in {('exit_code','integer'),('stdout','string'),('stderr','string')} for x in self.observations):
            raise ValueError('unsupported process observation')
        for assertion in self.assertions:
            if assertion.actual not in names:raise ValueError('undeclared actual observation')
            if isinstance(assertion.expected,ObservationOperand) and assertion.expected.name not in names:
                raise ValueError('undeclared expected observation')
        return self


class RealizedCase(StrictModel):
    case_id: Name
    scenario_id: Name
    requirement_ids: Names
    inputs: dict[Name,Value]
    input_plan: ArtifactRef
    comparison: ArtifactRef
    mandatory: bool


class CaseManifest(StrictModel):
    version: Literal['m4-manifest-v1']
    task: ArtifactRef
    verifier: ArtifactRef
    case_seed: Annotated[int,Field(ge=0,le=2**63-1)]
    algorithm: Literal['m4-sha256-v1']
    cases: Annotated[tuple[RealizedCase,...],Field(min_length=1,max_length=256)]

    @model_validator(mode='after')
    def distinct(self):
        unique([c.case_id for c in self.cases],'manifest cases')
        return self

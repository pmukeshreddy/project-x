"""Compile semantic action/expectation specifications into private typed checks."""
from typing import Annotated
from functools import lru_cache

from pydantic import Field, create_model, model_validator
from feature_rl.artifacts import canonical_json
from feature_rl.contracts import StrictModel
from .authoring_models import CaseProposal, CheckerProposal, WorkerProposal
from .models import (Assertion, CaseComparison, Constant, Choice, InputField,
    InputPlan, LiteralOperand, InputOperand, Name, ObservationField, unique)
from .probe_adapter import build_probe_adapter, validate_actions


class ExpectedObservation(StrictModel):
    name: Name
    value: LiteralOperand | InputOperand


class BehavioralCase(StrictModel):
    actions: Annotated[str, Field(min_length=1, max_length=8192)]
    inputs: Annotated[tuple[InputField, ...], Field(max_length=64)]
    expected: Annotated[tuple[ExpectedObservation, ...], Field(min_length=1, max_length=64)]

    @model_validator(mode='after')
    def valid_actions(self):
        validate_actions(self.actions)
        unique([field.name for field in self.inputs], 'input names')
        unique([field.name for field in self.expected], 'expected observations')
        if {field.name for field in self.expected} & {'passed', 'reward', 'verdict', 'skip'}:
            raise ValueError('verdict observations are forbidden')
        return self


class BehavioralScenario(StrictModel):
    cases: Annotated[tuple[BehavioralCase, ...], Field(min_length=1, max_length=4)]


class BehavioralSpecification(StrictModel):
    # Position binds each group to its controller-assigned scenario. Astra never
    # emits IDs, provenance, runtime settings, observation types or comparators.
    scenarios: Annotated[tuple[BehavioralScenario, ...], Field(min_length=1, max_length=64)]

    @model_validator(mode='after')
    def bounded(self):
        if len(canonical_json(self.model_dump(mode='json'))) > 131072:
            raise ValueError('behavioral specification exceeds 128 KiB')
        return self


def behavioral_schema(plan):
    count = len(plan.scenarios)
    if not 1 <= count <= 64:
        raise ValueError('behavioral checker supports 1 to 64 scenarios')
    return _schema(count)


@lru_cache(maxsize=64)
def _schema(count):
    return create_model('BehavioralSpecification', __base__=BehavioralSpecification,
        scenarios=(tuple[BehavioralScenario, ...], Field(min_length=count, max_length=count)))


def _value_type(value):
    scalars = {str: 'string', int: 'integer', bool: 'boolean', type(None): 'null'}
    if type(value) in scalars:
        return scalars[type(value)]
    if isinstance(value, tuple):
        kinds = {_value_type(item) for item in value}
        if len(kinds) > 1 or kinds - {'string', 'integer', 'boolean'}:
            raise ValueError('expected lists must have one scalar type')
        # An empty equality target accepts only an empty list, regardless of
        # which homogeneous list type represents it in the existing transport.
        return next(iter(kinds), 'string') + '_list'
    raise ValueError('unsupported expected value')


def _observation_type(expected, fields):
    if isinstance(expected, LiteralOperand):
        return _value_type(expected.value)
    domains = {field.name: field.domain for field in fields}
    if expected.name not in domains:
        raise ValueError('unknown expected input')
    domain = domains[expected.name]
    values = [domain.value] if isinstance(domain, Constant) else domain.values if isinstance(domain, Choice) else (domain.low, domain.high)
    kinds = {_value_type(value) for value in values}
    if len(kinds) != 1:
        raise ValueError('expected input domain must have one type')
    kind = kinds.pop()
    if (expected.prefix or expected.suffix) and kind != 'string':
        raise ValueError('input text templates require strings')
    return kind


def compile_behavioral(specification, contract, plan, *, timeout_seconds):
    """Fixed equality checks; the controller infers types from expected values."""
    specification = behavioral_schema(plan).model_validate_json(
        canonical_json(specification.model_dump(mode='json')))
    if type(timeout_seconds) not in {int, float} or not 0 < timeout_seconds <= 30:
        raise ValueError('invalid checker timeout')
    requirements = {r.requirement_id: r for r in contract.requirements + contract.compatibility_obligations}
    cases, dispatch = [], []
    for index, (group, scenario) in enumerate(zip(specification.scenarios, plan.scenarios), 1):
        if len(scenario.requirement_ids) != 1 or scenario.requirement_ids[0] not in requirements or scenario.reset_needs:
            raise ValueError('each scenario must bind one known self-contained requirement')
        requirement = requirements[scenario.requirement_ids[0]]
        for number, case in enumerate(group.cases, 1):
            identity = f'check_{index}_{number}'
            observations = tuple(ObservationField(name=name, type=_observation_type(value, case.inputs))
                for name, value in ((field.name, field.value) for field in case.expected))
            inputs = InputPlan(version='m4-input-v1', scenario_id=scenario.scenario_id,
                requirement_ids=scenario.requirement_ids, fields=case.inputs)
            comparison = CaseComparison(version='m4-comparison-v1', scenario_id=scenario.scenario_id,
                requirement_ids=scenario.requirement_ids, mode='json', observations=observations,
                assertions=tuple(Assertion(assertion_id=f'{identity}_{i}', requirement_ids=scenario.requirement_ids,
                    actual=name, operator='equal', expected=expected, oracle_origin=scenario.oracle_origin)
                    for i, (name, expected) in enumerate(((field.name, field.value) for field in case.expected), 1)),
                timeout_seconds=min(float(timeout_seconds), contract.episode_limits.wall_seconds))
            cases.append(CaseProposal(case_id=identity, requirement_ids=scenario.requirement_ids,
                mandatory=requirement.mandatory, inputs=inputs, comparison=comparison))
            dispatch.append((identity, case.actions, tuple(f.name for f in case.inputs),
                tuple((field.name, field.type) for field in observations)))
    return CheckerProposal(cases=tuple(cases), worker_adapter=WorkerProposal(
        source=build_probe_adapter(dispatch), supported_observables=('json',),
        limitations=('Stateless public API observations; private controller comparisons.',)))

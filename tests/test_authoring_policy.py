"""Authoring guidance and early capability gates; no model or runtime execution."""
import pytest

from feature_rl.contracts import Visibility
from feature_rl.scenarios import ScenarioFinalizer, ScenarioJoinError, build_scenario_request
from feature_rl.verifiers import build_checker_request
from test_authoring import (contract_request, finalized_contract, generation_limits,
    ref, scenario_inputs, scenario_proposal, sources)


def scenario_request():
    contract = finalized_contract()
    return build_scenario_request(request_id='POLICY_SCENARIO', response_id='POLICY_RESPONSE',
        prompt_id='POLICY_PROMPT', contract=contract, contract_ref=ref('RequirementContract', 'f'),
        sources=sources(), limits=generation_limits())


def checker_request():
    contract = finalized_contract()
    contract_ref = ref('RequirementContract', 'f')
    plan = ScenarioFinalizer().finalize(scenario_proposal(), contract,
        scenario_inputs(contract_ref), sources(), expected_contract=contract_ref)
    return build_checker_request(request_id='POLICY_CHECKER', response_id='POLICY_RESPONSE',
        prompt_id='POLICY_PROMPT', contract=contract, contract_ref=contract_ref, plan=plan,
        plan_ref=ref('ScenarioPlan', 'e', Visibility.PRIVATE), sources=sources(), limits=generation_limits())


def test_contract_prompt_preserves_behavior_while_factoring_requirement_ownership():
    instruction = contract_request().instruction
    for constraint in (
        'minimal cohesive set', 'independently falsifiable', 'explicit API and input scope',
        'residual behavior', 'merge inseparable', 'Do not drop requested behavior',
        'baseline B through existing interfaces', 'without the new feature',
    ):
        assert constraint in instruction


def test_scenario_prompt_declares_exact_observables_and_current_reset_capability():
    instruction = scenario_request().instruction
    assert 'Supported observable labels: ["combined terminal output","CLI exit code"]' in instruction
    for constraint in ('reset_needs=[]', 'fresh runtime and process', 'newly created fixture objects',
                       'no additional reset capability', 'preconditions', 'within one case'):
        assert constraint in instruction


@pytest.mark.parametrize('build_request', [scenario_request, checker_request])
def test_scenario_and_checker_use_primary_evidence_and_property_specific_attribution(build_request):
    instruction = build_request().instruction
    for constraint in ('request, baseline, or public_check',
                       'specifications, not admissible EvidenceLink oracle sources',
                       'only the requirement IDs whose properties', 'compatibility',
                       'existing interfaces', 'absent'):
        assert constraint in instruction
    assert 'FROZEN_CONTRACT' in instruction
    if build_request is checker_request:
        assert 'FROZEN_SCENARIO' in instruction
        assert 'ordinary observations' in instruction
        assert 'Do not copy every scenario ID onto every assertion' in instruction


def test_checker_prompt_preserves_transport_and_deterministic_raw_observations():
    instruction = checker_request().instruction
    for constraint in (
        'function-local', 'before importing candidate', 'read the input envelope',
        'captured serializer', 'mutable module globals', 'exact built-in',
        'custom subclasses', '__str__', 'to_json', 'expected values',
        'byte-identical', 'realized inputs', 'fixed clock',
        'Do not mock the required feature behavior',
    ):
        assert constraint.lower() in instruction.lower()


@pytest.mark.parametrize('reset_need', [
    'Discard objects and recreate fixtures for the next case.',
    'Reset an external database between cases.',
])
def test_scenario_finalizer_rejects_unsupported_resets_before_publishing_plan(reset_need):
    contract = finalized_contract()
    contract_ref = ref('RequirementContract', 'f')
    proposal = scenario_proposal()
    proposal = proposal.model_copy(update={'scenarios': (
        proposal.scenarios[0].model_copy(update={'reset_needs': (reset_need,)}),
        *proposal.scenarios[1:],
    )})
    before = proposal.model_dump_json()
    with pytest.raises(ScenarioJoinError, match='unsupported reset_needs.*no additional reset capability'):
        ScenarioFinalizer().finalize(proposal, contract, scenario_inputs(contract_ref),
            sources(), expected_contract=contract_ref)
    assert proposal.model_dump_json() == before

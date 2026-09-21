"""Wrong implementation authoring through the real parser and a fake model transport."""
import json
import pytest
from feature_rl import contracts as c
from feature_rl.verifiers import (ControlFinalizationInputs, ControlProposal, SourceChange,
    TextReplacement, ControlAuthoringService, ControlFinalizer, build_control_request)
from test_checker_authoring import checker_fixture, configured_diagnostic_provider
from test_generation import limits


def control_fixture(tmp_path):
    store, task, checker, base, sources, resolver = checker_fixture(tmp_path)
    inputs = ControlFinalizationInputs(control_id='wrong-1', category='partial', requirement_ids=('echo',),
        expected_reason='Implement only part of the feature', baseline=base.baseline,
        contract=base.contract, environment=base.environment, provenance=base.provenance, costs=base.costs)
    proposal = ControlProposal(files=(SourceChange(path='src/click/__init__.py', replacements=(
        TextReplacement(before='# diagnostic', after='# incomplete implementation'),)),),
        deletions=(), rationale='Partial behavior')
    request = build_control_request(request_id='CONTROL_1', response_id='RESPONSE_1', prompt_id='PROMPT_1',
        store=store, resolver=resolver, inputs=inputs, sources=sources, limits=limits())
    provider, runner = configured_diagnostic_provider(store, request, proposal)
    service = ControlAuthoringService(provider=provider, store=store, resolver=resolver,
        revision='a'*40, evidence_scope='unit_diagnostic')
    return store, inputs, sources, resolver, proposal, request, runner, service


def test_wrong_implementation_publishes_private_source_with_real_model_cost(tmp_path):
    store, inputs, sources, _, proposal, request, runner, service = control_fixture(tmp_path)
    result = service.generate((request,), inputs, sources)
    assert len(runner.calls) == 1
    assert result.control.patch.visibility is c.Visibility.PRIVATE
    assert result.control.category == 'partial'
    assert result.record.costs[-1].input_tokens == 41
    assert json.loads(store.get_bytes(result.journal_refs[-1]))['status'] == 'accepted'
    assert all(context.role != 'reference' for context in request.contexts)


def test_illegal_source_cannot_be_published(tmp_path):
    store, inputs, sources, resolver, proposal, *_ = control_fixture(tmp_path)
    from feature_rl.environments import SourceRejected
    proposal = proposal.model_copy(update={'files': (SourceChange(path='controller_checks/check.py',
        replacements=(TextReplacement(before='', after='pass'),)),)})
    with pytest.raises(SourceRejected):
        ControlFinalizer(store=store, resolver=resolver).prepare(proposal, inputs, sources)


def test_wrong_prompt_has_no_exact_requirement_isolation_or_alternative_positive(tmp_path):
    *_, request, runner, service = control_fixture(tmp_path)
    assert 'one or several requirements' in request.instruction
    assert 'satisfy every other' not in request.instruction
    assert request.stage.value == 'control_authoring'


@pytest.mark.parametrize('replacements', [
    (('# diagnostic', '# diagnostic'),),
    (('# diagnostic', '# changed'), ('# changed', '# diagnostic')),
])
def test_noop_source_changes_cannot_count_as_wrong_implementations(tmp_path, replacements):
    store, inputs, sources, resolver, proposal, *_ = control_fixture(tmp_path)
    proposal = proposal.model_copy(update={'files': (SourceChange(path='src/click/__init__.py',
        replacements=tuple(TextReplacement(before=before,after=after) for before,after in replacements)),)})
    with pytest.raises(ValueError, match='change the baseline'):
        ControlFinalizer(store=store, resolver=resolver).prepare(proposal, inputs, sources)

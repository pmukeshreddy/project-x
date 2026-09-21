"""Audited transport correction at the real provider/Registry boundary; no model calls."""
import json

import pytest

from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.pipeline import authoring as a
from feature_rl.pipeline.authoring_models import AuthoringBudgetExceeded
from feature_rl.requirements import GenerationCandidate
from feature_rl.verifiers.fragments import CheckerFragmentInputs, build_fragment_request
from codex_fixtures import events, response
from test_factory_authoring import setup, repaired, bind_events


PROPOSAL = {'cases': [{'actions': "return {'output': inputs['word']}\n",
    'inputs': [{'name': 'word', 'domain': {'kind': 'choice', 'values': ['red', 'blue']}}],
    'observations': [{'name': 'output', 'type': 'string'}],
    'assertions': [{'requirement_ids': ['echo'], 'actual': 'output', 'operator': 'equal',
        'expected': {'kind': 'input', 'name': 'word', 'prefix': '', 'suffix': ''}}]}]}


def fragment_call(factory, base, scenario_id='s0'):
    inputs = CheckerFragmentInputs(**base.inputs.model_dump(include={
        'contract', 'scenario_plan', 'baseline', 'environment', 'visibility', 'provenance', 'costs'}),
        scenario_id=scenario_id)
    request = build_fragment_request(request_id='fragment-' + scenario_id,
        response_id='response-' + scenario_id, prompt_id='prompt-' + scenario_id,
        contract=factory.store.get_artifact(inputs.contract), contract_ref=inputs.contract,
        plan=factory.store.get_artifact(inputs.scenario_plan), plan_ref=inputs.scenario_plan,
        scenario_id=scenario_id, sources=base.sources, limits=base.generation.request.limits)
    return base.model_copy(update={'inputs': inputs, 'generation': GenerationCandidate(request=request)})


def failed_events(reason='max_output_tokens', *, output=False):
    rows = [{'type': 'thread.started', 'thread_id': 'test-thread'}, {'type': 'turn.started'}]
    if output:
        rows.append({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'partial'}})
    message = 'stream disconnected before completion: Incomplete response returned, reason: ' + reason
    rows.extend(({'type': 'error', 'message': message}, {'type': 'turn.failed', 'error': {'message': message}}))
    return b'\n'.join(canonical_json(row) for row in rows) + b'\n'


def correction(call):
    old = call.generation.request
    request = old.model_copy(update={'request_id': 'transport-correction', 'response_id': 'corrected-response',
        'prompt_id': 'corrected-prompt', 'instruction': old.instruction + '\nController transport schema now omits unsupported unsafe numeric bounds; exact local checks remain.'})
    return call.model_copy(update={'generation': GenerationCandidate(request=request,
        diagnosis='Archived terminal max_output_tokens without output was caused by unsupported remote int64 schema bounds.',
        changed_input='Controller transport schema omits unsafe numeric bounds; behavioral inputs and authoritative local schema are unchanged.')})


def exhausted_fixture(tmp_path, monkeypatch, *, old_schema=True, reason='max_output_tokens', output=False):
    factory, candidate, base, runner = setup(tmp_path, monkeypatch)
    # Exactly five calls are affordable: three checker calls, one failed fragment,
    # and one transport correction. The failure remains a charged reservation.
    batch = factory.authoring.batch
    factory.authoring = factory.authoring.model_copy(update={'batch': batch.model_copy(update={
        'candidate_caps': batch.candidate_caps.model_copy(update={'commands': 5})})})
    factory.author(candidate, call=base)
    for index in (1, 2):
        changed = repaired(base, index)
        bind_events(runner, changed.generation.request)
        factory.author(candidate, call=changed)
    call = fragment_call(factory, base)
    runner.stdout, runner.exit_status = failed_events(reason, output=output), 1
    import feature_rl.generation.provider as provider
    current = provider.codex_request_schema
    def legacy(request, schema):
        value = current(request, schema)
        value['$defs']['IntegerDomain']['properties']['low']['minimum'] = -(2**63)
        value['$defs']['IntegerDomain']['properties']['high']['maximum'] = 2**63 - 1
        return value
    with monkeypatch.context() as old:
        if old_schema:
            old.setattr(provider, 'codex_request_schema', legacy)
        failed = factory.author(candidate, call=call)
    assert failed.disposition == c.Disposition.REJECTED
    assert len(runner.calls) == 4
    changed = correction(call)
    runner.stdout, runner.exit_status = events(response(changed.generation.request, PROPOSAL)), 0
    return factory, candidate, call, changed, failed, runner


def test_changed_transport_recovers_without_erasing_repairs_costs_or_reservations(tmp_path, monkeypatch):
    factory, candidate, call, changed, failed, runner = exhausted_fixture(tmp_path, monkeypatch)
    prior = a.read_authoring_receipt(factory.store, failed.artifacts[-1])
    failed_accounting = factory.registry.accounting(prior.claim.job_id)
    result = factory.author(candidate, call=changed)
    assert result.disposition == c.Disposition.SUCCESS
    receipt = a.read_authoring_receipt(factory.store, result.artifacts[-1])
    selected = next(request for job, request in a.jobs(factory, candidate) if job.result == result)
    assert selected.previous == prior.request and not selected.repair
    assert receipt.journal_refs[:-1] == prior.journal_refs and len(receipt.journal_refs) == 2
    assert sum(request.repair for _, request in a.jobs(factory, candidate)) == 2
    assert len(a.jobs(factory, candidate)) == 5 and len(runner.calls) == 5
    assert factory.registry.accounting(prior.claim.job_id) == failed_accounting
    assert factory.author(candidate, call=changed) == result
    a.validate_receipt(factory, selected, receipt)
    with pytest.raises(AuthoringBudgetExceeded, match='commands'):
        factory.author(candidate, call=fragment_call(factory, call, 's1'))
    assert len(runner.calls) == 5


@pytest.mark.parametrize('change', ['same_schema', 'authentication', 'partial_output', 'ids_only', 'changed_inputs'])
def test_no_repair_budget_exemption_without_exact_transport_failure(tmp_path, monkeypatch, change):
    factory, candidate, call, changed, failed, runner = exhausted_fixture(tmp_path, monkeypatch,
        old_schema=change != 'same_schema', reason='authentication_failed' if change == 'authentication' else 'max_output_tokens',
        output=change == 'partial_output')
    if change == 'ids_only':
        changed = changed.model_copy(update={'generation': changed.generation.model_copy(update={
            'request': changed.generation.request.model_copy(update={'instruction': call.generation.request.instruction})})})
    elif change == 'changed_inputs':
        changed = changed.model_copy(update={'inputs': changed.inputs.model_copy(update={'visibility': c.Visibility.EVALUATION})})
    with pytest.raises(ValueError, match='budget|meaningful'):
        factory.author(candidate, call=changed)
    assert len(runner.calls) == 4


def test_forged_nonrepair_receipt_is_rejected_on_revalidation(tmp_path, monkeypatch):
    factory, candidate, call, changed, failed, runner = exhausted_fixture(tmp_path, monkeypatch, old_schema=False)
    # A fabricated repair=False cannot convert a same-schema failure into an
    # authorized controller correction when an artifact consumer revalidates it.
    prior = a.read_authoring_receipt(factory.store, failed.artifacts[-1])
    old = next(request for job, request in a.jobs(factory, candidate) if job.result == failed)
    forged = old.model_copy(update={'previous': prior.request, 'repair': False, 'call': changed})
    with pytest.raises(ValueError, match='controller|transport|replacement'):
        a.validate_receipt(factory, forged, prior.model_copy(update={'repair': False}))

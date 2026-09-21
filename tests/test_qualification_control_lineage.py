"""Historical M6 lineage diagnostics; TEST provider output is never real evidence."""
import json
from types import SimpleNamespace

import pytest

from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.pipeline import Factory
from feature_rl.pipeline.authoring import read_authoring_receipt
from feature_rl.pipeline.authoring_history import selected_history
from feature_rl.pipeline.authoring_models import ControlPlan, ControlSlot
from feature_rl.pipeline.construction import ConstructionRequest
from feature_rl.pipeline.models import BuildInputs
from feature_rl.pipeline.packaging import read_record
from feature_rl.qualification import QualificationPolicy, QualificationRejected, RepairHistory, validate_repairs
from feature_rl.qualification.controls import control_origins
from feature_rl.requirements import AuthoringEvidenceResolver, GenerationCandidate
from feature_rl.verifiers import (CheckerProposal, ControlFinalizationInputs, ControlProposal,
    SourceEdit, TextReplacement, build_checker_request, build_control_request)
from feature_rl.verifiers.control_authoring import ControlRecord
from test_checker_authoring import configured_diagnostic_provider
from test_factory_authoring import setup, contract_call, scenario_call, repaired, bind_events


@pytest.fixture(scope='module')
def lineage(tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        factory, candidate, base, runner = setup(tmp_path_factory.mktemp('control-lineage'), patch)
        checker_content = next(json.loads(event['item']['text'])['content'] for event in
            (json.loads(line) for line in runner.stdout.splitlines()) if event['type'] == 'item.completed')
        contract = factory.author(candidate, call=contract_call(factory, base, runner)).artifacts[0]
        current = base.model_copy(update={'inputs': base.inputs.model_copy(update={'contract': contract})})
        scenarios = factory.author(candidate, call=scenario_call(factory, current, runner)).artifacts[0]
        control_inputs = ControlFinalizationInputs(control_id='TEST_HISTORY', category='omission',
            requirement_ids=('echo',), expected_valid=False, expected_reason='TEST diagnostic only',
            baseline=base.inputs.baseline, contract=contract, environment=base.inputs.environment,
            provenance=base.inputs.provenance.model_copy(update={'inputs': (*base.inputs.provenance.inputs, contract)}),
            costs=base.inputs.costs)
        request = build_control_request(request_id='LINEAGE_CONTROL', response_id='LINEAGE_CONTROL_RESPONSE',
            prompt_id='LINEAGE_CONTROL_PROMPT', store=factory.store,
            resolver=AuthoringEvidenceResolver(store=factory.store, **base.resolver.model_dump()),
            inputs=control_inputs, sources=base.sources, limits=base.generation.request.limits)
        proposal = ControlProposal(files=(SourceEdit(path='src/click/__init__.py', replacements=(
            TextReplacement(before='# diagnostic', after='# TEST history control\n'),)),),
            deletions=(), rationale='Synthetic unit diagnostic, not qualified semantic evidence')
        _, prepared = configured_diagnostic_provider(factory.store, request, proposal)
        runner.stdout = prepared.stdout
        call = base.model_copy(update={'inputs': control_inputs, 'generation': GenerationCandidate(request=request),
            'control_plan': ControlPlan(contract=contract,
                slots=(ControlSlot(category='omission', requirement_ids=('echo',)),))})
        result = factory.author(candidate, call=call)
        record = read_record(factory.store, result.artifacts[0], ControlRecord, 'm4-control-record')
        receipt_ref = result.artifacts[-1]
        receipt = read_authoring_receipt(factory.store, receipt_ref)
        inputs = current.inputs.model_copy(update={'scenario_plan': scenarios, 'controls': (record.control,),
            'provenance': current.inputs.provenance.model_copy(update={
                'inputs': (*current.inputs.provenance.inputs, contract, scenarios)})})
        request = build_checker_request(request_id='LINEAGE_CHECKER', response_id='LINEAGE_CHECKER_RESPONSE',
            prompt_id='LINEAGE_CHECKER_PROMPT', contract=factory.store.get_artifact(contract), contract_ref=contract,
            plan=factory.store.get_artifact(scenarios), plan_ref=scenarios, sources=base.sources,
            limits=base.generation.request.limits)
        _, prepared = configured_diagnostic_provider(factory.store, request,
            CheckerProposal.model_validate_json(canonical_json(checker_content)))
        runner.stdout = prepared.stdout
        call = current.model_copy(update={'inputs': inputs, 'generation': GenerationCandidate(request=request)})
        factory.author(candidate, call=call)
        later = Factory(store=factory.store, registry=factory.registry, revision='f' * 40,
            authoring=factory.authoring.model_copy(update={'m4_revision': 'b' * 40}))
        changed = repaired(call, 1)
        bind_events(runner, changed.generation.request)
        verifier_ref = later.author(candidate, call=changed).artifacts[0]
        build = BuildInputs(source_pair=base.source_pair, contract=contract, scenario_plan=scenarios,
            verifier=verifier_ref, environment=base.environment, baseline_files=('src/click/__init__.py',),
            invocation='TEST_CROSS_REVISION_HISTORY')
        history_ref = selected_history(later, ConstructionRequest(candidate=candidate,
            source=later.screen_source(candidate).artifacts[0], inputs=build, builder_job=None))
        history = read_record(later.store, history_ref, RepairHistory, 'm5-repair-history')
        assert history.complete and validate_repairs(history, candidate, ()) == 1
        assert len(runner.calls) == 5
        checked = SimpleNamespace(verifier=later.store.get_artifact(verifier_ref),
            contract=later.store.get_artifact(contract), task=SimpleNamespace(source_pair=base.source_pair,
                baseline=base.inputs.baseline, contract=contract, environment=base.inputs.environment))
        service = SimpleNamespace(store=later.store, registry=later.registry,
            policy=QualificationPolicy(repair_history=history_ref, factory_revision=later.revision))
        yield service, checked, receipt_ref, receipt, record


def test_changed_factory_revision_preserves_historical_control_lineage_without_trusting_diagnostics(lineage, monkeypatch):
    service, checked, _, receipt, record = lineage
    from feature_rl.pipeline import authoring
    original = authoring.archived_outcome
    validated = []
    def replay(*args):
        result = original(*args)
        validated.append(args[2])
        return result
    monkeypatch.setattr(authoring, 'archived_outcome', replay)
    assert receipt.revision != service.policy.factory_revision
    assert record.generation_provenance.producer_version == 'a' * 40
    assert checked.verifier.provenance.producer_version == 'b' * 40
    assert control_origins(service, checked) == {}  # Unit diagnostics cannot establish real authorship.
    assert validated == [receipt.claim]


def test_history_does_not_supply_an_unselected_control(lineage, monkeypatch):
    service, checked, *_ = lineage
    from feature_rl.pipeline import authoring
    def unexpected(*args):
        raise AssertionError('unselected control must not reach provider replay')
    monkeypatch.setattr(authoring, 'archived_outcome', unexpected)
    unselected = SimpleNamespace(**{**vars(checked),
        'verifier': checked.verifier.model_copy(update={'controls': ()})})
    assert control_origins(service, unselected) == {}


@pytest.mark.parametrize('target', ['receipt_revision', 'policy_revision', 'component_revision'])
def test_historical_control_still_rejects_its_own_revision_mismatch(lineage, monkeypatch, target):
    service, checked, receipt_ref, receipt, _ = lineage
    from feature_rl.verifiers import loader
    original = loader.read_bytes
    configuration = service.registry.job(receipt.claim.job_id).spec.configuration
    def corrupted(store, ref, *args, **kwargs):
        raw = original(store, ref, *args, **kwargs)
        if target == 'receipt_revision' and ref == receipt_ref:
            value = json.loads(raw)
            value['revision'] = '0' * 40
            return canonical_json(value)
        if target != 'receipt_revision' and ref == configuration:
            value = json.loads(raw)
            if target == 'policy_revision': value['revision'] = '0' * 40
            else: value['settings']['m4_revision'] = '0' * 40
            return canonical_json(value)
        return raw
    monkeypatch.setattr(loader, 'read_bytes', corrupted)
    with pytest.raises(QualificationRejected, match='control authorship'):
        control_origins(service, checked)

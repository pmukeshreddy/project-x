"""TEST source/control proposals through actual provider; no native inference or H."""
import json
import pytest
from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.requirements import GenerationCandidate
from feature_rl.verifiers import (ControlFinalizationInputs, ControlProposal, SourceEdit,
    ControlAuthoringService, ControlFinalizer, ControlPublicationPending, ReferenceExcerpt,
    build_control_request)
from test_checker_authoring import checker_fixture, configured_diagnostic_provider
from test_generation import limits


def control_fixture(tmp_path, *, alternative=False):
    store, task, checker, base, sources, resolver = checker_fixture(tmp_path)
    inputs = ControlFinalizationInputs(control_id='alt' if alternative else 'omit',
        category='alternative_positive' if alternative else 'omission', requirement_ids=() if alternative else ('echo',),
        expected_valid=alternative, expected_reason='DIAGNOSTIC ONLY; semantic validity unverified',
        baseline=base.baseline, contract=base.contract, environment=base.environment,
        provenance=base.provenance, costs=base.costs)
    proposal = ControlProposal(files=(SourceEdit(path='src/click/__init__.py', source='raise AssertionError("must never execute on host")\n'),), deletions=(), rationale='DIAGNOSTIC ONLY; no independence or semantic claim')
    request = build_control_request(request_id='CONTROL_1', response_id='RESPONSE_1', prompt_id='PROMPT_1', store=store, resolver=resolver, inputs=inputs, sources=sources, limits=limits(), seed=0)
    provider, runner = configured_diagnostic_provider(store, request, proposal)
    service = ControlAuthoringService(provider=provider, store=store, resolver=resolver, revision='a'*40, evidence_scope='unit_diagnostic')
    return store, inputs, sources, resolver, proposal, request, runner, service


@pytest.mark.parametrize('alternative', [False, True])
def test_actual_control_stage_source_delta_cost_and_authorship(tmp_path, alternative):
    store, inputs, sources, resolver, proposal, request, runner, service = control_fixture(tmp_path, alternative=alternative)
    result = service.generate((GenerationCandidate(request=request),), inputs, sources)
    assert len(runner.calls)==1 and result.control.patch.kind=='m4-submission'
    assert result.control.expected_valid is alternative
    assert result.record.qualification=='unverified'
    assert result.record.costs[-1] == result.generation.cost
    assert json.loads(store.get_bytes(result.record_ref))['qualification']=='unverified'
    from feature_rl.verifiers.finalize import submission_service
    archive = submission_service(store, inputs.environment).resolve(result.control.patch, inputs.baseline, store.get_artifact(inputs.contract).allowed_changes)
    assert archive.files['src/click/__init__.py'].data == proposal.files[0].source.encode()
    if alternative:
        assert request.stage.value=='alternative_authoring'
        assert {context.role for context in request.contexts} == {'baseline','contract'}
        assert set(result.control.author_provenance.inputs)=={inputs.baseline,inputs.contract}
        assert all(context.source.visibility in {c.Visibility.AUTHORING,c.Visibility.PUBLIC} for context in request.contexts)
    else:
        assert request.stage.value=='control_authoring'


@pytest.mark.parametrize('kind', ['m4-source-delta','m4-submission','m4-control-record','control-authoring-journal'])
def test_control_publication_recovery_never_regenerates(tmp_path, monkeypatch, kind):
    store, inputs, sources, resolver, proposal, request, runner, service = control_fixture(tmp_path)
    original = store.put_bytes
    def fail(data, name, visibility):
        if name==kind: raise OSError('TEST control publication failure')
        return original(data,name,visibility)
    monkeypatch.setattr(store,'put_bytes',fail)
    with pytest.raises(ControlPublicationPending) as caught:
        service.generate((GenerationCandidate(request=request),), inputs, sources)
    monkeypatch.setattr(store,'put_bytes',original)
    first=caught.value.replay(store)
    assert first==caught.value.replay(store) and len(runner.calls)==1


def test_reference_excerpts_bind_actual_private_pair_and_cannot_enter_alternative(tmp_path):
    from feature_rl.environments import SourceArchive, SourceFile
    from feature_rl.generation import GenerationStage
    store, inputs, sources, resolver, proposal, request, runner, service = control_fixture(tmp_path)
    h = store.put_bytes(SourceArchive({'src/click/__init__.py': SourceFile(b'# SYNTHETIC CONTROL REFERENCE ONLY\nvalue = 2\n',False)}).to_tar(), 'source-archive', c.Visibility.PRIVATE)
    contract = store.get_artifact(inputs.contract)
    candidate = c.ArtifactRef(sha256='b'*64, kind='CandidateRecord',schema_version=2,visibility=c.Visibility.PRIVATE,encoding='json')
    pair = c.SourcePair(kind='SourcePair', schema_version=2,visibility=c.Visibility.PRIVATE,
        provenance=contract.provenance,costs=contract.costs,provenance_label='reconstructed_specification',candidate=candidate,
        baseline_commit='a'*40,reference_commit='b'*40,baseline=inputs.baseline,reference=h,
        relationship=c.CommitRelationship(integration='linear',target_before='a'*40,integrated_after='b'*40,implementation_commits=('b'*40,),parents=('a'*40,),evidence=contract.provenance.evidence),
        changed_files=(c.ChangedFile(path='src/click/__init__.py',category='implementation',rationale='SYNTHETIC TEST'),),admissible_cutoff=contract.provenance.created_at,verification=contract.provenance.evidence)
    pair_ref=store.put_artifact(pair)
    supplied=inputs.model_copy(update={'reference':h,'source_pair':pair_ref,
        'reference_excerpts':(ReferenceExcerpt(context_id='REF1',path='src/click/__init__.py',line_ranges=((1,1),)),ReferenceExcerpt(context_id='REF2',path='src/click/__init__.py',line_ranges=((2,2),))),
        'provenance':inputs.provenance.model_copy(update={'inputs':inputs.provenance.inputs+(h,pair_ref)})})
    req=build_control_request(request_id='REFCONTROL',response_id='REFRESP',prompt_id='REFPROMPT',store=store,resolver=resolver,inputs=supplied,sources=sources,limits=limits(),seed=0)
    references=[context for context in req.contexts if context.role=='reference']
    assert len(references)==2 and all(context.source==h for context in references)
    assert references[1].text=='value = 2\n'
    with pytest.raises(ValueError):
        type(req).model_validate(req.model_copy(update={'stage':GenerationStage.ALTERNATIVE_AUTHORING}))
    with pytest.raises(ValueError):
        ControlFinalizationInputs.model_validate(supplied.model_copy(update={'category':'alternative_positive','expected_valid':True,'requirement_ids':()}))
    wrong=supplied.model_copy(update={'baseline':h})
    with pytest.raises(ValueError):
        build_control_request(request_id='WRONG',response_id='RESP',prompt_id='PROMPT',store=store,resolver=resolver,inputs=wrong,sources=sources,limits=limits(),seed=0)


def test_generated_disallowed_source_is_rejected_with_cost(tmp_path):
    from feature_rl.requirements import AuthoringExhausted
    store, inputs, sources, resolver, proposal, request, runner, service = control_fixture(tmp_path)
    broken = proposal.model_copy(update={'files':(SourceEdit(path='setup.py',source='raise AssertionError()'),)})
    service.provider, runner = configured_diagnostic_provider(store,request,broken)
    with pytest.raises(AuthoringExhausted) as caught:
        service.generate((GenerationCandidate(request=request),),inputs,sources)
    journal=json.loads(store.get_bytes(caught.value.journal_refs[0]))
    assert journal['status']=='rejected' and journal['cost']['input_tokens']==41
    assert len(runner.calls)==1


def test_explicit_hostile_control_import_retains_outer_join(tmp_path):
    from feature_rl.submission.source import Submission
    store, inputs, sources, resolver, proposal, request, runner, service = control_fixture(tmp_path)
    delta=store.put_bytes(b'INTENTIONALLY MALFORMED ARCHIVE TEST','m4-source-delta',c.Visibility.PRIVATE)
    manifest=Submission(version='m4-submission-v1',baseline=inputs.baseline,changes=delta,deletions=())
    ref=store.put_bytes(canonical_json(manifest.model_dump(mode='json')),'m4-submission',c.Visibility.PRIVATE)
    finalizer=ControlFinalizer(store=store,resolver=resolver)
    prepared=finalizer.import_submission(ref,inputs,sources,rationale='TEST: malformed source; M5 must diagnose source rejection, not semantic omission')
    assert prepared.record.control.patch==ref and prepared.record.qualification=='unverified'
    assert not runner.calls


def test_attach_controls_freezes_new_verifier_without_regeneration(tmp_path):
    from feature_rl.verifiers import CheckerFinalizer
    from test_checker_authoring import request_for
    store, task, checker, base, sources, resolver = checker_fixture(tmp_path)
    finalizer=CheckerFinalizer(store=store,resolver=resolver)
    original=finalizer.prepare(checker,base,sources).publish(store)
    control_inputs=ControlFinalizationInputs(control_id='alt',category='alternative_positive',requirement_ids=(),expected_valid=True,
        expected_reason='TEST ONLY',baseline=base.baseline,contract=base.contract,environment=base.environment,provenance=base.provenance,costs=base.costs)
    record=ControlFinalizer(store=store,resolver=resolver).prepare(ControlProposal(files=(),deletions=(),rationale='TEST noop; not independent'),control_inputs,sources)
    record_ref=record.publish(store)
    updated=finalizer.attach_controls(original,(record_ref,),baseline=base.baseline,environment=base.environment)
    new_ref=updated.publish(store)
    old=store.get_artifact(original)
    assert new_ref!=original and not old.controls
    assert updated.verifier.controls==(record.record.control,)
    assert updated.verifier.cases==old.cases and updated.verifier.worker_adapter==old.worker_adapter
    assert original in updated.verifier.provenance.inputs and record_ref in updated.verifier.provenance.inputs
    with pytest.raises(ValueError,match='duplicate'):
        finalizer.attach_controls(new_ref,(record_ref,),baseline=base.baseline,environment=base.environment)
    wrong=record.record.model_copy(update={'contract':base.contract.model_copy(update={'sha256':'f'*64})})
    wrong_ref=store.put_bytes(canonical_json(wrong.model_dump(mode='json')),'m4-control-record',c.Visibility.PRIVATE)
    with pytest.raises(ValueError,match='join'):
        finalizer.attach_controls(original,(wrong_ref,),baseline=base.baseline,environment=base.environment)

    unknown=record.record.model_copy(update={'control':record.record.control.model_copy(update={'requirement_ids':('invented',)})})
    unknown_ref=store.put_bytes(canonical_json(unknown.model_dump(mode='json')),'m4-control-record',c.Visibility.PRIVATE)
    with pytest.raises(ValueError,match='unknown'):
        finalizer.attach_controls(original,(unknown_ref,),baseline=base.baseline,environment=base.environment)

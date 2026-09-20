"""Inert retained REAL Click accounting import. Never generation or historical execution.

Run with the project Python and PYTHONPATH=src. Original stores are read-only;
new copied CAS/Registry state belongs only to .feature-rl/research/M6.
"""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore,canonical_json
from feature_rl.environments import PreparedEnvironment
from feature_rl.generation import GenerationRequest,GenerationCallRecord,LocalGenerationProvider
from feature_rl.generation.backend import BackendConfig,MODEL_REVISION
from feature_rl.pipeline import Factory
from feature_rl.pipeline.authoring_models import AuthoringCall,AuthoringSettings,AuthoringBatch,AuthoringCaps,ResolverInputs
from feature_rl.pipeline.authoring import read_authoring_receipt,jobs
from feature_rl.pipeline.construction import references
from feature_rl.qualification.evidence import unknown_cost
from feature_rl.registry import Registry
from feature_rl.requirements import GroundedSource,GenerationCandidate,ContractFinalizationInputs,RetrievalPolicy
from feature_rl.requirements.discovery import ClickDiscoveryObservation

ROOT=Path(__file__).resolve().parents[4]
STATE=ROOT/'.feature-rl/research/M6/retained-click-authoring-v1'
REPORT=Path(__file__).with_name('click-import.json')
ORIGINALS=(ROOT/'.feature-rl/research/M2/authoring-production/store',ROOT/'.feature-rl/research/M1/production-store')
JOURNALS=('acc19437a873207cc3ce74dbe37ce7f479aa6a5f2ff4b11d4fcc095fc2bc1890',
    '261cd3a33c46f28ea7d55825add6e8509ef196ebb0f952ec29ec3891134b6a4e',
    'af7cee9c0af50eeddcf1a21f23b0e875964796648d20a34659e68a460806ace6')


def digest(raw):return hashlib.sha256(raw).hexdigest()
def model(cls,value):return cls.model_validate_json(canonical_json(value))
def write(path,value):path.write_bytes(canonical_json(value)+b'\n')


def main():
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    sources={str(path.relative_to(ROOT)):digest(path.read_bytes()) for directory in
        ('src/feature_rl/pipeline','src/feature_rl/registry','src/feature_rl/requirements','src/feature_rl/generation','src/feature_rl/scenarios','src/feature_rl/verifiers')
        for path in (ROOT/directory).glob('*.py')}
    stores=tuple(ArtifactStore(path,c.ActorRole.CONTROLLER) for path in ORIGINALS)
    store=ArtifactStore(STATE/'store',c.ActorRole.CONTROLLER)
    copied={};observed={}
    def copy(ref):
        if ref in copied:return
        source=next((s for s in stores if (s.root/(ref.sha256+'.json')).exists()),None)
        if source is None:raise ValueError('missing retained original '+ref.kind+':'+ref.sha256)
        path=source.root/(ref.sha256+'.json');observed[str(path.relative_to(ROOT))]=digest(path.read_bytes())
        if ref.encoding=='json':
            value=source.get_artifact(ref,max_envelope_bytes=32*1024*1024)
            data=value.model_dump(mode='json');selected=store.put_artifact(value)
        else:
            raw=source.get_bytes(ref,max_envelope_bytes=64*1024*1024,max_payload_bytes=32*1024*1024)
            selected=store.put_bytes(raw,ref.kind,ref.visibility)
            if ref.kind in ('contract-authoring-journal','generation-request','generation-status','click-runtime-discovery'):
                data=json.loads(raw)
            else:data=None  # schemas, responses, source archives and other opaque leaves
        if selected!=ref:raise ValueError('copy changed immutable original bytes')
        copied[ref]=True
        for child in references(data):copy(child)
    intake_path=ROOT/'docs/evidence/M1/coordinator-round2-intake-1.json'
    intake=json.loads(intake_path.read_bytes())
    pair_ref=model(c.ArtifactRef,intake['source_pair_ref']);candidate=model(c.ArtifactRef,intake['candidate_ref'])
    for ref in (candidate,pair_ref):copy(ref)
    pair=store.get_artifact(pair_ref);candidate_value=store.get_artifact(candidate)
    refs=tuple(c.ArtifactRef(sha256=sha,kind='contract-authoring-journal',schema_version=1,
        visibility=c.Visibility.AUTHORING,encoding='bytes') for sha in JOURNALS)
    for ref in refs:copy(ref)
    entries=[json.loads(store.get_bytes(ref)) for ref in refs]
    records=[model(GenerationCallRecord,entry['generation_record']) for entry in entries]
    requests=[GenerationRequest.model_validate_json(store.get_bytes(record.archives['request'])) for record in records]
    last=requests[-1]
    sources_for_call=tuple(model(GroundedSource,ctx.model_dump(mode='json')) for ctx in last.contexts)
    discovery_ref=next(ctx.source for ctx in last.contexts if ctx.source.kind=='click-runtime-discovery')
    discovery=ClickDiscoveryObservation.model_validate_json(store.get_bytes(discovery_ref))
    recipe=store.get_artifact(discovery.recipe)
    policy=next(ref for ref in recipe.provenance.inputs if ref.kind=='sandbox-policy')
    setup_path=ROOT/'docs/evidence/M2/coordinator-authoring-input.json'
    assert digest(setup_path.read_bytes())=='6172bbb28f9cd9275db21bc47ea55af922fe3d295758f7c443881736d56aebc0'
    request_ref=next(ctx.source for ctx in last.contexts if ctx.role=='request')
    assert request_ref.sha256=='d8d8a97861a597c7bfe181dfd3bbd6dbf72b7854ee13d555add1b1e07f0d2c61'
    assert discovery.baseline==pair.baseline and pair.candidate==candidate
    # These inert scope fields are reconstructed from retained request/discovery
    # and the pinned driver. They are NOT an original saved finalizer-input record.
    provenance=candidate_value.provenance.model_copy(update={'producer':'feature_rl.pipeline.retained_import_metadata',
        'producer_version':revision,'inputs':(pair.baseline,request_ref,discovery_ref,discovery.recipe)})
    inputs=ContractFinalizationInputs(visible_request=store.get_bytes(request_ref).decode(),
        allowed_requirement_ids=last.allowed_requirement_ids,entry_points=discovery.entry_points,
        supported_observables=discovery.supported_observables,runtime_discovery=discovery_ref,
        allowed_changes=c.AllowedChanges(source_roots=('src/click',),forbidden_paths=('tests',),
            dependencies='forbidden',dependency_artifacts=(),additional_artifact_types=()),public_checks=(),
        episode_limits=recipe.limits,provenance_label='reconstructed_specification',visibility=c.Visibility.AUTHORING,
        provenance=provenance,costs=(unknown_cost('construction','Reconstructed inert import metadata is not an original finalizer cost allocation'),))
    call=AuthoringCall(source_pair=pair_ref,environment=PreparedEnvironment(recipe=discovery.recipe,policy=policy),
        resolver=ResolverInputs(request=request_ref,baseline=pair.baseline,runtime_discovery=discovery_ref,
            public_checks=(),retrieval_policy=RetrievalPolicy(allowed_paths=('src/click/core.py','src/click/exceptions.py',
                'tests/test_options.py','tests/test_commands.py','docs/commands-and-groups.md'),max_archive_bytes=16*1024*1024,
                max_files=2000,max_selected_bytes=48*1024,max_spans=8,max_expanded_bytes=16*1024*1024)),
        generation=GenerationCandidate(request=last,diagnosis=entries[-1]['diagnosis'],changed_input=entries[-1]['changed_input']),
        inputs=inputs,sources=sources_for_call)
    caps=AuthoringCaps(input_tokens=sum(r.limits.input_tokens for r in requests),output_tokens=sum(r.limits.output_tokens for r in requests),
        wall_seconds=sum(r.limits.wall_seconds for r in requests)+1.0,cpu_seconds=sum(r.limits.cpu_seconds+1 for r in requests),
        commands=3,memory_bytes=last.limits.declared_memory_ceiling_bytes,spend_usd=None)
    settings=AuthoringSettings(backend=BackendConfig(python_executable=ROOT/'.venv/bin/python',
        model_directory=ROOT/'.feature-rl/research/M2/model/mlx-community--Qwen3-4B-Instruct-2507-4bit'/MODEL_REVISION,
        model_manifest=ROOT/'.feature-rl/research/M2/model-acquisition.json',dependency_manifest=ROOT/'.feature-rl/research/M2/dependency-lock.json'),
        m2_revision=revision,m4_revision=revision,evidence_scope='real_integration',
        batch=AuthoringBatch(candidates=(candidate,),candidate_caps=caps,batch_caps=caps,
            calibration_evidence=(records[0].archives['cost'],discovery_ref)))
    write(STATE/'call.json',call.model_dump(mode='json'));write(STATE/'settings.json',settings.model_dump(mode='json'))
    registry=Registry(STATE/'registry',store)
    factory=Factory(store=store,registry=registry,revision=revision,authoring=settings)
    with patch.object(LocalGenerationProvider,'generate',side_effect=AssertionError('NO NEW INFERENCE AUTHORIZED')):
        result=factory.import_rejected_authoring(candidate,call=call,journal_refs=refs)
        repeated=factory.import_rejected_authoring(candidate,call=call,journal_refs=refs)
        source=factory.screen_source(candidate)
        blocked=factory.construct(candidate)
    assert result==repeated and result.disposition==c.Disposition.REJECTED
    assert source.disposition==c.Disposition.PROVISIONAL and blocked==source
    imported=jobs(factory,candidate)
    assert len(imported)==3 and sum(request.repair for _,request in imported)==2 and len(recipe.neutral_repairs)==1
    for job,request in imported:
        receipt=read_authoring_receipt(store,job.result.artifacts[-1])
        assert request.origin=='retained_journal' and not receipt.outputs
    assert all(digest((ROOT/name).read_bytes())==sha for name,sha in observed.items())
    assert all(digest((ROOT/name).read_bytes())==sha for name,sha in sources.items())
    write(REPORT,{'status':'passed','scope':'inert import of original REAL Click failed generation; not a fixture or new generation',
        'observed_git_revision':revision,'tested_source_sha256':sources,'original_envelope_sha256':observed,
        'intake_receipt_sha256':digest(intake_path.read_bytes()),'setup_receipt_sha256':digest(setup_path.read_bytes()),
        'original_source_bytes_unchanged':True,'production_sources_unchanged':True,'copied_artifacts':len(copied),
        'new_provider_calls':0,'new_native_or_docker_calls':0,'candidate':candidate.model_dump(mode='json'),
        'source_pair':pair_ref.model_dump(mode='json'),'source_disposition':source.model_dump(mode='json'),
        'selected_contract_rejection':result.model_dump(mode='json'),'import_job_ids':[job.job_id for job,_ in imported],
        'original_journals':[ref.model_dump(mode='json') for ref in refs],'original_provider_costs':[entry['cost'] for entry in entries],
        'known_repairs':{'environment':1,'authoring':2,'candidate':3,'candidate_cap':4,'stage_cap':2},
        'history_complete':False,'unresolved_gates':['mixed-purpose source scope review','malformed exhausted contract',
            'no saved original finalizer-input metadata','external repair frontier unknown','USD and remaining overhead unknown'],
        'new_state':str(STATE.relative_to(ROOT)),'recorded_at':datetime.now(timezone.utc).isoformat()})
    print(json.dumps({'status':'passed','imported_calls':3,'new_calls':0,'source':'provisional','contract':'rejected','known_repairs':3}))


if __name__=='__main__':main()

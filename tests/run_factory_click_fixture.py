"""TEST ONLY: actual M3/M4/M5/M6 Click integration, never feature generation.

Run from the repository root with PYTHONPATH=src. Both phases retain one receipt.
The source archive and wheels are copied inertly; candidate code executes only
inside the actual M3 Docker boundary. No human approval is created here.
"""
import os
from m4_fixtures import runtime_policy
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
import traceback
import uuid

from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.environments import (DockerEngine, EnvironmentRuntime, ExecutionRequest,
    PreparedEnvironment, SandboxPolicy, SourceArchive, SourceFile)
from feature_rl.grading import GradingService, read_grade
from feature_rl.pipeline import BuildInputs, TaskBuilder
from feature_rl.registry import Registry
from feature_rl.submission import SubmissionService


LABEL = 'TEST ONLY synthetic Click prefix fixture; not model generation or historical feature'
MODULE = 'src/click/m5_test_feature.py'
CORRECT = b'"""TEST ONLY disclosed prefix helper."""\ndef transform(text):\n    return "fixture:" + text\n'
BROKEN = b'"""TEST ONLY omission control."""\ndef transform(text):\n    return text\n'
ALTERNATIVE = b'"""TEST ONLY same-author extra positive; not independent."""\ndef transform(text):\n    return "".join(("fixture:", text))\n'
REQUEST = (LABEL + '\nF1: Add click.m5_test_feature.transform(text), returning exactly "fixture:" + text.\n'
    'C1: Preserve ordinary click.echo(text): exact text followed by newline and CLI exit zero.\n'
    'Disclosed diagnostic cases: "alpha", "", and " spaced ".\n').encode()
ADAPTER = b'''import importlib,json,sys
import click
from click.testing import CliRunner
request=json.load(sys.stdin)
text=request["inputs"]["text"]
try:
    helper=importlib.import_module("click.m5_test_feature")
except ModuleNotFoundError as exc:
    if exc.name!="click.m5_test_feature":
        raise
    present=False
    transformed=text
else:
    present=True
    transformed=helper.transform(text)
@click.command()
@click.argument("text")
def command(text):
    click.echo(text)
echo=CliRunner().invoke(command,[text])
print(json.dumps({"case_id":request["case_id"],"observations":{
    "present":present,"result":transformed,"echo":echo.output,"exit":echo.exit_code}}))
'''


def now():
    return datetime.now(timezone.utc).isoformat()


def doc(value):
    return value.model_dump(mode='json')


def ref(value):
    return c.ArtifactRef.model_validate_json(json.dumps(value))


def source_hashes():
    paths = [Path(__file__), Path('docs/evidence/M2/coordinator-authoring-input.json')]
    for folder in ('artifacts', 'contracts', 'environments', 'history', 'pipeline',
                   'registry', 'submission', 'verifiers', 'grading', 'qualification'):
        paths.extend(sorted((Path('src/feature_rl') / folder).glob('*.py')))
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def save(path, receipt):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2) + '\n')


def fixture(store, baseline, prepared, runtime, revision):
    """Create real resolvable M0 records for the explicitly synthetic feature."""
    source = runtime.source(baseline)
    assert MODULE not in source.files
    stamp = datetime.now(timezone.utc)
    request = store.put_bytes(REQUEST, 'm6-test-request', c.Visibility.PUBLIC)
    declaration = store.put_bytes(canonical_json({
        'label': LABEL, 'historical_feature': False, 'model_calls': 0,
        'human_approval': False, 'alternative_independent': False,
        'source_identity_note': 'B is the pinned actual Click archive. H is B plus the disclosed helper. '
            'Commit-shaped identifiers below are diagnostic snapshot hashes, not claimed Git commits.',
        'module': MODULE, 'baseline': doc(baseline),
    }), 'm6-test-declaration', c.Visibility.PRIVATE)
    evidence = c.EvidenceRecord(producer=LABEL, command=('inert-test-fixture-authoring',),
        recorded_at=stamp, exit_status=0, artifacts=(request, declaration),
        revision=revision, scope='unit_diagnostic')
    cost = c.CostRecord(category='authoring', wall_seconds=None, cpu_seconds=None,
        gpu_seconds=None, input_tokens=None, output_tokens=None, human_minutes=None,
        usd=None, measurement='unknown', note=LABEL + '; fixture authoring effort unmeasured')

    def provenance(inputs, producer=LABEL):
        return c.Provenance(producer=producer, producer_version=revision, created_at=stamp,
            inputs=inputs, evidence=(evidence,))

    common = dict(schema_version=1, visibility=c.Visibility.PRIVATE,
        provenance=provenance((request, declaration)), costs=(cost,))
    h_archive = SourceArchive(source.files | {MODULE: SourceFile(CORRECT, False)}).to_tar()
    h = store.put_bytes(h_archive, 'source-archive', c.Visibility.PRIVATE)
    b_hash = hashlib.sha256(runtime.read_bytes(baseline, runtime.policy.max_archive_bytes)).hexdigest()
    h_hash = hashlib.sha256(h_archive).hexdigest()
    relationship = c.CommitRelationship(integration='linear', target_before=b_hash,
        integrated_after=h_hash, implementation_commits=(h_hash,), parents=(b_hash,), evidence=(evidence,))
    license_bytes = source.files['LICENSE.txt'].data
    assert b'Redistribution and use' in license_bytes
    license_ref = store.put_bytes(license_bytes, 'license-text', c.Visibility.AUTHORING)
    candidate = c.CandidateRecord(kind='CandidateRecord', **(common | {'schema_version': 2}),
        provenance_label='reconstructed_specification', repository_url='diagnostic://m6/click-prefix-test-only',
        repository_family='diagnostic-only-click', request_lineage=('test-only-prefix-fixture',),
        partition=c.Partition.DEVELOPMENT,
        sources=(c.SourceSnapshot(url='diagnostic://m6/click-prefix-test-only', content=request,
            retrieved_at=stamp, published_at=None, edited_at=None, edit_history='not_applicable',
            media_type='text/plain', redirect_chain=None),),
        license=c.LicenseRecord(spdx_id='BSD-3-Clause', license_text=license_ref, status='verified', evidence=(evidence,)),
        commits=relationship, screening=c.ScreeningDecision(disposition=c.Disposition.SUCCESS,
            reason=LABEL + '; diagnostic source and license inventory only, no historical admission', evidence=(evidence,)))
    candidate_ref = store.put_artifact(candidate)
    pair = c.SourcePair(kind='SourcePair', **(common | {'schema_version': 2}),
        provenance_label='reconstructed_specification', candidate=candidate_ref,
        baseline_commit=b_hash, reference_commit=h_hash, baseline=baseline, reference=h,
        relationship=relationship, changed_files=(c.ChangedFile(path=MODULE, category='implementation',
            rationale=LABEL + '; sole changed path implements the disclosed prefix helper'),),
        admissible_cutoff=stamp, verification=(evidence,))
    pair_ref = store.put_artifact(pair)
    origin = c.EvidenceLink(source=request, locator='complete disclosed TEST request',
        quote=REQUEST.decode(), provenance_label='reconstructed_specification')
    rules = c.AllowedChanges(source_roots=('src',), forbidden_paths=(), dependencies='forbidden',
        dependency_artifacts=(), additional_artifact_types=())
    contract = c.RequirementContract(kind='RequirementContract', **common,
        visible_request=REQUEST.decode(), capability='TEST ONLY exact prefix transformation',
        entry_points=('click.m5_test_feature.transform', 'click.echo'),
        requirements=(c.Requirement(requirement_id='F1', statement='Return exactly "fixture:" + text.',
            mandatory=True, evidence=(origin,), observable='Helper presence and returned text'),),
        compatibility_obligations=(c.Requirement(requirement_id='C1', statement='Preserve ordinary echo text, newline and exit zero.',
            mandatory=True, evidence=(origin,), observable='CliRunner output and exit code'),),
        ambiguities=(), allowed_changes=rules, public_checks=(),
        episode_limits=runtime.recipe(prepared).limits, provenance_label='reconstructed_specification')
    contract_ref = store.put_artifact(contract)
    values = ('alpha', '', ' spaced ')
    scenarios = tuple(c.Scenario(scenario_id='s'+str(i), requirement_ids=('F1','C1'),
        preconditions=('Actual Click B installed from candidate source',),
        actions=('Call helper if present; invoke ordinary echo command',),
        observations=('helper presence, transformed text, echo output, exit code',),
        expected_relation='present true; fixture: prefix; original echo plus newline; exit zero',
        input_domain='disclosed TEST constant '+repr(value), oracle_origin=origin, reset_needs=())
        for i, value in enumerate(values))
    plan = c.ScenarioPlan(kind='ScenarioPlan', **common, contract=contract_ref,
        mandatory_requirement_ids=('F1','C1'), scenarios=scenarios,
        seed_policy=c.SeedPolicy(algorithm='m4-sha256-v1', seeds=(11,), same_cases_within_group=True))
    plan_ref = store.put_artifact(plan)
    cases = []
    for i, value in enumerate(values):
        inp = {'version':'m4-input-v1','scenario_id':'s'+str(i),'requirement_ids':['F1','C1'],
            'fields':[{'name':'text','domain':{'kind':'constant','value':value}}]}
        assertions = []
        for name, requirement, expected in (
            ('present','F1',{'kind':'literal','value':True}),
            ('result','F1',{'kind':'input','name':'text','prefix':'fixture:'}),
            ('echo','C1',{'kind':'input','name':'text','suffix':'\n'}),
            ('exit','C1',{'kind':'literal','value':0})):
            assertions.append({'assertion_id':name,'requirement_ids':[requirement],
                'oracle_origin':doc(origin),'actual':name,'operator':'equal','expected':expected})
        comparison = {'version':'m4-comparison-v1','scenario_id':'s'+str(i),
            'requirement_ids':['F1','C1'],'mode':'json','timeout_seconds':2.0,
            'observations':[{'name':name,'type':kind} for name,kind in
                (('present','boolean'),('result','string'),('echo','string'),('exit','integer'))],
            'assertions':assertions}
        cases.append(c.CaseDefinition(case_id='c'+str(i), requirement_ids=('F1','C1'), mandatory=True,
            inputs=store.put_bytes(canonical_json(inp),'m4-case-input',c.Visibility.PRIVATE),
            comparison=store.put_bytes(canonical_json(comparison),'m4-case-comparison',c.Visibility.PRIVATE)))
    submissions = SubmissionService(store=store, policy=runtime.policy)
    controls = []
    for name, code, category, valid, requirements in (
        ('omit_prefix', BROKEN, 'omission', False, ('F1',)),
        ('alternative', ALTERNATIVE, 'alternative_positive', True, ('F1','C1'))):
        patch = submissions.create(baseline, SourceArchive({MODULE:SourceFile(code,False)}).to_tar(), (), rules)
        controls.append(c.ControlPatch(control_id=name, category=category, patch=patch,
            requirement_ids=requirements, expected_valid=valid,
            expected_reason='TEST ONLY exact disclosed semantics; alternative uses the same fixture author',
            author_provenance=provenance((baseline,contract_ref),
                LABEL + '; same fixture author for H and controls, no independence claim')))
    adapter = store.put_bytes(ADAPTER, 'm4-worker-adapter', c.Visibility.PRIVATE)
    verifier = c.VerifierBundle(kind='VerifierBundle', **common, contract=contract_ref,
        scenario_plan=plan_ref, cases=tuple(cases), completion_manifest=tuple(case.case_id for case in cases),
        worker_adapter=c.WorkerAdapter(code=adapter, version='m4-worker-v1',
            supported_observables=('json',), limitations=(LABEL, 'Only disclosed three constants tested')),
        public_examples=(), controls=tuple(controls), permissions=c.VerifierPermissions(
            controller_role=c.ActorRole.CONTROLLER, worker_inputs=(adapter,),
            output_limit_bytes=65536, submission_policy=rules))
    verifier_ref = store.put_artifact(verifier)
    inputs = BuildInputs(source_pair=pair_ref, contract=contract_ref, scenario_plan=plan_ref,
        verifier=verifier_ref, environment=prepared, baseline_files=tuple(sorted(source.files)),
        invocation='test-only-click-prefix')
    return inputs, {'candidate':doc(candidate_ref),'source_pair':doc(pair_ref),'reference':doc(h),
        'contract':doc(contract_ref),'scenario_plan':doc(plan_ref),'verifier':doc(verifier_ref),
        'evidence':doc(evidence),'controls':{item.control_id:doc(item.patch) for item in controls}}


def make_runtime(state, store, setup, revision):
    print('Actual M3 boundary qualification using the cached pinned image', flush=True)
    engine = DockerEngine(image_repository=os.environ.get('FEATURE_RL_TEST_IMAGE_REPOSITORY'),state_root=state/'runtime', socket_path=Path(setup['socket_path']), policy=runtime_policy())
    engine.qualify_boundary()
    return EnvironmentRuntime(store=store, engine=engine, revision=revision)


def grade(store, runtime, grader, task, submission, label, expected, receipt, path):
    print('Actual M4 grade: '+label, flush=True)
    entry = {'label':label, 'started_at':now(), 'submission':doc(submission), 'status':'running'}
    receipt['grades'].append(entry); save(path, receipt)
    result = grader.grade(task, submission, 11)
    measured = read_grade(store, result.artifacts[0])
    entry.update(result=doc(result), receipt=doc(measured), ended_at=now(), status='returned')
    save(path, receipt)  # Retain the actual result before evaluating test assertions.
    assert measured.reward == expected and measured.cleanup_verified
    assert len(measured.cases) == 3 and all(case.status == 'completed' for case in measured.cases)
    failed = {r for case in measured.cases for a in case.assertions if not a.passed for r in a.requirement_ids}
    assert failed == (set() if expected else {'F1'})
    assert runtime.recover_owned() == []
    entry['status'] = 'asserted'; save(path, receipt)
    print(label+': reward='+str(measured.reward)+', all three cases completed; failed='+repr(sorted(failed)), flush=True)


def runtime_phase(path, receipt, setup):
    from feature_rl.qualification import derive_reference
    state = Path(receipt['state']); state.mkdir(parents=True, exist_ok=False)
    store = ArtifactStore(state/'store', c.ActorRole.CONTROLLER)
    baseline = ref(setup['authoring_view']['baseline'])
    author_store = ArtifactStore(Path(setup['source_store']), c.ActorRole.AUTHOR)
    data = author_store.get_bytes(baseline, max_envelope_bytes=24*1024*1024, max_payload_bytes=16*1024*1024)
    assert store.put_bytes(data, 'source-archive', c.Visibility.AUTHORING) == baseline
    pins = []
    for wheel in setup['dependency_wheels']:
        data = Path(wheel['local_path']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == wheel['sha256']
        artifact = store.put_bytes(data,'dependency-wheel',c.Visibility.AUTHORING)
        pins.append(c.DependencyPin(name=wheel['name'],version=wheel['version'],artifact=artifact,sha256=wheel['sha256']))
    revision = receipt['phases'][-1]['observed_head']
    runtime = make_runtime(state, store, setup, revision)
    source_evidence = c.EvidenceRecord(producer=LABEL + '; actual B/wheel hash inspection',
        command=('tests/run_factory_click_fixture.py','runtime'), recorded_at=datetime.now(timezone.utc),
        exit_status=0, artifacts=(baseline,*(pin.artifact for pin in pins)), revision=revision, scope='source_inspection')
    prepared = runtime.create_recipe(baseline, tuple(pins), source_evidence=source_evidence)
    inputs, context = fixture(store, baseline, prepared, runtime, revision)
    registry = Registry(state/'registry', store)
    builder = TaskBuilder(store=store, registry=registry, revision=revision)
    build = builder.build(inputs, owner='test-only-click-fixture', claim_key='initial-build')
    context.update(environment=doc(prepared), baseline=doc(baseline), builder_revision=revision,
        runtime_revision=revision, grading_revision=revision, build=doc(build),
        build_inputs=doc(inputs), boundary_qualification=doc(runtime.qualification_ref))
    receipt['context'] = context; save(path, receipt)
    assert build.disposition == c.Disposition.SUCCESS
    task, = build.artifacts; context['task'] = doc(task)
    package = builder.solver_package(task)
    context['package_raw_sha256'] = hashlib.sha256(package).hexdigest()
    assert ('workspace/'+MODULE) not in SourceArchive.read(package, runtime.policy).files
    projection = derive_reference(store, task, runtime.policy)
    context['projection'] = doc(projection); save(path, receipt)
    assert projection.reference == ref(context['reference'])
    assert [p.path for p in projection.paths] == [MODULE]
    rules = store.get_artifact(inputs.contract).allowed_changes
    grader = GradingService(store=store, runtime=runtime, revision=revision)
    noop = grader.submissions.create(baseline, SourceArchive({}).to_tar(), (), rules)
    grade(store,runtime,grader,task,noop,'baseline_missing_feature',0,receipt,path)
    grade(store,runtime,grader,task,projection.submission,'correct_reference_projection',1,receipt,path)
    grade(store,runtime,grader,task,ref(context['controls']['omit_prefix']),'omit_prefix',0,receipt,path)
    handle = runtime.open_workspace(prepared, role='baseline', allowed_changes=rules)
    try:
        initial_state, _, _, initial, _ = runtime.workspace(handle)
        mutation = runtime.execute_development(handle, ExecutionRequest(command=c.CommandSpec(
            argv=('python','-I','-c','import pathlib,sys;pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())',
                '/workspace/source/'+MODULE), working_directory='/workspace',timeout_seconds=2.0),
            stdin=CORRECT,save_source=True))
        receipt['reset'] = {'initial':doc(initial),'mutation':doc(mutation)}; save(path,receipt)
        assert mutation.reason == 'completed' and mutation.cleanup_verified
        assert mutation.saved_source.artifact == projection.projected_source
        restored = runtime.reset(handle)
        restored_state = runtime.workspace(handle)[0]
        receipt['reset'].update(restored=doc(restored),generation_before=initial_state['generation'],
            generation_after=restored_state['generation']); save(path,receipt)
        assert restored == initial and restored_state['generation'] > initial_state['generation']
        reset_submission = grader.submissions.from_saved(baseline, restored.artifact, rules)
        assert reset_submission == noop
    finally:
        runtime.close(handle)
    grade(store,runtime,grader,task,reset_submission,'post_reset_baseline',0,receipt,path)
    receipt['runtime_milestone'] = 'PASS: B fails F1, correct H passes, omission fails F1, reset restores exact B; C1 always passes'
    assert runtime.recover_owned() == []


def qualification_phase(path, receipt, setup):
    from feature_rl.qualification import QualificationService, QualificationPolicy, ControlDiagnosis
    assert receipt.get('runtime_milestone','').startswith('PASS:')
    state = Path(receipt['state']); context = receipt['context']
    store = ArtifactStore(state/'store', c.ActorRole.CONTROLLER)
    registry = Registry(state/'registry', store)
    runtime = make_runtime(state, store, setup, context['runtime_revision'])
    grader = GradingService(store=store, runtime=runtime, revision=context['grading_revision'])
    builder = TaskBuilder(store=store, registry=registry, revision=context['builder_revision'])
    evidence = c.EvidenceRecord.model_validate_json(json.dumps(context['evidence']))
    diagnoses = (
        ControlDiagnosis(control_id='omit_prefix', validity='invalid', mode='semantic_negative',
            targets=('F1',), attack=None, evidence=(evidence,), independence_evidence=(),
            note=LABEL + '; omits exactly the required prefix'),
        ControlDiagnosis(control_id='alternative', validity='valid', mode='positive', targets=(),
            attack=None, evidence=(evidence,), independence_evidence=(),
            note=LABEL + '; same-author extra positive only; independence is not established'))
    policy = QualificationPolicy(baseline_missing_requirements=('F1',), controls=diagnoses,
        max_grade_calls=9, max_wall_seconds=2400.0)
    service = QualificationService(store=store, registry=registry, grader=grader, builder=builder,
        revision=receipt['phases'][-1]['observed_head'], policy=policy)
    receipt['qualification'] = {'status':'running','policy':doc(policy),'started_at':now(),
        'boundary_qualification':doc(runtime.qualification_ref)}; save(path,receipt)
    print('Actual M5 qualify: B + 3 fresh H + 2 controls + 3 interrupted/reset H grades', flush=True)
    result = service.qualify(ref(context['task']))
    report_ref = next(r for r in result.artifacts if r.kind == 'QualificationReport')
    report = store.get_artifact(report_ref)
    summary_ref = next(r for r in result.artifacts if r.kind == 'm5-qualification-summary')
    summary = json.loads(store.get_bytes(summary_ref,max_envelope_bytes=2*1024*1024,max_payload_bytes=1024*1024))
    receipt['qualification'].update(status='returned',result=doc(result),report=doc(report),summary=summary,ended_at=now())
    save(path,receipt)
    assert result.disposition == c.Disposition.PROVISIONAL and report.disposition == c.Disposition.PROVISIONAL
    assert not any(r.kind == 'm5-review-request' for r in result.artifacts)
    assert len(report.fresh_runs) == 3 and len(report.interrupted_reset_runs) == 3 and len(report.controls) == 2
    gates = (report.baseline_health,report.baseline_absence,report.reference_run,*report.fresh_runs,*report.interrupted_reset_runs,*report.controls)
    assert all(gate is not None and gate.passed for gate in gates)
    assert any('human' in reason for reason in report.rejection_reasons)
    assert any('independence' in reason for reason in report.rejection_reasons)
    assert any('repair history' in reason for reason in report.rejection_reasons)
    assert runtime.recover_owned() == []
    before = registry.events()
    assert service.qualify(ref(context['task'])) == result and registry.events() == before
    receipt['qualification']['status'] = 'asserted_provisional'
    print('M5 returned provisional after all nine grades; actual gates pass, missing admissions remain enforced',flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('runtime','qualification'))
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    if args.phase == 'runtime':
        if args.receipt.exists():
            raise SystemExit('Refusing to overwrite an existing runtime receipt or redispatch unknown work')
        receipt = {'label':LABEL,'generated_feature':False,'human_approval':False,
            'alternative_independent':False,'state':str((Path('.feature-rl/research/M6')/('fixture-runtime-'+uuid.uuid4().hex)).resolve()),
            'phases':[],'grades':[]}
    else:
        receipt = json.loads(args.receipt.read_text())
        if 'qualification' in receipt:
            raise SystemExit('Existing qualification attempt requires explicit M5 recovery; this driver will not redispatch it')
    setup = json.loads(Path('docs/evidence/M2/coordinator-authoring-input.json').read_text())
    phase = {'name':args.phase,'started_at':now(),'source_hashes_before':source_hashes(),
        'observed_head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()}
    receipt['phases'].append(phase); save(args.receipt,receipt)
    start = time.monotonic()
    try:
        (runtime_phase if args.phase == 'runtime' else qualification_phase)(args.receipt,receipt,setup)
        phase['status'] = 'passed'
    except BaseException as exc:
        phase.update(status='failed',error=type(exc).__name__+': '+str(exc),traceback=traceback.format_exc())
        raise
    finally:
        phase.update(ended_at=now(),wall_seconds=time.monotonic()-start,source_hashes_after=source_hashes())
        save(args.receipt,receipt)


if __name__ == '__main__':
    main()

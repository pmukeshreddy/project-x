"""Behavioral checks for direct tests and byte-exact output comparison."""
import subprocess
import sys
from types import SimpleNamespace

import pytest
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.environments import SourceArchive, SourceFile
from feature_rl.verifiers.inputs import repository_tests, matches, observe, NoBehavioralInputs
from feature_rl.verifiers.loader import read_local
from feature_rl.verifiers.models import BehavioralInput, ProcessObservation


def tree(files):
    return SourceArchive({name: SourceFile(text.encode(), False) for name, text in files.items()})


def test_original_pytest_files_run_with_fixtures_and_parameters_without_rewriting(tmp_path):
    store=ArtifactStore(tmp_path/'store',c.ActorRole.CONTROLLER)
    original='''import pytest
import example
@pytest.fixture
def number():
    return 3
@pytest.mark.parametrize("factor, expected", [(2, 6), (4, 12)])
def test_multiply(number, factor, expected):
    assert example.multiply(number, factor) == expected
'''
    baseline=tree({'src/example.py':'def multiply(a,b): return a+b\n'})
    reference=tree({'src/example.py':'def multiply(a,b): return a*b\n','tests/test_example.py':original})
    origin=store.put_bytes(reference.to_tar(),'source-archive',c.Visibility.PRIVATE)
    selected=repository_tests(store,baseline,reference,origin,10.0)
    inp=read_local(store,selected[0],BehavioralInput,'behavioral-input')
    from m4_fixtures import runtime_policy
    frozen=SourceArchive.read(store.get_bytes(inp.stdin),runtime_policy())
    assert frozen.files['tests/test_example.py'].data==original.encode()
    assert 'src/example.py' not in frozen.files
    work=tmp_path/'worker';(work/'tests').mkdir(parents=True)
    (work/'tests/test_example.py').write_bytes(frozen.files['tests/test_example.py'].data)
    results=[]
    for implementation in (baseline,reference,baseline):
        (work/'example.py').write_bytes(implementation.files['src/example.py'].data)
        import os
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
        result=subprocess.run([sys.executable,*inp.command.argv[1:]],cwd=work,env=env,capture_output=True,timeout=10)
        results.append(result.returncode)
    assert results==[1,0,1]


def test_output_comparison_includes_exit_stdout_stderr_and_exact_bytes():
    inp=SimpleNamespace(mode='output')
    expected=ProcessObservation(exit_code=7,stdout_hex=b'\xffhello\n'.hex(),stderr_hex=b'note'.hex())
    assert matches(inp,expected,expected)
    for update in ({'exit_code':0},{'stdout_hex':b'\xffhello'.hex()},{'stderr_hex':''}):
        assert not matches(inp,expected.model_copy(update=update),expected)


def test_direct_test_verdict_uses_test_exit_status_not_log_spelling():
    inp=SimpleNamespace(mode='tests')
    expected=ProcessObservation(exit_code=0,stdout_hex=b'2 passed in 0.1s'.hex(),stderr_hex='')
    assert matches(inp,expected.model_copy(update={'stdout_hex':b'2 passed in 0.2s'.hex()}),expected)
    for code in (1,2,3,4,5):
        assert not matches(inp,expected.model_copy(update={'exit_code':code}),expected)


def test_timeouts_and_unverified_cleanup_are_not_valid_observations():
    output=SimpleNamespace(cleanup_verified=True,failure_category='none',reason='completed',exit_code=0,stdout=b'ok',stderr=b'')
    assert observe(output).stdout_hex=='6f6b'
    output.reason='timeout'
    with pytest.raises(ValueError):observe(output)
    output.reason='completed';output.cleanup_verified=False
    with pytest.raises(ValueError):observe(output)


def test_no_tests_and_no_explicit_input_rejects_before_execution():
    from feature_rl.verifiers.reference import freeze_reference
    pair=SimpleNamespace(baseline='B',reference='H')
    recipe=SimpleNamespace(baseline='B',limits=SimpleNamespace(wall_seconds=60.0))
    contract=SimpleNamespace(episode_limits=recipe.limits)
    store=SimpleNamespace(get_artifact=lambda ref:pair if ref=='pair' else contract)
    runtime=SimpleNamespace(recipe=lambda prepared:recipe,profile=SimpleNamespace(import_modules=('example',)),source=lambda ref:tree({}))
    with pytest.raises(NoBehavioralInputs,match='No directly runnable'):
        freeze_reference(store=store,runtime=runtime,source_pair='pair',contract_ref='contract',prepared=None,revision='a'*40)


def test_explicit_reference_capture_then_candidate_grading_never_reads_h(tmp_path, monkeypatch):
    """Native subprocess diagnostic of the controller path, not a Docker admission."""
    import os
    from m4_fixtures import runtime_policy, replace_artifact
    from m5_fixtures import task_fixture
    from feature_rl.environments import EnvironmentRuntime, PreparedEnvironment
    from feature_rl.grading import GradingService, read_grade
    from feature_rl.verifiers.reference import freeze_reference
    store=ArtifactStore(tmp_path/'store',c.ActorRole.CONTROLLER)
    task_ref=task_fixture(store,changes={'src/click/__init__.py':SourceFile(b'def echo(value): print(value)\n',False)})
    task=store.get_artifact(task_ref);pair=store.get_artifact(task.source_pair)
    recipe=store.get_artifact(task.environment)
    prepared=PreparedEnvironment(recipe=task.environment,policy=next(ref for ref in recipe.provenance.inputs if ref.kind=='sandbox-policy'))
    old=store.get_artifact(task.private_oracle)
    runtime=object.__new__(EnvironmentRuntime);runtime.store=store;runtime.policy=runtime_policy();runtime.base_policy=runtime.policy;runtime.profile=runtime.policy.profile
    # Build/container plumbing is a labeled unit double; commands actually run.
    runtime.recipe=lambda _:recipe
    evidence=store.put_bytes(b'UNIT diagnostic runtime boundary','unit-execution',c.Visibility.PRIVATE)
    runtime.build_snapshot=lambda handle:SimpleNamespace(evidence=evidence,cost=task.costs[0])
    opened=[]
    def open_workspace(prepared,source=None,role='baseline',**kwargs):
        opened.append((source,role));return source
    runtime.open_workspace=open_workspace;runtime.close=lambda handle:None
    def execute(handle,request,build):
        folder=tmp_path/('process-'+str(len(list(tmp_path.glob('process-*')))))
        (folder/'click').mkdir(parents=True)
        (folder/'click/__init__.py').write_bytes(runtime.source(handle).files['src/click/__init__.py'].data)
        out=subprocess.run([sys.executable,*request.command.argv[1:]],input=request.stdin,
            cwd=folder,env=dict(os.environ,PYTHONPATH=str(folder),PYTHONDONTWRITEBYTECODE='1'),capture_output=True,timeout=5)
        return SimpleNamespace(exit_code=out.returncode,stdout=out.stdout,stderr=out.stderr,cleanup_verified=True,
            failure_category='none' if out.returncode==0 else 'candidate',reason='completed' if out.returncode==0 else 'command_failed',evidence=evidence,cost=task.costs[0])
    runtime.execute=execute
    result=freeze_reference(store=store,runtime=runtime,source_pair=task.source_pair,contract_ref=task.contract,
        prepared=prepared,revision='a'*40,behavioral_inputs=(old.cases[0].inputs,))
    verifier=store.get_artifact(result.artifacts[0])
    expected=read_local(store,verifier.cases[0].expected,ProcessObservation,'reference-output')
    assert bytes.fromhex(expected.stdout_hex)==b'indigo\n'
    from feature_rl.verifiers.loader import validate_reference
    proof=validate_reference(store,verifier,task.environment,pair.baseline,pair.reference)
    assert proof.baseline_matches==(False,)
    assert [role for source,role in opened]==['reference','reference','baseline']
    task_ref=replace_artifact(store,task_ref,private_oracle=result.artifacts[0])
    grader=GradingService(store=store,runtime=runtime,revision='a'*40)
    grader.select_task=lambda checked:prepared
    gold=grader.submissions.create(pair.baseline,SourceArchive({'src/click/__init__.py':SourceFile(b'def echo(value): print(str(value))  # alternative implementation\n',False)}).to_tar(),(),old.permissions.submission_policy)
    wrong=grader.submissions.create(pair.baseline,SourceArchive({'src/click/__init__.py':SourceFile(b'def echo(value): print("wrong")\n',False)}).to_tar(),(),old.permissions.submission_policy)
    opened.clear()
    original_read=store.get_bytes
    def no_reference(ref,**kwargs):
        assert ref!=pair.reference, 'candidate grading tried to read H'
        return original_read(ref,**kwargs)
    monkeypatch.setattr(store,'get_bytes',no_reference)
    assert read_grade(store,grader.grade(task_ref,gold,11).artifacts[0]).reward==1
    assert read_grade(store,grader.grade(task_ref,wrong,11).artifacts[0]).reward==0
    assert [role for source,role in opened]==['candidate','candidate']

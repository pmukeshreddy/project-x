"""DeepSWE import must preserve official grading and the solver projection."""
import io
import json
import os
from pathlib import Path
import tarfile

import pytest

from feature_rl.artifacts import AccessDenied, ArtifactStore
from feature_rl.contracts import ActorRole, Visibility


def test_solver_package_contains_only_baseline_instruction_and_runtime(tmp_path):
    from feature_rl.pipeline.deepswe import package_solver
    store = ArtifactStore(tmp_path/'cas', ActorRole.CONTROLLER)
    secret = store.put_bytes(b'private solution and tests', 'deepswe-private', Visibility.PRIVATE)
    instruction = b'Official request, unchanged.\n'
    baseline = b'opaque git archive bytes'
    runtime = {'image_digest': 'registry/b@sha256:'+'1'*64, 'base_commit': '2'*40}
    view, package = package_solver(store, instruction, baseline, runtime)
    reader = ArtifactStore(tmp_path/'cas', ActorRole.SOLVER)
    with pytest.raises(AccessDenied):
        reader.get_bytes(secret)
    assert reader.get_bytes(view.instruction) == instruction
    assert reader.get_bytes(view.workspace) == baseline
    with tarfile.open(fileobj=io.BytesIO(reader.get_bytes(package))) as archive:
        assert archive.getnames() == ['baseline.tar', 'instruction.md', 'runtime_manifest.json']
        assert archive.extractfile('instruction.md').read() == instruction
        assert archive.extractfile('baseline.tar').read() == baseline
        assert json.loads(archive.extractfile('runtime_manifest.json').read()) == runtime
    assert b'private solution and tests' not in reader.get_bytes(package)


def test_official_reward_zero_is_not_infrastructure_failure():
    from feature_rl.pipeline.deepswe import official_reward
    # Missing/skipped tests have already been scored by the official grader.
    assert official_reward(b'{"reward":0,"f2p_total":2,"f2p_passed":0}', 'exited', 0) == 0
    assert official_reward(b'{"reward":1,"f2p_total":2,"f2p_passed":2}', 'exited', 0) == 1


@pytest.mark.parametrize('raw,reason,code', [
    (b'', 'exited', 0), (b'{"reward":-1}', 'exited', 0),
    (b'{"reward":true}', 'exited', 0), (b'{"reward":1}', 'timeout', 0),
    (b'{"reward":1}', 'exited', 127), (b'{"reward":0,"reward":1}', 'exited', 0),
])
def test_missing_or_failed_official_run_cannot_be_published_as_a_grade(raw, reason, code):
    from feature_rl.pipeline.deepswe import official_reward
    with pytest.raises(ValueError):
        official_reward(raw, reason, code)


def test_cli_exposes_deepswe_without_configuring_authoring_or_training():
    from feature_rl.cli import parser
    args = parser().parse_args(['deepswe', '--state', '/tmp/state', 'build',
                              '--task', '/tmp/upstream/tasks/one'])
    assert args.command == 'deepswe' and args.action == 'build'
    assert args.config is None


def test_package_requires_successful_controls_and_reset(tmp_path):
    from feature_rl.pipeline.deepswe import DeepSWE
    pipeline = DeepSWE(tmp_path/'state')
    pipeline.state.write('task-example.json', {'task_id': 'example', 'validation': {
        'B': {'reward': 0}, 'H': {'reward': 0}, 'reset': {'restored': True}}})
    with pytest.raises(ValueError, match='B=0, H=1 and reset'):
        pipeline.package('example', tmp_path/'exports')


def test_cli_import_failure_returns_structured_failure(tmp_path, capsys):
    from feature_rl.cli import main
    status = main(['deepswe','--state',str(tmp_path/'state'),'validate','--task-id','not-built'])
    assert status == 2
    value = json.loads(capsys.readouterr().out)
    assert value['disposition'] == 'infrastructure_failure'


@pytest.mark.skipif(not os.environ.get('FEATURE_RL_DEEPSWE_STATE'), reason='requires built official ABS task')
def test_official_docker_episode_can_read_instruction_and_reset_edits():
    from feature_rl.pipeline.deepswe import DeepSWE
    pipeline = DeepSWE(Path(os.environ['FEATURE_RL_DEEPSWE_STATE']))
    episode = pipeline.start('abs-module-cache-flags')
    try:
        result = pipeline.execute(episode['episode'], ['bash','-c',
            'test -s /instruction.md && test ! -e /tests && test ! -e /solution'])
        assert result['exit_code'] == 0
        before = pipeline.snapshot(episode['container'])
        result = pipeline.execute(episode['episode'], ['bash','-c',
            'echo dirty >> go.mod; echo dirty > .feature-rl-reset-probe'])
        assert result['exit_code'] == 0
        assert pipeline.snapshot(episode['container'])['tree_sha256'] != before['tree_sha256']
        reset = pipeline.reset(episode['episode'])
        assert pipeline.snapshot(reset['container']) == before
    finally:
        pipeline.close(episode['episode'])

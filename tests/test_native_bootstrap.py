"""CPU checks for native bootstrap JSON. They do not run GPU training."""
import hashlib
import json
from pathlib import Path

from feature_rl.agents.protocol import DEEPSWE_INSTRUCTIONS, HARNESS
from feature_rl.artifacts import canonical_json
from feature_rl.contracts import ActorRole, TrainRequest, Visibility
from feature_rl.pipeline.configuration import CLIConfiguration
from feature_rl.training.files import inspect_directory, publish_directory, verify_directory
from feature_rl.training.skyrl_bridge import PINNED_HARBOR, PINNED_SKYRL


def _model(path):
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from transformers import PreTrainedTokenizerFast
    path.mkdir(mode=0o700, parents=True)
    raw = Tokenizer(WordLevel({'[UNK]': 0, 'probe': 1}, unk_token='[UNK]'))
    raw.pre_tokenizer = Whitespace()
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=raw, unk_token='[UNK]')
    tokenizer.save_pretrained(path)
    (path / 'config.json').write_text(json.dumps({
        'model_type': 'qwen2', '_name_or_path': 'Qwen/Qwen2.5-Coder-7B-Instruct'}))
    (path / 'model.safetensors').write_bytes(b'local-weight-bytes')


def _task(root, task_id):
    from feature_rl.pipeline.deepswe import DeepSWE
    state = root / 'state' / task_id
    pipeline = DeepSWE(state)
    package = pipeline.store.put_bytes(b'packaged ' + task_id.encode(), 'deepswe-solver-package', Visibility.PUBLIC)
    pipeline.state.write('task-' + task_id + '.json', {
        'task_id': task_id,
        'package': package.model_dump(mode='json'),
        'metadata': {'agent': {'timeout_sec': 300}, 'environment': {'memory_mb': 2048, 'storage_mb': 4096}},
    })
    return state, package


def _bootstrap(tmp_path, task_id, monkeypatch, model_id='Example/Model'):
    from feature_rl.pipeline.native_bootstrap import bootstrap_native
    monkeypatch.setenv('FEATURE_RL_REVISION', 'ab' * 20)
    model = tmp_path / task_id / 'model'
    _model(model)
    state, package = _task(tmp_path, task_id)
    output = tmp_path / task_id / 'config'
    artifacts = tmp_path / task_id / 'artifacts'
    result = bootstrap_native(model=model, model_id=model_id, work=tmp_path / task_id / 'work', state=state,
                              task_id=task_id, output=output, artifacts=artifacts)
    return model, artifacts, output, package, result


def test_revision_requires_explicit_commit(monkeypatch):
    import pytest
    from feature_rl.pipeline import native_bootstrap
    monkeypatch.delenv('FEATURE_RL_REVISION', raising=False)
    with pytest.raises(ValueError, match='FEATURE_RL_REVISION is required'):
        native_bootstrap._revision()
    monkeypatch.setenv('FEATURE_RL_REVISION', 'abc')
    with pytest.raises(ValueError, match='40 lowercase hex'):
        native_bootstrap._revision()
    monkeypatch.setenv('FEATURE_RL_REVISION', 'AB' * 20)
    with pytest.raises(ValueError, match='40 lowercase hex'):
        native_bootstrap._revision()
    commit = 'ab' * 20
    monkeypatch.setenv('FEATURE_RL_REVISION', commit)
    assert native_bootstrap._revision() == commit
    assert not hasattr(native_bootstrap, '_source_revision')
    assert not hasattr(native_bootstrap, '_git_revision')


def test_bootstrap_native_command_exists():
    import pytest
    from feature_rl.cli import parser
    args = parser().parse_args(['bootstrap-native', '--model', '/models/qwen',
                                 '--model-id', 'Qwen/Qwen2.5-Coder-7B-Instruct', '--work', '/checkpoints/qwen',
                                 '--state', '/state', '--task-id', 'abs-module-cache-flags', '--output', '/config'])
    assert args.command == 'bootstrap-native'
    assert args.model_id == 'Qwen/Qwen2.5-Coder-7B-Instruct'
    with pytest.raises(SystemExit):
        parser().parse_args(['bootstrap-native', '--model', '/models/qwen', '--work', '/checkpoints/qwen',
                              '--state', '/state', '--task-id', 'abs-module-cache-flags', '--output', '/config'])


def test_bootstrap_writes_real_native_configuration(tmp_path, monkeypatch):
    from feature_rl.artifacts import ArtifactStore
    from feature_rl.cli import read_json
    from feature_rl.registry import Registry
    from transformers import AutoTokenizer
    model, artifacts, output, package, result = _bootstrap(tmp_path, 'widget-cache', monkeypatch)
    controller = read_json(output / 'native-controller.json', CLIConfiguration)
    request = read_json(output / 'train-request.json', TrainRequest)
    training = request.config
    settings = controller.native.settings
    store = ArtifactStore(artifacts / 'store', ActorRole.CONTROLLER)
    registry = Registry(artifacts / 'registry', store)
    published = publish_directory(store=store, registry=registry, path=model)
    assert training.initial_policy.identity.weights == published
    assert training.reference_checkpoint == published
    assert verify_directory(store=store, ref=training.initial_policy.identity.weights, path=model) 
    assert training.initial_policy.identity.weights.sha256 not in {'0' * 64, 'a' * 64}
    assert training.initial_policy.identity.weights.kind == 'm7-native-directory'
    tokenizer_directory = Path(settings.tokenizer_directory)
    assert not (tokenizer_directory / 'model.safetensors').exists()
    digest = hashlib.sha256(canonical_json({item.path: item.sha256 for item in inspect_directory(tokenizer_directory).files})).hexdigest()
    assert settings.tokenizer_sha256 == digest == training.initial_policy.identity.tokenizer_digest
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_directory, local_files_only=True, trust_remote_code=False)
    expected = tuple(int(item) for item in tokenizer.encode('probe', add_special_tokens=False))
    assert settings.probe_tokens == expected
    assert all(0 <= item < len(tokenizer) for item in settings.probe_tokens)
    assert store.get_bytes(training.initial_policy.system_prompt) == DEEPSWE_INSTRUCTIONS.encode()
    assert '/app' in DEEPSWE_INSTRUCTIONS and '/workspace/source' not in DEEPSWE_INSTRUCTIONS
    assert 'fresh isolated worker' not in DEEPSWE_INSTRUCTIONS
    assert training.initial_policy.identity.model == 'Example/Model'
    assert training.initial_policy.identity.revision == published.sha256
    assert controller.revision == 'ab' * 20 and controller.native.revision == 'ab' * 20
    assert training.initial_policy.system_prompt.visibility == Visibility.PUBLIC
    assert training.initial_policy.harness_version == HARNESS
    assert training.initial_policy.identity.provider == 'skyrl'
    assert training.initial_policy.temperature == 1.0 and training.initial_policy.top_p == 1.0
    assert training.initial_policy.require_token_probabilities is True
    assert training.algorithm == 'grpo' and training.group_size == 4 and training.max_updates == 1
    assert training.framework == 'skyrl' and training.framework_version == PINNED_SKYRL
    assert training.backend_version == PINNED_HARBOR and training.budget_usd is None
    assert training.learning_rate == 1e-4
    assert training.tasks == (package,)
    assert controller.native.bootstrap == training
    assert settings.skyrl_checkout == '/opt/skyrl' and settings.num_gpus == 1
    assert settings.groups_per_update == 1 and settings.mini_batch_groups == 1
    assert settings.lora.enabled is True and settings.lora.dropout == 0.0
    assert settings.model_directory == str(model) and settings.reference_directory == str(model)
    assert result['weights']['sha256'] == published.sha256


def test_bootstrap_failure_reports_the_original_reason(tmp_path, capsys):
    import json
    from feature_rl.cli import main
    status = main(['bootstrap-native', '--model', str(tmp_path / 'missing-model'),
                   '--model-id', 'Example/Model', '--work', str(tmp_path / 'work'),
                   '--state', str(tmp_path / 'state'), '--task-id', 'missing-task',
                   '--output', str(tmp_path / 'out')])
    assert status == 2
    value = json.loads(capsys.readouterr().out)
    assert value['operation'] == 'construct'
    assert 'local model directory is required' in value['reason']
    assert str(tmp_path / 'missing-model') in value['reason']
    assert 'ValidationError' not in value['reason']


def test_bootstrap_requires_packaged_task_artifact(tmp_path, monkeypatch):
    import pytest
    from feature_rl.pipeline.deepswe import DeepSWE
    from feature_rl.pipeline.native_bootstrap import bootstrap_native
    monkeypatch.setenv('FEATURE_RL_REVISION', 'ab' * 20)
    model = tmp_path / 'model'
    _model(model)
    state = tmp_path / 'state'
    pipeline = DeepSWE(state)
    pipeline.state.write('task-unpackaged.json', {
        'task_id': 'unpackaged',
        'official_files': {'instruction.md': {'sha256': 'ab' * 32, 'kind': 'deepswe-instruction',
                                              'schema_version': 1, 'visibility': 'public', 'encoding': 'bytes'}},
        'metadata': {'agent': {'timeout_sec': 300}, 'environment': {'memory_mb': 2048, 'storage_mb': 4096}},
    })
    with pytest.raises(ValueError, match='packaged DeepSWE task artifact is required'):
        bootstrap_native(model=model, model_id='Example/Model', work=tmp_path / 'work', state=state,
                         task_id='unpackaged', output=tmp_path / 'out', artifacts=tmp_path / 'artifacts')


def test_bootstrap_accepts_another_packaged_task(tmp_path, monkeypatch):
    from feature_rl.cli import read_json
    _, _, output, package, _ = _bootstrap(tmp_path, 'other-packaged-task', monkeypatch)
    request = read_json(output / 'train-request.json', TrainRequest)
    assert request.config.tasks == (package,)
    assert request.config.tasks[0].kind == 'deepswe-solver-package'

"""CPU checks for native bootstrap JSON. They do not run GPU training."""
import hashlib
import json
from pathlib import Path

from feature_rl.agents.protocol import HARNESS, INSTRUCTIONS
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


def _bootstrap(tmp_path, task_id):
    from feature_rl.pipeline.native_bootstrap import bootstrap_native
    model = tmp_path / task_id / 'model'
    _model(model)
    state, package = _task(tmp_path, task_id)
    output = tmp_path / task_id / 'config'
    artifacts = tmp_path / task_id / 'artifacts'
    result = bootstrap_native(model=model, work=tmp_path / task_id / 'work', state=state,
                              task_id=task_id, output=output, artifacts=artifacts)
    return model, artifacts, output, package, result


def test_revision_without_git_hashes_installed_source(tmp_path, monkeypatch):
    from feature_rl.pipeline import native_bootstrap
    root = tmp_path / 'feature_rl'
    (root / 'pipeline').mkdir(parents=True)
    (root / '__init__.py').write_text('value = 1\n')
    (root / 'pipeline' / 'native_bootstrap.py').write_text('def revision():\n    return 1\n')
    (root / '__pycache__').mkdir()
    (root / '__pycache__' / 'native_bootstrap.pyc').write_bytes(b'compiled')
    (root / 'model.safetensors').write_bytes(b'weights')
    monkeypatch.setattr(native_bootstrap, '_package_root', lambda: root)
    first = native_bootstrap._revision()
    copy = tmp_path / 'same'
    copy.mkdir()
    for path in root.rglob('*'):
        if path.is_dir():
            continue
        target = copy / path.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    monkeypatch.setattr(native_bootstrap, '_package_root', lambda: copy)
    assert native_bootstrap._revision() == first
    assert len(first) == 64 and all(item in '0123456789abcdef' for item in first)
    (root / 'pipeline' / 'native_bootstrap.py').write_text('def revision():\n    return 2\n')
    monkeypatch.setattr(native_bootstrap, '_package_root', lambda: root)
    assert native_bootstrap._revision() != first


def test_bootstrap_native_command_exists():
    from feature_rl.cli import parser
    args = parser().parse_args(['bootstrap-native', '--model', '/models/qwen', '--work', '/checkpoints/qwen',
                                 '--state', '/state', '--task-id', 'abs-module-cache-flags', '--output', '/config'])
    assert args.command == 'bootstrap-native'
    assert args.task_id == 'abs-module-cache-flags' and args.model == '/models/qwen'


def test_bootstrap_writes_real_native_configuration(tmp_path):
    from feature_rl.artifacts import ArtifactStore
    from feature_rl.cli import read_json
    from feature_rl.registry import Registry
    from transformers import AutoTokenizer
    model, artifacts, output, package, result = _bootstrap(tmp_path, 'widget-cache')
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
    assert store.get_bytes(training.initial_policy.system_prompt) == INSTRUCTIONS.encode()
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


def test_bootstrap_accepts_another_packaged_task(tmp_path):
    from feature_rl.cli import read_json
    _, _, output, package, _ = _bootstrap(tmp_path, 'other-packaged-task')
    request = read_json(output / 'train-request.json', TrainRequest)
    assert request.config.tasks == (package,)
    assert request.config.tasks[0].kind == 'deepswe-solver-package'

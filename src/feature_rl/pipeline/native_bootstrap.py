"""Create native GRPO configuration from a local model directory and a packaged DeepSWE task."""
import hashlib
import os
import re
import shutil
from pathlib import Path

from feature_rl import contracts as c
from feature_rl.agents.protocol import DEEPSWE_INSTRUCTIONS, HARNESS
from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.pipeline.configuration import CLIConfiguration, NativeConfiguration
from feature_rl.pipeline.deepswe import DeepSWE
from feature_rl.registry import Registry
from feature_rl.training.files import inspect_directory, publish_directory
from feature_rl.training.native import NativeSettings
from feature_rl.training.skyrl_bridge import PINNED_HARBOR, PINNED_SKYRL

_TOKENIZER_NAMES = frozenset({
    'tokenizer.json', 'tokenizer_config.json', 'tokenizer.model', 'vocab.json', 'merges.txt',
    'special_tokens_map.json', 'added_tokens.json', 'chat_template.jinja', 'spiece.model',
})
_TOKENIZER_LIMIT = 64 * 1024 * 1024
_TOKENIZER_TOTAL = 128 * 1024 * 1024
_LEARNING_RATE = 1e-4


def _absolute(path, name):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError(name + ' must be an absolute path')
    return path


def _revision():
    value = os.environ.get('FEATURE_RL_REVISION')
    if value is None:
        raise ValueError('FEATURE_RL_REVISION is required')
    if re.fullmatch(r'[0-9a-f]{40}', value) is None:
        raise ValueError('FEATURE_RL_REVISION must be exactly 40 lowercase hex characters')
    return value


def _task_ref(record):
    if 'package' not in record:
        raise ValueError('packaged DeepSWE task artifact is required')
    return c.ArtifactRef.model_validate_json(canonical_json(record['package']))


def _bounded(manifest):
    return (len(manifest.files) <= 1024 and sum(item.size for item in manifest.files) <= _TOKENIZER_TOTAL
            and all(item.size <= _TOKENIZER_LIMIT for item in manifest.files))


def _copy_file(source, target):
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with source.open('rb') as incoming, target.open('xb') as outgoing:
        shutil.copyfileobj(incoming, outgoing)


def _tokenizer_source(path):
    return path.name in _TOKENIZER_NAMES


def _tokenizer_directory(model, artifacts):
    manifest = inspect_directory(model)
    names = {Path(item.path).name for item in manifest.files}
    has_tokenizer = bool(names & {'tokenizer.json', 'tokenizer.model', 'vocab.json'})
    if (_bounded(manifest) and names <= _TOKENIZER_NAMES and has_tokenizer
            and all(_tokenizer_source(model / item.path) for item in manifest.files)):
        return model
    destination = artifacts / 'tokenizer'
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    for item in list(destination.rglob('*')):
        if item.is_file() and not item.is_symlink():
            item.unlink()
    copied = False
    for item in manifest.files:
        source = model / item.path
        if item.size > _TOKENIZER_LIMIT or not _tokenizer_source(source):
            continue
        _copy_file(source, destination / item.path)
        copied = True
    if not copied:
        raise ValueError('local model directory has no bounded tokenizer files')
    bundle = inspect_directory(destination)
    if not _bounded(bundle):
        raise ValueError('tokenizer bundle exceeds the native tokenizer limit')
    return destination


def _limits(record):
    metadata = record['metadata']
    agent = metadata['agent']
    environment = metadata['environment']
    wall = float(agent['timeout_sec'])
    return c.ResourceLimits(
        wall_seconds=wall, cpu_seconds=wall,
        memory_bytes=int(environment['memory_mb']) * 1024 * 1024,
        pids=1024, output_bytes=8 * 1024 * 1024,
        disk_bytes=int(environment['storage_mb']) * 1024 * 1024,
        tool_calls=16, input_tokens=8192, output_tokens=1024)


def _probe_tokens(tokenizer_directory):
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(str(tokenizer_directory), local_files_only=True, trust_remote_code=False)
    identifiers = tuple(int(item) for item in tokenizer.encode('probe', add_special_tokens=False)[:32])
    if not identifiers or any(item < 0 or item >= len(tokenizer) for item in identifiers):
        raise ValueError('tokenizer did not produce valid probe token IDs')
    return identifiers


def _digest(directory):
    manifest = inspect_directory(directory)
    return hashlib.sha256(canonical_json({item.path: item.sha256 for item in manifest.files})).hexdigest()


def bootstrap_native(*, model, model_id, work, state, task_id, output, artifacts=Path('/artifacts')):
    model = _absolute(model, 'model')
    work = _absolute(work, 'work')
    state = _absolute(state, 'state')
    output = _absolute(output, 'output')
    artifacts = _absolute(artifacts, 'artifacts')
    if type(model_id) is not str or not model_id.strip():
        raise ValueError('explicit model id is required')
    if not model.is_dir():
        raise ValueError('local model directory is required: '+str(model))
    record = DeepSWE(state)._record(task_id)
    task = _task_ref(record)
    store = ArtifactStore(artifacts / 'store', c.ActorRole.CONTROLLER)
    registry = Registry(artifacts / 'registry', store)
    weights = publish_directory(store=store, registry=registry, path=model)
    reference = publish_directory(store=store, registry=registry, path=model)
    tokenizer_directory = _tokenizer_directory(model, artifacts)
    tokenizer_sha256 = _digest(tokenizer_directory)
    prompt = store.put_bytes(DEEPSWE_INSTRUCTIONS.encode(), 'agent-system-prompt', c.Visibility.PUBLIC)
    registry.register(prompt)
    policy = c.PolicyConfig(
        identity=c.ModelIdentity(provider='skyrl', model=model_id, revision=weights.sha256,
                                 weights=weights, tokenizer_digest=tokenizer_sha256),
        policy_version='base-' + weights.sha256[:16], temperature=1.0, top_p=1.0, seed=0,
        system_prompt=prompt, harness_version=HARNESS, require_token_probabilities=True)
    training = c.TrainingConfig(
        initial_policy=policy, reference_checkpoint=reference, tasks=(task,), limits=_limits(record),
        seeds=c.SeedPolicy(algorithm='deepswe-grpo-v1', seeds=(0,), same_cases_within_group=True),
        algorithm='grpo', group_size=4, max_updates=1, learning_rate=_LEARNING_RATE,
        framework='skyrl', framework_version=PINNED_SKYRL, backend_version=PINNED_HARBOR, budget_usd=None)
    revision = _revision()
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    settings = NativeSettings(
        skyrl_checkout='/opt/skyrl', model_directory=str(model), reference_directory=str(model),
        tokenizer_directory=str(tokenizer_directory), work_directory=str(work),
        tokenizer_sha256=tokenizer_sha256, num_gpus=1, groups_per_update=1, mini_batch_groups=1,
        probe_tokens=_probe_tokens(tokenizer_directory),
        lora={'enabled': True, 'dropout': 0.0})
    configuration = CLIConfiguration(
        store_root=str(artifacts / 'store'), registry_root=str(artifacts / 'registry'), revision=revision,
        native=NativeConfiguration(revision=revision, settings=settings, bootstrap=training))
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    (output / 'native-controller.json').write_bytes(canonical_json(configuration.model_dump(mode='json')))
    (output / 'train-request.json').write_bytes(canonical_json(c.TrainRequest(config=training).model_dump(mode='json')))
    return {'native_controller': str(output / 'native-controller.json'),
            'train_request': str(output / 'train-request.json'),
            'weights': weights.model_dump(mode='json'),
            'reference_checkpoint': reference.model_dump(mode='json'),
            'tokenizer_sha256': tokenizer_sha256, 'task': task.model_dump(mode='json')}

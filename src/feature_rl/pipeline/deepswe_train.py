"""Feed packaged DeepSWE episodes into the existing SkyRL GRPO update.

Collection uses DeepSWE start, execute, reset, collect, grade, and close.
The optimizer step and checkpoint are NativeSession.update and save_reload.
"""
import math
from pathlib import Path

from feature_rl.agents.protocol import Read, Submit, Write, parse_action
from feature_rl.training.core import group_advantages
from feature_rl.training.skyrl_bridge import UpdateRow
from feature_rl.training.torch_backend import CausalTurn

_MAX_TURNS = 16
_APP_TOOL = '''import pathlib,sys
root=pathlib.Path("/app")
kind,path,*rest=sys.argv[1:]
target=(root/path).resolve()
if not target.is_relative_to(root) or target==root: raise SystemExit("outside /app")
if kind=="read":
    data=target.read_bytes()
    if len(data)>262144: raise SystemExit("read output limit")
    sys.stdout.buffer.write(data)
else:
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(rest[0],encoding="utf-8")
'''


def _native_configuration(group_size, max_updates):
    from feature_rl.cli import read_json
    from feature_rl.contracts import TrainRequest
    from feature_rl.pipeline.configuration import CLIConfiguration
    path = next((item for item in (Path('native-controller.json'), Path('/config/native-controller.json')) if item.is_file()), None)
    if path is None:
        raise ValueError('existing native-controller.json is required')
    config = read_json(path, CLIConfiguration)
    if config.native is None:
        raise ValueError('existing native Qwen settings are required')
    if config.native.bootstrap is not None:
        training = config.native.bootstrap
    else:
        request = next((item for item in (Path('train-request.json'), Path('/config/train-request.json')) if item.is_file()), None)
        if request is None:
            raise ValueError('existing train-request.json is required')
        training = read_json(request, TrainRequest).config
    settings = config.native.settings
    settings = settings.model_copy(update={
        'num_gpus': 1,
        'lora': settings.lora.model_copy(update={'enabled': True, 'dropout': 0.0}),
    })
    training = training.model_copy(update={'group_size': group_size, 'max_updates': max_updates, 'algorithm': 'grpo'})
    return config, settings, training


def open_session(*, group_size, max_updates):
    from feature_rl.artifacts import ArtifactStore
    from feature_rl.contracts import ActorRole
    from feature_rl.registry import Registry
    from feature_rl.training.native import NativeSession
    config, settings, training = _native_configuration(group_size, max_updates)
    store = ArtifactStore(Path(config.store_root), ActorRole.CONTROLLER)
    registry = Registry(Path(config.registry_root), store, limits=config.registry_limits)
    return NativeSession(store=store, registry=registry, settings=settings,
                         configuration=training, revision=config.native.revision)


def _timeout(pipeline, task_id):
    limit = float(pipeline._record(task_id)['metadata']['agent']['timeout_sec'])
    if not 0 < limit:
        raise ValueError('DeepSWE agent timeout required')
    return min(30.0, limit)


def _argv(action, timeout):
    if isinstance(action, (Read, Write)):
        if action.path.startswith('/') or '\\' in action.path or '..' in action.path.split('/'):
            raise ValueError('unsafe source path')
        if isinstance(action, Read):
            return ['python3', '-c', _APP_TOOL, 'read', action.path], timeout
        return ['python3', '-c', _APP_TOOL, 'write', action.path, action.content], timeout
    if action.stdin:
        raise ValueError('DeepSWE execute has no stdin channel')
    return list(action.argv), min(float(action.timeout_seconds), timeout)


def _episode(pipeline, session, task_id, episode_id, index, timeout):
    policy = session.policy
    if hasattr(policy, 'model_copy'):
        policy = policy.model_copy(update={'seed': index})
    system = session.store.get_bytes(policy.system_prompt, max_envelope_bytes=524288, max_payload_bytes=262144)
    instruction = pipeline.execute(episode_id, ['cat', '/instruction.md'], timeout)
    if instruction['exit_code'] != 0:
        raise RuntimeError('DeepSWE instruction is unavailable')
    messages = [
        {'role': 'system', 'content': system.decode()},
        {'role': 'user', 'content': instruction['stdout']},
    ]
    turns = []
    for _ in range(_MAX_TURNS):
        session.backend.verify_policy(policy)
        _, context = session.backend.render(messages)
        max_tokens = min(1024, session.backend.max_seq_len - len(context))
        if max_tokens <= 0:
            break
        completion = session.backend.generate(context, policy=policy, max_tokens=max_tokens,
                                              timeout=timeout, session_id=task_id+':'+episode_id+':'+str(index)+':'+str(len(turns)))
        completion.validate(context=context, policy=policy, max_tokens=max_tokens, vocab_size=session.backend.vocab_size)
        if completion.logprobs is None:
            raise ValueError('GRPO requires sampled behavior log probabilities')
        turns.append(CausalTurn(context, completion.tokens, (True,)*len(completion.tokens), tuple(map(float, completion.logprobs))))
        try:
            action = parse_action(completion.text)
        except ValueError as exc:
            messages.extend(({'role': 'assistant', 'content': completion.text},
                             {'role': 'user', 'content': 'malformed action: '+str(exc)[:500]}))
            continue
        if isinstance(action, Submit):
            break
        try:
            argv, limit = _argv(action, timeout)
            result = pipeline.execute(episode_id, argv, limit)
            observation = {'exit_code': result['exit_code'], 'stdout': result['stdout'][:65536], 'stderr': result['stderr'][:65536]}
        except ValueError as exc:
            observation = {'exit_code': None, 'stdout': '', 'stderr': str(exc)[:500]}
        messages.extend(({'role': 'assistant', 'content': completion.text},
                         {'role': 'user', 'content': str(observation)}))
    patch = pipeline.collect(episode_id)
    graded = pipeline.grade(task_id, patch)
    reward = graded.get('reward')
    if reward is not None and reward not in (0, 1):
        raise ValueError('DeepSWE grade did not return binary reward')
    return turns, reward, patch


def _row(task_id, index, turns, reward, advantage):
    rows = []
    for turn_index, turn in enumerate(turns):
        last = turn_index == len(turns)-1
        rows.append(UpdateRow(
            CausalTurn(turn.context, turn.targets, turn.mask, turn.behavior, advantage),
            task_id, index, last, float(reward) if last else 0.))
    return rows


def _diagnostic_rows(task_id, episodes):
    """Reuse sampled trajectories and force advantage 1 so the optimizer can step.

    Verifier rewards, tokens, masks, and behavior logprobs stay the collected values.
    """
    sampled = [(index, turns, reward) for index, (turns, reward, _) in enumerate(episodes)
               if reward is not None and turns]
    if len(sampled) < 2:
        raise RuntimeError('diagnostic force update requires at least two sampled episodes')
    rows = []
    for index, turns, reward in sampled:
        rows.extend(_row(task_id, index, turns, reward, 1.0))
    if not rows:
        raise RuntimeError('diagnostic force update requires sampled turns')
    return rows


def _rows(task_id, episodes, *, diagnostic_force_update=False):
    rewards = tuple(reward for _, reward, _ in episodes)
    advantages = group_advantages(rewards)
    if sum(reward is not None for reward in rewards) < 2 or not any(item not in (None, 0, 0.0) for item in advantages):
        if not diagnostic_force_update:
            return [], False
        return _diagnostic_rows(task_id, episodes), True
    rows = []
    for index, (turns, reward, _) in enumerate(episodes):
        if advantages[index] is None or not turns:
            continue
        rows.extend(_row(task_id, index, turns, reward, advantages[index]))
    return rows, False


def _require_diagnostic_mutation(status):
    if status.get('optimizer_skipped'):
        raise RuntimeError('diagnostic force update skipped the optimizer')
    norm = status.get('grad_norm')
    if type(norm) is bool or type(norm) not in (int, float) or not math.isfinite(norm) or norm == 0:
        raise RuntimeError('diagnostic force update requires a nonzero finite gradient norm')
    changed = status.get('changed_trainable_shards')
    if type(changed) is not int or changed < 1:
        raise RuntimeError('diagnostic force update did not change trainable worker state')


def train_task(pipeline, task_id, *, group_size, max_updates, session=None, diagnostic_force_update=False):
    if type(group_size) is not int or group_size < 2:
        raise ValueError('GRPO group_size must be an integer of at least 2')
    if type(max_updates) is not int or max_updates < 1:
        raise ValueError('at least one GRPO update is required')
    timeout = _timeout(pipeline, task_id)
    owns_session = session is None
    if owns_session:
        session = open_session(group_size=group_size, max_updates=max_updates)
    rewards = []
    checkpoints = []
    updates = 0
    used_diagnostic = False
    try:
        for _ in range(max_updates):
            opened = pipeline.start(task_id)
            episode_id = opened['episode']
            episodes = []
            try:
                for index in range(group_size):
                    if index:
                        pipeline.reset(episode_id)
                    episodes.append(_episode(pipeline, session, task_id, episode_id, index, timeout))
            finally:
                pipeline.close(episode_id)
            rewards.append([reward for _, reward, _ in episodes])
            rows, forced = _rows(task_id, episodes, diagnostic_force_update=diagnostic_force_update)
            if not rows:
                continue
            status = session.update(rows, algorithm='grpo')
            if forced:
                _require_diagnostic_mutation(status)
                used_diagnostic = True
            elif status.get('optimizer_skipped'):
                continue
            checkpoints.append(session.save_reload())
            updates += 1
        if diagnostic_force_update and updates < 1:
            raise RuntimeError('diagnostic force update did not apply an optimizer step')
    finally:
        if owns_session:
            session.close()
    result = {'task_id': task_id, 'group_size': group_size, 'max_updates': max_updates,
              'updates': updates, 'rewards': rewards, 'checkpoints': checkpoints}
    if used_diagnostic:
        result['diagnostic_force_update'] = True
        result['real_reward_signal'] = False
    return result

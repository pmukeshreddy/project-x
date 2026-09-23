"""Wiring only: DeepSWE collection feeds the existing GRPO update. No GPU."""
from feature_rl.agents.backend import Completion
from feature_rl.training.core import group_advantages


class _Backend:
    max_seq_len = 128
    vocab_size = 32
    messages = None

    def verify_policy(self, policy):
        return None

    def render(self, messages):
        self.messages = messages
        return 'prompt', (1, 2)

    def generate(self, context, *, policy, max_tokens, timeout, session_id):
        return Completion('{"action":"submit"}', context, (3,), (-0.5,), policy.policy_version, 'stop')


class _Store:
    def get_bytes(self, ref, **kwargs):
        from feature_rl.agents.protocol import DEEPSWE_INSTRUCTIONS
        return DEEPSWE_INSTRUCTIONS.encode()


class _Policy:
    policy_version = 'policy-0'
    system_prompt = object()
    temperature = 1.0
    top_p = 1.0
    require_token_probabilities = True

    def model_copy(self, *, update):
        copied = _Policy()
        copied.policy_version = self.policy_version
        copied.seed = update['seed']
        return copied


class _Session:
    def __init__(self):
        self.backend = _Backend()
        self.policy = _Policy()
        self.store = _Store()
        self.updates = []
        self.checkpoints = 0
        self.closed = False

    def update(self, rows, *, algorithm):
        self.updates.append((algorithm, rows))
        return {'grad_norm': 1.0}

    def save_reload(self):
        self.checkpoints += 1
        return {'path': '/checkpoints/global_step_1'}

    def close(self):
        self.closed = True


def _pipeline(tmp_path, task_id, rewards):
    from feature_rl.pipeline.deepswe import DeepSWE
    pipeline = DeepSWE(tmp_path/'state')
    pipeline.state.write('task-'+task_id+'.json', {
        'task_id': task_id,
        'metadata': {'agent': {'timeout_sec': 300}, 'verifier': {'collect': []}},
    })
    calls = []
    patch = b'diff --git a/src/app.py b/src/app.py\n'

    def start(requested):
        calls.append(('start', requested))
        return {'episode': 'ab'*16, 'task_id': requested}

    def reset(episode):
        calls.append(('reset', episode))
        return {'episode': episode}

    def execute(episode, argv, timeout):
        calls.append(('execute', episode, tuple(argv)))
        return {'exit_code': 0, 'stdout': 'Fix the cache.\n', 'stderr': '', 'reason': 'exited'}

    def collect(episode):
        calls.append(('collect', episode))
        return patch

    def grade(requested, submitted=b''):
        calls.append(('grade', requested, submitted))
        return {'reward': rewards.pop(0)}

    def close(episode):
        calls.append(('close', episode))

    pipeline.start = start
    pipeline.reset = reset
    pipeline.execute = execute
    pipeline.collect = collect
    pipeline.grade = grade
    pipeline.close = close
    return pipeline, calls, patch


def test_deepswe_train_command_exists(tmp_path, monkeypatch):
    from feature_rl.cli import parser
    from feature_rl.pipeline.deepswe import dispatch_cli
    import feature_rl.pipeline.deepswe_train as adapter
    args = parser().parse_args(['deepswe', '--state', '/tmp/state', 'train',
                                 '--task-id', 'abs-module-cache-flags', '--group-size', '4', '--max-updates', '1'])
    assert args.command == 'deepswe' and args.action == 'train'
    assert args.task_id == 'abs-module-cache-flags' and args.group_size == 4 and args.max_updates == 1
    monkeypatch.setattr(adapter, 'train_task', lambda pipeline, task_id, group_size, max_updates: {
        'task_id': task_id, 'group_size': group_size, 'max_updates': max_updates})
    args.state = str(tmp_path/'state')
    assert dispatch_cli(args) == {'task_id': 'abs-module-cache-flags', 'group_size': 4, 'max_updates': 1}


def test_collected_grade_reward_enters_existing_grpo_update(tmp_path):
    from feature_rl.pipeline.deepswe_train import train_task
    task_id = 'widget-cache'
    pipeline, calls, patch = _pipeline(tmp_path, task_id, [0, 1, 0, 1])
    session = _Session()
    result = train_task(pipeline, task_id, group_size=4, max_updates=1, session=session)
    assert [name for name, *_ in calls].count('start') == 1
    assert [name for name, *_ in calls].count('reset') == 3
    assert [item for item in calls if item[0] == 'grade'] == [('grade', task_id, patch)]*4
    assert result['updates'] == 1 and result['rewards'] == [[0, 1, 0, 1]]
    assert session.checkpoints == 1 and session.closed is False
    algorithm, rows = session.updates[0]
    assert algorithm == 'grpo' and len(rows) == 4
    advantages = group_advantages((0, 1, 0, 1))
    assert [row.reward for row in rows] == [0.0, 1.0, 0.0, 1.0]
    assert [row.turn.advantage for row in rows] == list(advantages)
    assert {row.instance_id for row in rows} == {task_id}
    assert calls[-1][0] == 'close'
    from feature_rl.agents.protocol import DEEPSWE_INSTRUCTIONS, INSTRUCTIONS
    system, user = session.backend.messages
    assert system['content'] == DEEPSWE_INSTRUCTIONS
    assert user['content'] == 'Fix the cache.\n'
    assert '/workspace/source' not in system['content'] + user['content']
    assert 'fresh isolated worker' not in system['content'] + user['content']
    assert INSTRUCTIONS not in user['content']

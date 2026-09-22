"""Select unchanged repository test files and run ordinary commands directly."""
from pathlib import PurePosixPath
import shlex
from feature_rl.artifacts import canonical_json
from feature_rl.contracts import CommandSpec, Visibility
from feature_rl.environments import ExecutionRequest, SourceArchive
from .models import BehavioralInput, ProcessObservation, MAX_OUTPUT_BYTES
from .loader import read_bytes


class NoBehavioralInputs(ValueError):
    pass


def repository_tests(store, baseline, reference, origin, timeout):
    """Freeze whole changed pytest files and their test tree. Never parse test code."""
    selected = sorted(path for path, entry in reference.files.items()
        if (PurePosixPath(path).name.startswith('test_') and path.endswith('.py'))
        and baseline.files.get(path) != entry)
    if not selected:
        return ()
    # Only conventional standalone test trees. No layout inference or rewriting.
    if any(len(PurePosixPath(path).parts) < 2 or PurePosixPath(path).parts[0] not in {'tests', 'test'} for path in selected):
        raise NoBehavioralInputs('changed tests require a standalone tests/ or test/ directory')
    roots = {PurePosixPath(path).parts[0] for path in selected}
    files = {path: entry for path, entry in reference.files.items()
        if PurePosixPath(path).parts[0] in roots or path in {'conftest.py', 'pytest.ini', 'pyproject.toml', 'setup.cfg'}}
    archive = store.put_bytes(SourceArchive(files).to_tar(), 'repository-tests', Visibility.PRIVATE)
    command = CommandSpec(argv=('/usr/local/bin/python', '-m', 'pytest', '-q', '-o', 'addopts=', '--', *selected),
        working_directory='/workspace/checks', timeout_seconds=timeout)
    inp = BehavioralInput(mode='tests', command=command, stdin=archive, origin=origin)
    return (store.put_bytes(canonical_json(inp.model_dump(mode='json')), 'behavioral-input', Visibility.PRIVATE),)


def execution_request(store, inp, policy):
    data = read_bytes(store, inp.stdin, policy.stdin_bytes, private=True)
    command = inp.command
    if inp.mode == 'tests':
        # Validate the frozen tar on the controller, then stage it in the fresh
        # disposable worker. Pytest executes the original files, including its
        # normal fixtures/helpers, against the installed candidate package.
        SourceArchive.read(data, policy)
        command = CommandSpec(argv=('/bin/sh', '-c',
            'mkdir /workspace/checks && tar -xf - -C /workspace/checks && cd /workspace/checks && '
            + shlex.join(('/usr/local/bin/python','-c','import pytest')) + ' || exit 125; exec '
            + shlex.join(inp.command.argv)), working_directory='/workspace',
            timeout_seconds=inp.command.timeout_seconds)
    return ExecutionRequest(command=command, stdin=data, save_source=False)


def observe(output, cap=MAX_OUTPUT_BYTES):
    if (not output.cleanup_verified or output.failure_category in {'infrastructure', 'unresolved'}
            or output.reason not in {'completed', 'command_failed'} or output.exit_code is None):
        raise ValueError('command did not complete cleanly: '+output.reason)
    if len(output.stdout)+len(output.stderr)>cap:
        raise ValueError('command output exceeds bound')
    return ProcessObservation(exit_code=output.exit_code,
        stdout_hex=output.stdout.hex(), stderr_hex=output.stderr.hex())


def matches(inp, actual, expected):
    if inp.mode == 'tests':
        return actual.exit_code == expected.exit_code == 0
    return actual == expected

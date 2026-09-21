"""Real sandbox regression for candidate console scripts shadowing the harness."""
from pathlib import Path

from feature_rl.contracts import CommandSpec
from feature_rl.environments import DockerEngine
from feature_rl.environments.images import LINK_DEPS
from m4_fixtures import diagnostic_wheel, runtime_policy


def test_candidate_python_console_script_cannot_replace_controller_setup(tmp_path):
    policy = runtime_policy(lifecycle_seconds=30.0, memory_bytes=256 * 1024 * 1024)
    engine = DockerEngine(state_root=tmp_path / 'runtime',
                          socket_path=Path.home() / '.docker/run/docker.sock', policy=policy)
    # These inert wheel bytes become executable only inside the real sandbox.
    wheel = diagnostic_wheel('runtime-shadow', '1.0', extra_files={
        'runtime_shadow.py': b'def main():\n print("candidate interpreter intercepted command")\n',
        'runtime_shadow-1.0.dist-info/entry_points.txt':
            b'[console_scripts]\npython = runtime_shadow:main\n',
    })
    with engine.session(binding={'test': 'trusted-python-shadow'}, saved_source={}) as session:
        staged = session.execute(CommandSpec(argv=('/usr/local/bin/python', '-I', '-c',
            "import pathlib,sys; p=pathlib.Path('/workspace/built'); p.mkdir();"
            "(p/'runtime_shadow-1.0-py3-none-any.whl').write_bytes(sys.stdin.buffer.read())"),
            working_directory='/workspace', timeout_seconds=5.0), wheel)
        assert staged.reason == 'exited' and staged.exit_code == 0
        installed = session.execute(policy.profile.setup[2])
        assert installed.reason == 'exited' and installed.exit_code == 0

        # Establish that the installed entry point really wins ordinary PATH lookup.
        intercepted = session.execute(CommandSpec(argv=('python', '-c', 'print("trusted")'),
            working_directory='/workspace', timeout_seconds=5.0),
            environment=policy.profile.environment)
        assert intercepted.stdout == b'candidate interpreter intercepted command\n'

        linked = session.execute(LINK_DEPS, environment=policy.profile.environment)
        assert linked.reason == 'exited' and linked.exit_code == 0
        verified = session.execute(CommandSpec(argv=('/usr/local/bin/python', '-I', '-c',
            "import pathlib; print(pathlib.Path('/workspace/deps').is_symlink())"),
            working_directory='/workspace', timeout_seconds=5.0))
        assert verified.stdout == b'True\n'
    assert session.cleanup_verified
    assert engine.recover_owned() == []

"""Trusted adapter startup with a genuinely installed adversarial wheel."""
import json
from pathlib import Path

import pytest

from feature_rl.contracts import CommandSpec
from feature_rl.environments import DockerEngine, SourceArchive, SourceFile, SourceMapping
from feature_rl.environments.command_profiles import CommandRuntimeProfile
from feature_rl.environments.inference import infer_repository
from feature_rl.environments.toolchains import infer_toolchain
from feature_rl.environments.wheels import validate_wheel
from m4_fixtures import diagnostic_wheel, runtime_policy


def shadow_wheel():
    manifest = '''[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"
[project]
name = "startup-probe"
version = "1.0"
requires-python = ">=3.11"
[tool.setuptools]
packages = ["startup_probe"]
py-modules = ["json", "sitecustomize"]
package-dir = {"" = "src/startup_probe", "startup_probe" = "src/startup_probe/pkg"}
'''
    files = {'startup_probe/__init__.py': b'VALUE = "candidate import succeeded"\n',
        'json.py': b'SHADOW = "candidate json"\n',
        'sitecustomize.py': b'import os\nos.environ["STARTUP_SHADOW"] = "executed"\n'}
    tree = SourceArchive({'pyproject.toml': SourceFile(manifest.encode(), False),
        'src/startup_probe/pkg/__init__.py': SourceFile(files['startup_probe/__init__.py'], False),
        **{'src/startup_probe/'+name: SourceFile(files[name], False) for name in ('json.py', 'sitecustomize.py')}})
    metadata = infer_repository(tree)
    # Every submitted path is inside the original package or build manifest;
    # dependency requirements and distribution identity need not change.
    baseline = SourceArchive({'pyproject.toml': SourceFile(manifest.replace(
        'py-modules = ["json", "sitecustomize"]\n', '').replace(
            '"startup_probe" = "src/startup_probe/pkg"', '"startup_probe" = "src/startup_probe"').encode(), False),
        'src/startup_probe/__init__.py': tree.files['src/startup_probe/pkg/__init__.py']})
    tree.validate_changes(baseline, infer_repository(baseline)['source_roots'], ())
    profile = runtime_policy().profile.model_copy(update={
        'project_name': metadata['project_name'], 'project_version': metadata['project_version'],
        'source_mappings': tuple(SourceMapping(**mapping) for mapping in metadata['source_mappings'])})
    wheel = diagnostic_wheel('startup-probe', '1.0', extra_files=files)
    validate_wheel(wheel, tree, 'startup_probe-1.0-py3-none-any.whl', runtime_policy(profile=profile))
    return wheel


def test_declared_shadow_modules_pass_actual_source_and_wheel_validation():
    assert shadow_wheel()


def test_non_python_profile_does_not_receive_implicit_python_candidate_paths():
    from feature_rl.grading.bootstrap import adapter_argv
    tree = SourceArchive({'go.mod': SourceFile(b'module example.org/probe\ngo 1.23.0\n', False),
                          'main.go': SourceFile(b'package main\nfunc main() {}\n', False)})
    profile = CommandRuntimeProfile.from_plan(infer_toolchain(tree), interpreter_version='1.23.0',
        harness_interpreter_version='3.11.0', dependency_sha256='a'*64)
    argv = adapter_argv(b'print("ok")', profile.environment)
    assert json.loads(argv[-1]) == []


@pytest.mark.parametrize('path', ['', 'relative', '/workspace/site:', '/workspace/site:.:/workspace/deps'])
def test_invalid_explicit_python_paths_fail_closed(path):
    from feature_rl.grading.bootstrap import adapter_argv
    with pytest.raises(ValueError):
        adapter_argv(b'pass', (('PYTHONPATH', path),))


@pytest.mark.parametrize('safe_path', [True, False], ids=['with-safepath', 'without-safepath'])
def test_real_startup_shadow_is_blocked_without_changing_candidate_paths_or_hash_seed(tmp_path, safe_path):
    from feature_rl.grading.bootstrap import adapter_argv
    policy = runtime_policy(lifecycle_seconds=30.0, memory_bytes=256*1024*1024)
    engine = DockerEngine(state_root=tmp_path/'runtime', socket_path=Path.home()/'.docker/run/docker.sock', policy=policy)
    environment = tuple((name, value) for name, value in policy.profile.environment
                        if safe_path or name != 'PYTHONSAFEPATH')
    with engine.session(binding={'test': 'trusted-adapter-startup'}, saved_source={}) as session:
        stage = session.execute(CommandSpec(argv=('/usr/local/bin/python', '-I', '-c',
            "import pathlib,sys;p=pathlib.Path('/workspace/built');p.mkdir();"
            "(p/'startup_probe-1.0-py3-none-any.whl').write_bytes(sys.stdin.buffer.read());"
            "pathlib.Path('/workspace/json.py').write_text('SHADOW = \\\"cwd json\\\"\\n');"
            "pathlib.Path('/workspace/sitecustomize.py').write_text('import os\\nos.environ[\\\"STARTUP_SHADOW\\\"] = \\\"cwd executed\\\"\\n')"),
            working_directory='/workspace', timeout_seconds=5.0), shadow_wheel())
        assert stage.exit_code == 0
        assert session.execute(policy.profile.setup[2]).exit_code == 0
        old = session.execute(CommandSpec(argv=('/usr/local/bin/python', '-c',
            'import json,os;print(json.SHADOW,os.environ.get("STARTUP_SHADOW"))'),
            working_directory='/workspace', timeout_seconds=5.0), environment=environment)
        assert old.stdout == (b'candidate json executed\n' if safe_path else b'cwd json executed\n')
        adapter = b'''import json, os, sys, startup_probe
print(json.dumps({"startup": os.environ.get("STARTUP_SHADOW"), "json": json.__file__,
    "candidate": startup_probe.VALUE, "paths": sys.path[:2], "argv": sys.argv,
    "site_loaded": "site" in sys.modules, "implicit_cwd": "" in sys.path,
    "hash_randomization": sys.flags.hash_randomization,
    "hash": hash("feature-rl fixed seed probe")}))
'''
        outputs = []
        for _ in range(2):
            result = session.execute(CommandSpec(argv=adapter_argv(adapter, environment),
                working_directory='/workspace', timeout_seconds=5.0), environment=environment)
            assert result.exit_code == 0 and result.stderr == b''
            outputs.append(json.loads(result.stdout))
        assert outputs[0] == outputs[1]
        assert outputs[0] | {'hash': None, 'json': None} == {
            'startup': None, 'json': None, 'candidate': 'candidate import succeeded',
            'paths': ['/workspace/site', '/workspace/deps'], 'argv': ['-c'],
            'site_loaded': False, 'implicit_cwd': False, 'hash_randomization': 0, 'hash': None}
        assert outputs[0]['json'].startswith('/usr/local/lib/python')
    assert session.cleanup_verified and engine.recover_owned() == []

"""Opt-in real Docker checks using clearly test-only application sources."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

import pytest

from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole, CommandSpec, EvidenceRecord, Visibility
from feature_rl.environments import (DockerEngine, EnvironmentRuntime, ExecutionRequest,
                                     PolicyRejected, SandboxPolicy, SourceArchive, SourceFile)
from feature_rl.requirements.runtime_discovery import RuntimeDiscoveryService


pytestmark = pytest.mark.skipif(not os.environ.get('FEATURE_RL_COMMAND_RUNTIME_DOCKER'),
                                reason='explicit real toolchain Docker integration opt-in required')


@pytest.mark.parametrize('extra,source_paths', [
    ('', ('src/main.rs',)),
    ('[[bin]]\nname="custom"\npath="cmd/custom.rs"\n', ('cmd/custom.rs',)),
    ('', ('src/bin/one.rs', 'src/bin/two/main.rs')),
    ('[lib]\nname="actual_lib"\npath="library/root.rs"\ncrate-type=["rlib"]\n',
     ('src/main.rs', 'library/root.rs')),
])
def test_real_rust_fetch_accepts_genuine_targets_without_source(tmp_path, extra, source_paths):
    from feature_rl.environments.toolchains import infer_toolchain
    files = {'Cargo.toml': '[package]\nname="actual-cli"\nversion="0.1.0"\nedition="2021"\nrust-version="1.85.0"\n'+extra,
        'Cargo.lock': 'version=4\n[[package]]\nname="actual-cli"\nversion="0.1.0"\n',
        **{path: '// The acquisition container must never receive this source.\n' for path in source_paths}}
    tree = SourceArchive({name: SourceFile(data.encode(), False) for name, data in files.items()})
    plan = infer_toolchain(tree)
    for name, data in plan['resolver_files'].items():
        (tmp_path/name).write_bytes(data)
    assert {path.name for path in tmp_path.iterdir()} == {'Cargo.toml', 'Cargo.lock'}
    image = subprocess.run(['docker', 'image', 'inspect', '--format', '{{index .RepoDigests 0}}',
        'rust:1.85.0-bookworm'], capture_output=True, text=True, check=True, timeout=15).stdout.strip()
    result = subprocess.run(['docker', 'run', '--rm', '--network=none', '--read-only',
        '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=64', '--memory=256m',
        '--user=1000:1000', '--tmpfs', '/tmp:rw,noexec,nosuid,size=16m',
        '--tmpfs', '/cargo-cache:rw,nosuid,size=16m', '-e', 'CARGO_HOME=/cargo-cache',
        '--mount', 'type=bind,src='+str(tmp_path)+',dst=/manifest,readonly', '--workdir', '/manifest',
        '--entrypoint', '/usr/local/cargo/bin/cargo', image, 'fetch', '--locked', '--offline'],
        capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode()
    assert all(not (tmp_path/path).exists() for path in source_paths)


@pytest.mark.parametrize('language', ['node', 'rust', 'go', 'node_service', 'typescript'])
def test_real_offline_build_retained_product_discovery_and_reset(tmp_path, language):
    files = {
        'node': {'package.json': json.dumps({'name': 'runtime-test', 'version': '1.0.0',
                    'engines': {'node': '22.17.0'}, 'main': 'index.js'}),
                 'package-lock.json': json.dumps({'name': 'runtime-test', 'version': '1.0.0',
                    'lockfileVersion': 3, 'packages': {'': {'name': 'runtime-test', 'version': '1.0.0'}}}),
                 'index.js': 'console.log("runtime-test-ok");\n'},
        'rust': {'Cargo.toml': '[package]\nname="runtime-test"\nversion="0.1.0"\nedition="2021"\nrust-version="1.85.0"\n[dependencies]\nitoa="=1.0.15"\n',
                 'Cargo.lock': 'version=4\n[[package]]\nname="runtime-test"\nversion="0.1.0"\ndependencies=["itoa"]\n'
                               '[[package]]\nname="itoa"\nversion="1.0.15"\nsource="registry+https://github.com/rust-lang/crates.io-index"\n'
                               'checksum="4a5f13b858c8d314ee3e8f639011f7ccefe71f97f96e50151fb991f267928e2c"\n',
                 'src/main.rs': 'fn main() { assert_eq!(itoa::Buffer::new().format(42), "42"); println!("runtime-test-ok"); }\n'},
        'go': {'go.mod': 'module example.org/runtime-test\ngo 1.23.0\nrequire github.com/google/uuid v1.6.0\n',
               'go.sum': 'github.com/google/uuid v1.6.0 h1:NIvaJDMOsjHA8n1jAhLSgzrAzy1Hgr+hNrb57e+94F0=\n'
                         'github.com/google/uuid v1.6.0/go.mod h1:TIyPZe4MgqvfeYDBFedMoGGpEw/LqOeaOT+nhxU+yHo=\n',
               'main.go': 'package main\nimport "fmt"\nimport "github.com/google/uuid"\n'
                          'func main() { if uuid.Nil.String() != "00000000-0000-0000-0000-000000000000" { panic("uuid") }; fmt.Println("runtime-test-ok") }\n'},
    }['node' if language in ('node_service', 'typescript') else language]
    if language == 'typescript':
        dependency = {'version': '5.8.3', 'resolved': 'https://registry.npmjs.org/typescript/-/typescript-5.8.3.tgz',
            'integrity': 'sha512-p1diW6TqL9L07nNxvRMM7hMMw4c5XOo/1ibL4aAIGmSAt9slTE1Xgw5KWuof2uTOvCg9BY7ZRi+GaF+7sfgPeQ==',
            'dev': True, 'bin': {'tsc': 'bin/tsc', 'tsserver': 'bin/tsserver'}, 'engines': {'node': '>=14.17'}}
        package = json.loads(files['package.json'])
        package.update(main='dist/index.js', scripts={'build': 'tsc'}, devDependencies={'typescript': '5.8.3'})
        files['package.json'] = json.dumps(package)
        files['package-lock.json'] = json.dumps({'name': 'runtime-test', 'version': '1.0.0', 'lockfileVersion': 3,
            'packages': {'': {'name': 'runtime-test', 'version': '1.0.0', 'devDependencies': {'typescript': '5.8.3'}},
                         'node_modules/typescript': dependency}})
        files['src/index.ts'] = files.pop('index.js')
        files['tsconfig.json'] = '{"compilerOptions":{"outDir":"dist","module":"CommonJS"},"include":["src/**/*.ts"]}'
    if language == 'node_service':
        files['index.js'] = 'console.log(require("child_process").execFileSync("redis-cli",["incr","runtime-test-counter"],{encoding:"utf8"}).trim());\n'
        files['.feature-rl/runtime.toml'] = '''system_packages = ["redis-server", "redis-tools"]
[[services]]
name = "cache"
start = ["redis-server", "--bind", "127.0.0.1", "--port", "6379", "--save", ""]
readiness = ["redis-cli", "-h", "127.0.0.1", "ping"]
ready_stdout = "PONG\\n"
startup_seconds = 10.0
'''
    store = ArtifactStore(tmp_path/'store', ActorRole.CONTROLLER)
    source = SourceArchive({name: SourceFile(data.encode(), False) for name, data in files.items()})
    baseline = store.put_bytes(source.to_tar(), 'source-archive', Visibility.AUTHORING)
    evidence = EvidenceRecord(producer='test-only command toolchain integration source',
        command=('pytest', 'test_command_toolchains_docker.py', language), recorded_at=datetime.now(timezone.utc),
        exit_status=0, artifacts=(baseline,), revision='a'*40, scope='unit_diagnostic')
    policy = SandboxPolicy(platform='linux/arm64', disk_bytes=256*1024*1024,
        max_source_bytes=32*1024*1024, max_archive_bytes=64*1024*1024,
        max_staging_bytes=64*1024*1024, max_files=10000, lifecycle_seconds=180.0, cpu_seconds=120.0)
    engine = DockerEngine(state_root=tmp_path/'runtime', socket_path=Path.home()/'.docker/run/docker.sock',
        policy=policy, image_repository=os.environ.get('FEATURE_RL_TEST_IMAGE_REPOSITORY', 'localhost:5000/feature-rl-toolchain-tests'),
        buildx_plugin_directory=Path.home()/'.docker/cli-plugins', image_seconds=900.0)
    runtime = EnvironmentRuntime(store=store, engine=engine, revision='a'*40)
    prepared = runtime.prepare_repository(baseline, source_evidence=evidence)
    discovery = RuntimeDiscoveryService(runtime=runtime).discover(prepared)
    assert discovery.observation.interpreter_version == runtime.profile.interpreter_version
    assert discovery.observation.entry_points == runtime.profile.entry_points
    handle = runtime.open_workspace(prepared, role='baseline')
    try:
        build = runtime.build_snapshot(handle)
        entry = '/workspace/site/' + runtime.profile.entry_points[0]
        argv = ('node', entry) if language.startswith('node') or language == 'typescript' else (entry,)
        result = runtime.execute(handle, ExecutionRequest(command=CommandSpec(argv=argv,
            working_directory='/workspace', timeout_seconds=10.0), save_source=False), build=build)
        assert result.reason == 'completed', result.stderr
        expected = b'1\n' if language == 'node_service' else b'runtime-test-ok\n'
        assert result.stdout == expected
        assert result.cleanup_verified
        repeated = runtime.execute(handle, ExecutionRequest(command=CommandSpec(argv=argv,
            working_directory='/workspace', timeout_seconds=10.0), save_source=False), build=build)
        assert repeated.stdout == expected
        assert repeated.cleanup_verified
        editable = {'rust': 'src/main.rs', 'go': 'main.go', 'typescript': 'src/index.ts'}.get(language, 'index.js')
        edit = 'import pathlib;path=pathlib.Path(' + repr('/workspace/source/'+editable) + ');path.write_text(path.read_text()+"\\n// saved source probe\\n")'
        changed = runtime.execute_development(handle, ExecutionRequest(command=CommandSpec(
            argv=('/usr/local/bin/python', '-I', '-c', edit), working_directory='/workspace', timeout_seconds=5.0)))
        assert changed.reason == 'completed'
        assert changed.save_status == 'saved'
        assert changed.saved_source.tree_sha256 != source.tree_sha256
        with pytest.raises(PolicyRejected):
            runtime.execute(handle, ExecutionRequest(command=CommandSpec(argv=argv,
                working_directory='/workspace', timeout_seconds=10.0), save_source=False), build=build)
        assert runtime.reset(handle).tree_sha256 == source.tree_sha256
    finally:
        runtime.close(handle)
    if language == 'node_service':
        from feature_rl.environments.command_profiles import PREPARE_SITE
        from feature_rl.environments.runtime import StageFailure
        from feature_rl.environments.services import start_services
        service = runtime.profile.services[0].model_copy(update={'ready_stdout': 'not ready', 'startup_seconds': 0.2})
        runtime.profile = runtime.profile.model_copy(update={'services': (service,)})
        recipe = store.get_artifact(prepared.recipe)
        session = engine.session(binding={'purpose': 'test-service-readiness-failure'}, saved_source={}, image=recipe.image_digest)
        with pytest.raises(StageFailure, match='service readiness'):
            with session:
                runtime.stage(session, source)
                assert session.execute(PREPARE_SITE, environment=runtime.profile.environment).exit_code == 0
                start_services(runtime, session, recipe.image_digest)
        assert session.cleanup_verified
        with engine.state.lock():
            engine._require_clean_owned_state()
        original = service.model_copy(update={'ready_stdout': 'PONG\n', 'startup_seconds': 10.0})
        dead = original.model_copy(update={'name': 'exited', 'start': ('/usr/local/bin/python', '-I', '-c', 'pass'),
            'readiness': original.readiness.model_copy(update={'working_directory': '/workspace/services/exited'})})
        runtime.profile = runtime.profile.model_copy(update={'services': (original, dead)})
        failed = engine.session(binding={'purpose': 'test-exited-service'}, saved_source={}, image=recipe.image_digest)
        with pytest.raises(StageFailure, match='service process exited'):
            with failed:
                runtime.stage(failed, source)
                assert failed.execute(PREPARE_SITE, environment=runtime.profile.environment).exit_code == 0
                start_services(runtime, failed, recipe.image_digest)
        assert failed.cleanup_verified

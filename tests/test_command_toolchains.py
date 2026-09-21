"""Language detection and executable offline runtime boundaries."""
import hashlib
import json
import io
import tarfile

import pytest

from feature_rl.environments import PolicyRejected, SourceArchive, SourceFile


def source(files):
    return SourceArchive({name: SourceFile(value.encode() if isinstance(value, str) else value, False)
                          for name, value in files.items()})


def test_npm_plan_uses_locked_dependencies_and_declared_build_script():
    from feature_rl.environments.toolchains import infer_toolchain
    package = {'name': 'example', 'version': '1.2.3', 'engines': {'node': '22.17.0'},
               'packageManager': 'npm@10.9.2', 'scripts': {'build': 'tsc'}, 'main': 'dist/index.js'}
    lock = {'name': 'example', 'version': '1.2.3', 'lockfileVersion': 3,
            'packages': {'': {'name': 'example', 'version': '1.2.3'}}}
    tree = source({'package.json': json.dumps(package), 'package-lock.json': json.dumps(lock),
                   'src/index.ts': 'export const answer = 42;', 'tsconfig.json': '{}'})
    plan = infer_toolchain(tree)
    assert plan['language'] == 'node'
    assert plan['dependency_manager'] == 'npm'
    assert plan['toolchain_selector'] == '22.17.0'
    assert plan['entry_points'] == ('dist/index.js',)
    assert [command.argv for command in plan['build_commands']] == [
        ('npm', 'ci', '--offline', '--no-audit', '--no-fund'),
        ('npm', 'run', 'build', '--offline')]
    assert plan['manifest_hashes']['package-lock.json'] == hashlib.sha256(tree.files['package-lock.json'].data).hexdigest()


def test_cargo_plan_builds_locked_offline_with_actual_binary_entrypoint():
    from feature_rl.environments.toolchains import infer_toolchain
    tree = source({'Cargo.toml': '[package]\nname="hello-cli"\nversion="0.1.0"\nedition="2021"\nrust-version="1.85"\n',
                   'Cargo.lock': 'version=4\n[[package]]\nname="hello-cli"\nversion="0.1.0"\n',
                   'rust-toolchain.toml': '[toolchain]\nchannel="1.85.1"\n',
                   'src/main.rs': 'fn main() { println!("hello"); }'})
    plan = infer_toolchain(tree)
    assert plan['language'] == 'rust'
    assert plan['toolchain_selector'] == '1.85.1'
    assert plan['entry_points'] == ('target/release/hello-cli',)
    assert [command.argv for command in plan['build_commands']] == [
        ('cargo', 'build', '--release', '--locked', '--offline')]


def test_go_plan_binds_toolchain_and_builds_without_module_updates():
    from feature_rl.environments.toolchains import infer_toolchain
    tree = source({'go.mod': 'module example.org/hello\n\ngo 1.23.0\ntoolchain go1.23.6\n',
                   'main.go': 'package main\nfunc main() {}\n'})
    plan = infer_toolchain(tree)
    assert plan['language'] == 'go'
    assert plan['toolchain_selector'] == '1.23.6'
    assert plan['entry_points'] == ('bin/hello',)
    assert plan['build_commands'][0].argv == ('go', 'build', '-mod=readonly', '-o', '/workspace/site/bin/', './...')


@pytest.mark.parametrize('files,reason', [
    ({'package.json': '{"name":"a","version":"1.0.0","engines":{"node":"22.17.0"}}'}, 'lock'),
    ({'package.json': '{"name":"a","version":"1.0.0","engines":{"node":"22.17.0"}}',
      'package-lock.json': '{"lockfileVersion":3,"packages":{}}',
      'compose.yaml': 'services:\n  db:\n    image: postgres:17\n'}, 'service'),
    ({'Cargo.toml': '[workspace]\nmembers=["a"]\n', 'Cargo.lock': 'version=4\n'}, 'workspace'),
    ({'go.mod': 'module example.org/a\ngo 1.23.0\nreplace example.org/b => ../b\n'}, 'replace'),
])
def test_unfrozen_dependencies_or_unsupported_services_fail_closed(files, reason):
    from feature_rl.environments.toolchains import infer_toolchain
    with pytest.raises(PolicyRejected, match=reason):
        infer_toolchain(source(files))


def product_tar(entries):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w') as archive:
        for path, data, link in entries:
            item = tarfile.TarInfo(path)
            if link:
                item.type = tarfile.SYMTYPE
                item.linkname = data
                archive.addfile(item)
            else:
                item.mode = 0o755
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
    return out.getvalue()


def test_build_product_preserves_internal_npm_executable_links():
    from feature_rl.environments.products import BuildProduct
    from feature_rl.environments import SandboxPolicy
    product = BuildProduct.read(product_tar([
        ('node_modules/cli/main.js', b'console.log(42)', False),
        ('node_modules/.bin/cli', '../cli/main.js', True),
    ]), SandboxPolicy(platform='linux/arm64'))
    assert product.files['node_modules/cli/main.js'].executable
    assert product.links == {'node_modules/.bin/cli': '../cli/main.js'}
    assert BuildProduct.read(product.to_tar(), SandboxPolicy(platform='linux/arm64')).tree_sha256 == product.tree_sha256


@pytest.mark.parametrize('entries', [
    [('escape', '../../controller', True)],
    [('escape', '/etc/passwd', True)],
    [('a', 'b', True), ('b', 'a', True)],
    [('a', 'target', True), ('target', b'bytes', False), ('a/hidden', b'bytes', False)],
    [('dangling', 'absent', True)],
])
def test_build_product_rejects_links_outside_regular_retained_files(entries):
    from feature_rl.environments.products import BuildProduct
    from feature_rl.environments import SandboxPolicy, SourceRejected
    with pytest.raises(SourceRejected):
        BuildProduct.read(product_tar(entries), SandboxPolicy(platform='linux/arm64'))


def test_command_profile_preserves_manifest_binding_while_accepting_source_edits():
    from feature_rl.environments.command_profiles import CommandRuntimeProfile
    from feature_rl.environments.toolchains import infer_toolchain
    tree = source({'go.mod': 'module example.org/hello\ngo 1.23.0\n',
                   'main.go': 'package main\nfunc main() {}\n'})
    plan = infer_toolchain(tree)
    profile = CommandRuntimeProfile.from_plan(plan, interpreter_version='1.23.0',
                                               harness_interpreter_version='3.11.2',
                                               dependency_sha256='a' * 64)
    changed = source({'go.mod': 'module example.org/hello\ngo 1.23.0\n',
                      'main.go': 'package main\nfunc main() { println(42) }\n'})
    profile.validate_source(changed)
    with pytest.raises(PolicyRejected, match='manifest'):
        profile.validate_source(source({'go.mod': 'module example.org/hello\ngo 1.24.0\n',
                                       'main.go': 'package main\nfunc main() {}\n'}))


def test_resolution_context_contains_only_declarations_and_trusted_resolver():
    from feature_rl.environments.command_resolution import resolution_context
    from feature_rl.environments.toolchains import infer_toolchain
    from feature_rl.environments import SandboxPolicy
    tree = source({'go.mod': 'module example.org/hello\ngo 1.23.0\n',
                   'main.go': 'package main\n// SECRET_REPOSITORY_IMPLEMENTATION\nfunc main() {}\n'})
    policy = SandboxPolicy(platform='linux/arm64')
    payload = resolution_context(infer_toolchain(tree), policy, 'docker.io/library/golang@sha256:' + 'a' * 64)
    captured = SourceArchive.read(payload, policy)
    assert set(captured.files) == {'Dockerfile', 'input.json', 'resolve.py', 'manifests/go.mod'}
    assert b'SECRET_REPOSITORY_IMPLEMENTATION' not in payload
    assert captured.files['manifests/go.mod'].data == tree.files['go.mod'].data


def test_runtime_image_uses_exact_toolchain_supply_and_rejects_replacement(tmp_path):
    from feature_rl.artifacts import ArtifactStore
    from feature_rl.contracts import ActorRole, DependencyPin, Visibility
    from feature_rl.environments import SandboxPolicy
    from feature_rl.environments.command_profiles import CommandRuntimeProfile
    from feature_rl.environments.images import image_context
    from feature_rl.environments.toolchains import infer_toolchain
    tree = source({'go.mod': 'module example.org/hello\ngo 1.23.0\n', 'main.go': 'package main\nfunc main() {}'})
    supply = source({'go/cache/download/example.org/pkg/@v/v1.0.0.mod': 'module example.org/pkg'}).to_tar()
    digest = hashlib.sha256(supply).hexdigest()
    profile = CommandRuntimeProfile.from_plan(infer_toolchain(tree), interpreter_version='1.23.0',
        harness_interpreter_version='3.11.2', dependency_sha256=digest)
    store = ArtifactStore(tmp_path / 'store', ActorRole.CONTROLLER)
    pin = DependencyPin(name='go-locked-supply', version=digest, sha256=digest,
        artifact=store.put_bytes(supply, 'dependency-supply', Visibility.AUTHORING))
    policy = SandboxPolicy(platform='linux/arm64', image='example/runtime@sha256:' + 'a' * 64, profile=profile)
    context = SourceArchive.read(image_context(store, (pin,), policy), policy)
    assert context.files['toolchain-cache/go/cache/download/example.org/pkg/@v/v1.0.0.mod'].data == b'module example.org/pkg'
    replacement = pin.model_copy(update={'artifact': store.put_bytes(b'changed', 'dependency-supply', Visibility.AUTHORING)})
    with pytest.raises(PolicyRejected, match='hash'):
        image_context(store, (replacement,), policy)


def test_explicit_local_service_and_system_package_are_bound_to_toolchain_plan():
    from feature_rl.environments.toolchains import infer_toolchain
    tree = source({'go.mod': 'module example.org/hello\ngo 1.23.0\n', 'main.go': 'package main\nfunc main() {}',
        '.feature-rl/runtime.toml': '''system_packages = ["redis-server"]
[[services]]
name = "cache"
start = ["redis-server", "--bind", "127.0.0.1", "--port", "6379", "--save", ""]
readiness = ["redis-cli", "-h", "127.0.0.1", "ping"]
ready_stdout = "PONG\\n"
startup_seconds = 10.0
'''})
    plan = infer_toolchain(tree)
    assert plan['system_requirements'] == ('redis-server',)
    assert plan['services'][0].name == 'cache'
    assert plan['services'][0].state_directory == '/workspace/services/cache'
    assert plan['services'][0].readiness.argv == ('redis-cli', '-h', '127.0.0.1', 'ping')
    assert plan['services'][0].ready_stdout == 'PONG\n'


def test_python_packaging_remains_primary_for_rust_extension_repositories():
    from feature_rl.environments.toolchains import repository_language
    tree = source({'pyproject.toml': '[build-system]\nrequires=["maturin"]\nbuild-backend="maturin"\n',
                   'Cargo.toml': '[package]\nname="example"\nversion="1.0.0"\n'})
    assert repository_language(tree) == 'python'


def test_explicit_primary_toolchain_disambiguates_polyglot_repository():
    from feature_rl.environments.toolchains import repository_language
    tree = source({'Cargo.toml': '[package]\nname="example"\nversion="1.0.0"\n',
                   'package.json': '{"name":"example"}', '.feature-rl/runtime.toml': 'language="node"\n'})
    assert repository_language(tree) == 'node'


def test_npm_acquisition_rejects_unlocked_external_dependency_transports():
    from feature_rl.environments.toolchains import infer_toolchain
    tree = source({'package.json': json.dumps({'name': 'example', 'version': '1.0.0',
        'engines': {'node': '22.17.0'}, 'main': 'index.js',
        'dependencies': {'unsafe': 'https://internal.invalid/secret.tgz'}}),
        'package-lock.json': '{"lockfileVersion":3,"packages":{"":{"name":"example","version":"1.0.0"}}}',
        'index.js': 'console.log(1)'})
    with pytest.raises(PolicyRejected, match='dependency transport'):
        infer_toolchain(tree)


def test_python_runtime_does_not_ignore_unimplemented_explicit_configuration():
    from feature_rl.environments.toolchains import repository_language
    tree = source({'pyproject.toml': '[project]\nname="example"\nversion="1.0.0"\n',
                   '.feature-rl/runtime.toml': 'language="python"\nbuild=[["unsupported"]]\n'})
    with pytest.raises(PolicyRejected, match='Python runtime configuration'):
        repository_language(tree)

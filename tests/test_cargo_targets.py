"""Cargo acquisition declares real B targets without receiving their source."""
import tomllib

import pytest

from feature_rl.environments import PolicyRejected, SandboxPolicy, SourceArchive, SourceFile
from feature_rl.environments.toolchains import infer_toolchain


def repository(extra='', files=None, package_extra=''):
    values = {'Cargo.toml': '[package]\nname="real-cli"\nversion="0.1.0"\nedition="2021"\nrust-version="1.85.0"\n'+package_extra+extra,
        'Cargo.lock': 'version=4\n[[package]]\nname="real-cli"\nversion="0.1.0"\n'}
    values.update(files or {})
    return SourceArchive({name: SourceFile(data.encode(), False) for name, data in values.items()})


@pytest.mark.parametrize('extra,files,expected', [
    ('', {'src/main.rs': 'fn main() {}'}, [{'name': 'real-cli', 'path': 'src/main.rs'}]),
    ('[[bin]]\nname="custom"\npath="cmd/custom.rs"\ntest=false\n',
     {'cmd/custom.rs': 'fn main() {}'}, [{'name': 'custom', 'path': 'cmd/custom.rs', 'test': False}]),
    ('[[bin]]\nname="custom"\npath="src/main.rs"\n',
     {'src/main.rs': 'fn main() {}'}, [{'name': 'custom', 'path': 'src/main.rs'}]),
    ('', {'src/bin/alpha.rs': 'fn main() {}', 'src/bin/beta/main.rs': 'fn main() {}'},
     [{'name': 'alpha', 'path': 'src/bin/alpha.rs'}, {'name': 'beta', 'path': 'src/bin/beta/main.rs'}]),
    ('[[bin]]\nname="tool"\npath="custom.rs"\n',
     {'custom.rs': 'fn main() {}', 'src/bin/tool.rs': 'fn main() {}'}, [{'name': 'tool', 'path': 'custom.rs'}]),
    ('[[bin]]\nname="tool"\n', {'src/bin/tool.rs': 'fn main() {}'}, [{'name': 'tool', 'path': 'src/bin/tool.rs'}]),
])
def test_resolver_retains_genuine_binary_target_paths(extra, files, expected):
    plan = infer_toolchain(repository(extra, files))
    manifest = tomllib.loads(plan['resolver_files']['Cargo.toml'].decode())
    assert manifest['bin'] == expected
    assert 'lib' not in manifest
    assert plan['entry_points'] == tuple('target/release/'+item['name'] for item in expected)


@pytest.mark.parametrize('extra,library_path,expected', [
    ('', 'src/lib.rs', {'path': 'src/lib.rs'}),
    ('[lib]\nname="real_library"\npath="library/root.rs"\ncrate-type=["rlib"]\ndoctest=false\n',
     'library/root.rs', {'name': 'real_library', 'path': 'library/root.rs', 'crate-type': ['rlib'], 'doctest': False}),
])
def test_resolver_retains_actual_library_alongside_binary(extra, library_path, expected):
    plan = infer_toolchain(repository(extra, {'src/main.rs': 'fn main() {}', library_path: 'pub fn value() {}'}))
    manifest = tomllib.loads(plan['resolver_files']['Cargo.toml'].decode())
    assert manifest['lib'] == expected


@pytest.mark.parametrize('extra,files,reason', [
    ('[[bin]]\npath="src/main.rs"\n', {'src/main.rs': 'fn main() {}'}, 'name'),
    ('[[bin]]\nname="tool"\npath="absent.rs"\n', {'src/main.rs': 'fn main() {}'}, 'source'),
    ('[[bin]]\nname="tool"\npath="one.rs"\n[[bin]]\nname="tool"\npath="two.rs"\n',
     {'one.rs': 'fn main() {}', 'two.rs': 'fn main() {}'}, 'duplicate'),
    ('', {'src/bin/tool.rs': 'fn main() {}', 'src/bin/tool/main.rs': 'fn main() {}'}, 'ambiguous'),
    ('[lib]\npath="missing.rs"\n', {'src/main.rs': 'fn main() {}'}, 'source'),
    ('[[bin]]\nname="../escape"\npath="src/main.rs"\n', {'src/main.rs': 'fn main() {}'}, 'name'),
])
def test_invalid_or_ambiguous_target_declarations_fail_closed(extra, files, reason):
    with pytest.raises(PolicyRejected, match=reason):
        infer_toolchain(repository(extra, files))


def test_disabled_automatic_targets_do_not_create_unrequested_entries():
    plan = infer_toolchain(repository('[[bin]]\nname="custom"\npath="custom.rs"\n',
        {'custom.rs': 'fn main() {}', 'src/main.rs': 'fn main() {}', 'src/lib.rs': 'pub fn value() {}'},
        package_extra='autobins=false\nautolib=false\n'))
    manifest = tomllib.loads(plan['resolver_files']['Cargo.toml'].decode())
    assert plan['entry_points'] == ('target/release/custom',)
    assert 'lib' not in manifest


@pytest.mark.parametrize('flag,expected', [('', ('custom',)),
    ('autobins=true\n', ('custom', 'real-cli', 'tool')), ('autobins=false\n', ('custom',))])
def test_edition_2015_explicit_bins_preserve_cargo_automatic_target_default(flag, expected):
    tree = repository('[[bin]]\nname="custom"\npath="custom.rs"\n',
        {'custom.rs': 'fn main() {}', 'src/main.rs': 'fn main() {}', 'src/bin/tool.rs': 'fn main() {}'},
        package_extra=flag)
    original = tree.files['Cargo.toml']
    tree.files['Cargo.toml'] = SourceFile(original.data.replace(b'edition="2021"', b'edition="2015"'), False)
    plan = infer_toolchain(tree)
    assert plan['entry_points'] == tuple('target/release/'+name for name in expected)


def test_cargo_acquisition_context_has_no_real_or_fabricated_source():
    from feature_rl.environments.command_resolution import resolution_context
    plan = infer_toolchain(repository(files={'src/main.rs': 'fn main() { /* PRIVATE_SOURCE */ }'}))
    policy = SandboxPolicy(platform='linux/arm64')
    data = resolution_context(plan, policy, 'docker.io/library/rust@sha256:'+'a'*64)
    archive = SourceArchive.read(data, policy)
    assert set(archive.files) == {'Dockerfile', 'input.json', 'resolve.py', 'manifests/Cargo.toml', 'manifests/Cargo.lock'}
    assert b'PRIVATE_SOURCE' not in data and b'fn main' not in data
    assert tomllib.loads(archive.files['manifests/Cargo.toml'].data.decode())['bin'][0]['path'] == 'src/main.rs'


def test_bounded_manifest_writer_preserves_arrays_tables_and_dependency_semantics():
    from feature_rl.environments.cargo_manifest import cargo_manifest
    value = {'package': {'name': 'real-cli', 'version': '0.1.0'},
        'bin': [{'name': 'a', 'path': 'src/bin/a.rs'}, {'name': 'b', 'path': 'src/bin/b.rs', 'required-features': ['extra']}],
        'features': {'extra': ['dep:itoa'], 'default': []},
        'target': {'cfg(unix)': {'dependencies': {'itoa': {'version': '=1.0.15', 'optional': True, 'default-features': False}}}}}
    encoded = cargo_manifest(value)
    assert tomllib.loads(encoded.decode()) == value
    assert b'[["bin"]]' in encoded
    with pytest.raises(PolicyRejected, match='byte limit'):
        cargo_manifest(value, max_bytes=10)
    with pytest.raises(PolicyRejected, match='unsupported'):
        cargo_manifest({'bin': [{'name': 'a'}, 'not a table']})

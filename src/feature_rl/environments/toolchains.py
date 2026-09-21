"""Inert, source-grounded plans for the supported non-Python toolchains.

Dependency acquisition receives declarations only. Repository commands are
retained as argv and run later in the ordinary networkless Docker sandbox.
"""
import hashlib
import json
import re
import tomllib

from feature_rl.contracts import CommandSpec
from .archive import SourceArchive, safe_path
from .models import PolicyRejected


MANIFESTS = {'node': 'package.json', 'rust': 'Cargo.toml', 'go': 'go.mod'}
CONFIG = '.feature-rl/runtime.toml'
SERVICE_FILES = ('compose.yml', 'compose.yaml', 'docker-compose.yml', 'docker-compose.yaml')


def repository_language(source):
    """Respect explicit selection and preserve Python packaging of native code."""
    configuration = {}
    if CONFIG in source.files:
        try:
            configuration = tomllib.loads(_text(source, CONFIG))
            explicit = configuration.get('language')
        except ValueError as exc:
            raise PolicyRejected('invalid explicit runtime configuration') from exc
        if explicit is not None:
            if explicit == 'python' and any(path in source.files for path in ('pyproject.toml', 'setup.cfg', 'setup.py')):
                if set(configuration) - {'language'}:
                    raise PolicyRejected('explicit Python runtime configuration is not implemented by the wheel profile')
                return explicit
            if explicit not in MANIFESTS or MANIFESTS[explicit] not in source.files:
                raise PolicyRejected('declared primary toolchain lacks its build manifest')
            return explicit
    languages = [name for name, path in MANIFESTS.items() if path in source.files]
    if any(path in source.files for path in ('pyproject.toml', 'setup.cfg', 'setup.py')):
        if set(configuration) - {'language'}:
            raise PolicyRejected('explicit Python runtime configuration is not implemented by the wheel profile')
        return 'python'
    if len(languages) != 1:
        raise PolicyRejected('exactly one root toolchain is required; ambiguous or missing build manifests')
    return languages[0]


def _text(source, path):
    try:
        data = source.files[path].data
        if len(data) > 1024 * 1024:
            raise PolicyRejected('toolchain manifest exceeds byte bound: ' + path)
        return data.decode('utf-8')
    except (KeyError, UnicodeError) as exc:
        raise PolicyRejected('required toolchain manifest/lock is unavailable: ' + path) from exc


def _json(source, path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result
    value = json.loads(_text(source, path), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise PolicyRejected('toolchain manifest must be an object: ' + path)
    return value


def _command(argv):
    return CommandSpec(argv=tuple(argv), working_directory='/workspace/site', timeout_seconds=120.0)


def _selector(value, language):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+){0,2}', value.removeprefix('v')):
        raise PolicyRejected(language + ' requires a numeric toolchain selector in its runtime declarations')
    return value.removeprefix('v')


def _node(source):
    package = _json(source, 'package.json')
    def dependency_versions(values):
        if not isinstance(values, dict):
            raise PolicyRejected('npm dependency declarations must be mappings')
        for version in values.values():
            if isinstance(version, dict):
                dependency_versions(version)
            elif not isinstance(version, str) or not re.fullmatch(r'[0-9xX*.+<>=~^| -]+', version):
                raise PolicyRejected('npm dependency transport/tag requires an implemented registry policy')
    for section in ('dependencies', 'devDependencies', 'optionalDependencies', 'overrides'):
        dependency_versions(package.get(section, {}))
    if package.get('workspaces') or any(name in source.files for name in ('pnpm-lock.yaml', 'yarn.lock')):
        raise PolicyRejected('Node workspaces/pnpm/yarn require an implemented dependency resolver')
    if '.npmrc' in source.files:
        raise PolicyRejected('repository npm configuration requires an implemented registry policy')
    lock = _json(source, 'package-lock.json')
    if lock.get('lockfileVersion') not in (2, 3) or not isinstance(lock.get('packages'), dict):
        raise PolicyRejected('npm requires a complete package-lock version 2 or 3')
    for path, item in lock['packages'].items():
        if not isinstance(item, dict) or item.get('link'):
            raise PolicyRejected('npm lock links/local dependencies are unsupported')
        if path:
            safe_path(path)
            if (not isinstance(item.get('resolved'), str)
                    or not item['resolved'].startswith('https://registry.npmjs.org/')
                    or not re.fullmatch(r'sha512-[A-Za-z0-9+/]+={0,2}', item.get('integrity', ''))):
                raise PolicyRejected('npm lock requires registry.npmjs.org HTTPS packages with SHA512 integrity')
    managers = package.get('packageManager', '')
    if managers and not re.fullmatch(r'npm@[0-9]+\.[0-9]+\.[0-9]+', managers):
        raise PolicyRejected('Node dependency manager must be an exact npm version')
    selector = next((_text(source, path).strip() for path in ('.node-version', '.nvmrc') if path in source.files),
                    package.get('engines', {}).get('node'))
    entries = []
    bins = package.get('bin', {})
    if isinstance(bins, str):
        entries.append(bins)
    elif isinstance(bins, dict):
        entries.extend(bins.values())
    else:
        raise PolicyRejected('npm bin must be a path or path mapping')
    if package.get('main'):
        entries.append(package['main'])
    if not entries:
        if 'index.js' not in source.files:
            raise PolicyRejected('Node requires declared main/bin entry points or index.js')
        entries.append('index.js')
    commands = [_command(('npm', 'ci', '--offline', '--no-audit', '--no-fund'))]
    if 'build' in package.get('scripts', {}):
        commands.append(_command(('npm', 'run', 'build', '--offline')))
    manifests = ['package.json', 'package-lock.json', '.node-version', '.nvmrc', '.npmrc', 'pnpm-lock.yaml', 'yarn.lock']
    # Build settings are part of the pinned recipe, not dependency-selection code.
    manifests.extend(path for path in source.files if re.fullmatch(r'tsconfig(?:\.[A-Za-z0-9_-]+)?\.json', path))
    resolver = {key: package[key] for key in ('name', 'version', 'dependencies', 'devDependencies', 'optionalDependencies', 'overrides') if key in package}
    return dict(language='node', dependency_manager='npm', project_name=package['name'],
                project_version=package.get('version', ''), toolchain_selector=_selector(selector, 'Node'),
                manager_version=managers.removeprefix('npm@'), constraints=package.get('engines', {}),
                entry_points=entries, build_commands=commands, manifests=manifests,
                resolver_files={'package.json': json.dumps(resolver, sort_keys=True).encode(),
                                'package-lock.json': source.files['package-lock.json'].data})


def _rust_targets(source, manifest):
    """Resolve Cargo's binary/lib declarations against actual baseline files."""
    from .models import SourceRejected
    package = manifest['package']
    for flag in ('autobins', 'autolib'):
        if flag in package and type(package[flag]) is not bool:
            raise PolicyRejected('Cargo automatic target flags must be booleans')

    def target_name(name):
        try:
            if type(name) is not str or '/' in name or safe_path(name) != name:
                raise ValueError('invalid name')
        except (ValueError, SourceRejected) as exc:
            raise PolicyRejected('invalid Cargo binary target name') from exc
        return name

    def target_path(path):
        try:
            path = safe_path(path.removeprefix('./'))
        except (AttributeError, ValueError, SourceRejected) as exc:
            raise PolicyRejected('invalid Cargo target source path') from exc
        if path not in source.files:
            raise PolicyRejected('Cargo target source is absent from baseline: '+path)
        return path

    automatic = []
    if 'src/main.rs' in source.files:
        automatic.append({'name': target_name(package['name']), 'path': 'src/main.rs'})
    for path in sorted(source.files):
        match = re.fullmatch(r'src/bin/([^/]+)\.rs|src/bin/([^/]+)/main\.rs', path)
        if match:
            automatic.append({'name': target_name(match[1] or match[2]), 'path': path})
    declarations = manifest.get('bin', [])
    if type(declarations) is not list or len(declarations) > 64:
        raise PolicyRejected('Cargo binary declarations must be a bounded array of tables')
    targets, names, paths = [], set(), set()
    for declared in declarations:
        if type(declared) is not dict:
            raise PolicyRejected('Cargo binary declaration must be a table')
        name = target_name(declared.get('name'))
        if name in names:
            raise PolicyRejected('duplicate Cargo binary target name: '+name)
        path = declared.get('path')
        if path is None:
            candidates = [item['path'] for item in automatic if item['name'] == name]
            if len(candidates) != 1:
                raise PolicyRejected('Cargo binary target source is absent or ambiguous: '+name)
            path = candidates[0]
        path = target_path(path)
        targets.append({**declared, 'name': name, 'path': path})
        names.add(name)
        paths.add(path)
    auto_default = package.get('edition', '2015') != '2015' or not declarations
    if package.get('autobins', auto_default):
        # Cargo suppresses auto targets overridden by either explicit name or path.
        remaining = [item for item in automatic if item['name'] not in names and item['path'] not in paths]
        for item in remaining:
            if item['name'] in names:
                raise PolicyRejected('ambiguous automatic Cargo binary target: '+item['name'])
            targets.append(item)
            names.add(item['name'])
    if not targets or len(targets) > 64:
        raise PolicyRejected('library-only Rust requires an implemented compiled observer; declare bounded binary entry points')
    library = manifest.get('lib')
    if library is not None and type(library) is not dict:
        raise PolicyRejected('Cargo library declaration must be a table')
    if library is not None or package.get('autolib', True) and 'src/lib.rs' in source.files:
        library = {**(library or {}), 'path': target_path((library or {}).get('path', 'src/lib.rs'))}
    return targets, library


def _rust(source):
    manifest = tomllib.loads(_text(source, 'Cargo.toml'))
    if 'workspace' in manifest or 'workspace' in manifest.get('package', {}):
        raise PolicyRejected('Cargo workspaces require an implemented workspace resolver')
    if any(path in source.files for path in ('.cargo/config', '.cargo/config.toml')):
        raise PolicyRejected('repository Cargo configuration requires an implemented source policy')
    if 'patch' in manifest or 'replace' in manifest:
        raise PolicyRejected('Cargo patch/replace declarations are unsupported')
    package = manifest['package']
    selector = package.get('rust-version')
    if 'rust-toolchain.toml' in source.files:
        declaration = tomllib.loads(_text(source, 'rust-toolchain.toml'))['toolchain']
        if set(declaration) - {'channel', 'profile'}:
            raise PolicyRejected('additional Rust components/targets require explicit image support')
        selector = declaration['channel']
    elif 'rust-toolchain' in source.files:
        selector = _text(source, 'rust-toolchain').strip()
    lock = tomllib.loads(_text(source, 'Cargo.lock'))
    for item in lock.get('package', []):
        if item.get('source') and (item['source'] != 'registry+https://github.com/rust-lang/crates.io-index'
                                   or not re.fullmatch(r'[0-9a-f]{64}', item.get('checksum', ''))):
            raise PolicyRejected('Cargo lock requires crates.io packages with SHA256 checksums')
    tables = [manifest, *manifest.get('target', {}).values()]
    for table in tables:
        for section in ('dependencies', 'dev-dependencies', 'build-dependencies'):
            for dependency in table.get(section, {}).values():
                if isinstance(dependency, dict) and any(key in dependency for key in ('path', 'git', 'registry', 'workspace')):
                    raise PolicyRejected('Cargo path/git/alternate-registry dependencies are unsupported')
    targets, library = _rust_targets(source, manifest)
    entries = ['target/release/' + item['name'] for item in targets]
    # Fetch only needs real target declarations, not the files they refer to.
    # The acquisition image receives dependencies/metadata and no project code.
    resolver = dict(package={'name': package['name'], 'version': package['version'], 'edition': package.get('edition', '2015')},
                    bin=targets, **{key: manifest[key] for key in ('dependencies', 'dev-dependencies', 'build-dependencies', 'target', 'features') if key in manifest})
    if library is not None:
        resolver['lib'] = library
    from .cargo_manifest import cargo_manifest
    return dict(language='rust', dependency_manager='cargo', project_name=package['name'], project_version=package['version'],
                toolchain_selector=_selector(selector, 'Rust'), manager_version='', constraints={}, entry_points=entries,
                build_commands=[_command(('cargo', 'build', '--release', '--locked', '--offline'))],
                manifests=['Cargo.toml', 'Cargo.lock', 'rust-toolchain', 'rust-toolchain.toml', '.cargo/config', '.cargo/config.toml'],
                resolver_files={'Cargo.lock': source.files['Cargo.lock'].data, 'Cargo.toml': cargo_manifest(resolver)})


def _go(source):
    manifest = _text(source, 'go.mod')
    if re.search(r'^\s*(replace|exclude)\b', manifest, re.M) or 'go.work' in source.files:
        raise PolicyRejected('Go workspace/replace/exclude declarations require an implemented resolver')
    module = re.search(r'^\s*module\s+(\S+)\s*$', manifest, re.M)
    language = re.search(r'^\s*go\s+([0-9.]+)\s*$', manifest, re.M)
    selected = re.search(r'^\s*toolchain\s+go([0-9.]+)\s*$', manifest, re.M)
    if module is None or language is None:
        raise PolicyRejected('Go requires explicit module and go toolchain declarations')
    if re.search(r'^\s*require\b', manifest, re.M) and 'go.sum' not in source.files:
        raise PolicyRejected('Go dependencies require a retained go.sum lock')
    name = module[1].strip('"')
    entries = []
    for path in source.files:
        if not path.endswith('.go') or path.endswith('_test.go'):
            continue
        if re.search(r'^\s*package\s+main\b', _text(source, path), re.M):
            parent = path.rpartition('/')[0]
            entry = parent.rsplit('/', 1)[-1] if parent else name.rsplit('/', 1)[-1]
            entries.append('bin/' + entry)
    if not entries:
        raise PolicyRejected('library-only Go requires an implemented package observer; declare a package main entry point')
    return dict(language='go', dependency_manager='go', project_name=name, project_version='',
                toolchain_selector=_selector(selected[1] if selected else language[1], 'Go'),
                manager_version='', constraints={}, entry_points=entries,
                build_commands=[_command(('go', 'build', '-mod=readonly', '-o', '/workspace/site/bin/', './...'))],
                manifests=['go.mod', 'go.sum', 'go.work', 'go.work.sum'],
                resolver_files={path: source.files[path].data for path in ('go.mod', 'go.sum') if path in source.files})


def infer_toolchain(source: SourceArchive):
    try:
        config = tomllib.loads(_text(source, CONFIG)) if CONFIG in source.files else {}
        if set(config) - {'language', 'build', 'entry_points', 'services', 'system_packages'}:
            raise PolicyRejected('unsupported explicit runtime configuration field')
        if any(path in source.files for path in SERVICE_FILES):
            raise PolicyRejected('declared services require an isolated service lifecycle; unsupported by this networkless runtime')
        for path in source.files:
            if path.startswith('.github/workflows/') and re.search(r'^\s+services\s*:', _text(source, path), re.M):
                raise PolicyRejected('CI-declared services require an isolated service lifecycle')
        language = config.get('language') or repository_language(source)
        if language not in MANIFESTS:
            raise PolicyRejected('unsupported command toolchain: ' + str(language))
        result = {'node': _node, 'rust': _rust, 'go': _go}[language](source)
        requirements = config.get('system_packages', [])
        if (not isinstance(requirements, list) or len(requirements) > 32 or any(not isinstance(value, str)
                or not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*(?:=[0-9][A-Za-z0-9.+:~_-]*)?', value) for value in requirements)):
            raise PolicyRejected('runtime system packages must be bounded Debian package declarations')
        result['system_requirements'] = tuple(requirements)
        from .services import LocalService
        from feature_rl.artifacts import canonical_json
        services = config.get('services', [])
        if not isinstance(services, list) or len(services) > 4:
            raise PolicyRejected('at most four same-container local services are supported')
        parsed = []
        for value in services:
            if not isinstance(value, dict) or not isinstance(value.get('readiness'), list):
                raise PolicyRejected('local service readiness must be a concrete argv array')
            settings = {**value, 'readiness': {'argv': value['readiness'],
                'working_directory': '/workspace/services/' + value['name'], 'timeout_seconds': 5.0}}
            parsed.append(LocalService.model_validate_json(canonical_json(settings)))
        if len({service.name for service in parsed}) != len(parsed):
            raise PolicyRejected('duplicate local service name')
        result['services'] = tuple(parsed)
        if 'build' in config:
            value = config['build']
            if not isinstance(value, list) or not value or len(value) > 16 or any(
                    not isinstance(argv, list) or not argv or any(not isinstance(arg, str) or not arg or '\x00' in arg for arg in argv)
                    for argv in value):
                raise PolicyRejected('runtime build must contain bounded argv arrays')
            result['build_commands'] = [_command(argv) for argv in value]
        if 'entry_points' in config:
            result['entry_points'] = config['entry_points']
        entries = result['entry_points']
        if not isinstance(entries, list) or not entries or len(entries) > 64:
            raise PolicyRejected('runtime requires bounded concrete entry point paths')
        result['entry_points'] = tuple(dict.fromkeys(safe_path(path.removeprefix('./')) for path in entries))
        manifests = set(result.pop('manifests')) | {CONFIG, *SERVICE_FILES}
        result['manifest_hashes'] = {path: hashlib.sha256(source.files[path].data).hexdigest() if path in source.files else None
                                     for path in sorted(manifests)}
        result['manifest_paths'] = tuple(path for path in sorted(manifests) if path in source.files)
        result['manifest_path'] = MANIFESTS[language]
        result['source_roots'] = tuple(sorted({path.split('/')[0] for path in source.files}))
        result['build_commands'] = tuple(result['build_commands'])
        return result
    except PolicyRejected:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
        raise PolicyRejected('malformed or unsupported toolchain declarations: ' + str(exc)) from exc

"""Bounded, inert repository metadata inference for runtime construction.

No project module, setup script, build hook, or shell command is executed here.
Values which cannot be explained by retained source bytes are rejected; the
isolated wheel build subsequently checks the inferred metadata against reality.
"""
import ast
import configparser
import fnmatch
import hashlib
import json
import posixpath
import re
import shlex
import tomllib

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from .archive import SourceArchive, safe_path
from .models import PolicyRejected, SourceRejected


_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_IMPORT = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\Z", re.ASCII)
_VERSION = re.compile(r"[0-9][A-Za-z0-9.!+_-]*\Z")
_APT = re.compile(r"[a-z0-9][a-z0-9+.-]*(?:=[0-9][A-Za-z0-9.+:~_-]*)?\Z")
_PROTECTED = {'test', 'tests', 'build', 'dist', '.venv', '.pytest_cache',
              'docs', 'doc', 'examples', 'scripts', 'tools', 'benchmarks',
              'benchmark', 'venv', 'env', 'site-packages', 'node_modules'}
_FORTRAN = ('.f', '.for', '.f77', '.f90', '.f95', '.f03', '.f08', '.fpp')
_NATIVE = ('.c', '.cc', '.cpp', '.cxx', '.m', '.mm', '.rs', '.pyx', *_FORTRAN)


def _reject(message):
    raise PolicyRejected('repository inference: '+message)


def _strings(value, label):
    if not isinstance(value, (list, tuple)) or any(not isinstance(x, str) for x in value):
        _reject(label+' must be a static list of strings')
    return list(value)


def _table(value, label):
    if not isinstance(value, dict):
        _reject(label+' must be a table')
    return value


def _path(value, parent=''):
    if not isinstance(value, str) or value.startswith('/') or '\\' in value:
        _reject('non-repository metadata path: '+repr(value))
    path = posixpath.normpath(posixpath.join(parent, value))
    try:
        if safe_path(path) != path:
            _reject('noncanonical metadata path: '+path)
    except SourceRejected as exc:
        _reject('unsafe metadata path: '+str(exc))
    return path


class _Repository:
    def __init__(self, source):
        self.files = source.files
        self.evidence = {}
        self.hashes = {}
        self.read_bytes = 0
        self.read_count = 0

    def text(self, path):
        path = _path(path)
        entry = self.files.get(path)
        if entry is None:
            _reject('referenced metadata file is missing: '+path)
        self.read_bytes += len(entry.data)
        self.read_count += 1
        if (len(entry.data) > 1024*1024 or self.read_bytes > 16*1024*1024 or self.read_count > 1024
                or len(self.evidence) >= 256 and path not in self.evidence):
            _reject('metadata parsing byte/file limit: '+path)
        self.evidence[path] = hashlib.sha256(entry.data).hexdigest()
        try:
            return entry.data.decode('utf-8-sig')
        except UnicodeError:
            _reject('metadata is not UTF-8: '+path)

    def toml(self, path):
        try:
            return tomllib.loads(self.text(path))
        except (tomllib.TOMLDecodeError, RecursionError) as exc:
            _reject('invalid TOML in '+path+': '+str(exc))

    def requirement(self, value, label, *, hashes=()):
        if not isinstance(value, str):
            _reject(label+' contains a non-string dependency')
        value = value.strip()
        if not value or value.startswith('#'):
            return None
        # Parent construction uses the target interpreter's packaging parser.
        # Reject transports and pip options here before they can reach pip.
        if ('@' in value or '://' in value or '${' in value or '\x00' in value
                or '\n' in value or '\r' in value or value.startswith(('-', '.', '/'))):
            _reject(label+' contains an unsupported URL/local/option dependency: '+value)
        match = re.match(r'^([A-Za-z0-9][A-Za-z0-9._-]*)(?=\s|\[|[<>=!~;(]|$)', value)
        if not match:
            _reject(label+' contains an uninterpretable dependency: '+value)
        if '--' in value or '`' in value or '$' in value:
            _reject(label+' contains unsupported dependency syntax: '+value)
        try:
            parsed = Requirement(value)
        except InvalidRequirement as exc:
            _reject(label+' contains an invalid dependency: '+str(exc))
        if parsed.url:
            _reject(label+' contains an unsupported direct dependency URL')
        name = re.sub(r'[-_.]+', '-', match[1]).lower()
        if hashes:
            if not re.search(r'==\s*[^\s,;*]+', value):
                _reject(label+' has hashes without an exact version: '+value)
            values = self.hashes.setdefault(name, set())
            for digest in hashes:
                if not re.fullmatch(r'[0-9a-fA-F]{64}', digest):
                    _reject(label+' contains a non-SHA256 dependency hash')
                values.add(digest.lower())
        return value

    def requirements(self, path, *, constraint=False, stack=()):
        if path in stack or len(stack) >= 16:
            _reject('cyclic/deep requirement include: '+' -> '.join((*stack, path)))
        requirements, constraints = [], []
        content = re.sub(r'\\\s*\n', ' ', self.text(path))
        for number, original in enumerate(content.splitlines(), 1):
            line = re.split(r'\s+#', original, maxsplit=1)[0].strip()
            if not line or line.startswith('#'):
                continue
            label = f'{path}:{number}'
            include = re.fullmatch(r'(?:-r\s*|--requirement(?:=|\s+))(.+)', line)
            include_constraint = re.fullmatch(r'(?:-c\s*|--constraint(?:=|\s+))(.+)', line)
            if include or include_constraint:
                raw = (include or include_constraint)[1].strip().strip('"\'')
                target = _path(raw, posixpath.dirname(path))
                reqs, cons = self.requirements(target, constraint=constraint or bool(include_constraint),
                                               stack=(*stack, path))
                requirements.extend(reqs)
                constraints.extend(cons)
                continue
            hashes = re.findall(r'\s+--hash(?:=|\s+)sha256:([0-9a-fA-F]{64})(?=\s|$)', line)
            line = re.sub(r'\s+--hash(?:=|\s+)sha256:[0-9a-fA-F]{64}(?=\s|$)', '', line)
            value = self.requirement(line, label, hashes=hashes)
            if value:
                (constraints if constraint else requirements).append(value)
        return requirements, constraints

    def attribute(self, attribute, label):
        if not isinstance(attribute, str) or not _IMPORT.fullmatch(attribute) or '.' not in attribute:
            _reject(label+' has an invalid version attribute')
        module, name = attribute.rsplit('.', 1)
        suffixes = (module.replace('.', '/')+'.py', module.replace('.', '/')+'/__init__.py')
        candidates = [path for path in self.files if any(path == suffix or path.endswith('/'+suffix)
                                                       for suffix in suffixes)
                      and not any(part in {'.venv', '.pytest_cache', 'venv', 'env', 'node_modules'}
                                  for part in path.split('/'))]
        if len(candidates) != 1:
            _reject(label+' version attribute source is missing or ambiguous: '+attribute)
        return self.version_file(candidates[0], name)

    def version_file(self, path, name=None, pattern=None):
        text = self.text(_path(path))
        if pattern is not None:
            if len(pattern) > 256:
                _reject('dynamic version regex exceeds limit: '+path)
            # Arbitrary repository regexes can consume unbounded controller CPU.
            _reject('custom dynamic version regex requires unsupported evaluation: '+path)
        if name is None and _VERSION.fullmatch(text.strip()):
            return text.strip()
        try:
            tree = ast.parse(text, filename=path)
        except (SyntaxError, RecursionError):
            _reject('version file is not a static version or Python assignment: '+path)
        wanted = {name} if name else {'__version__', 'VERSION', 'version'}
        values = []
        env = {}
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                if any(isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del))
                       and child.id in wanted for child in ast.walk(node)):
                    _reject('version assignment occurs under unresolved control flow in '+path)
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            try:
                value = _literal(node.value, env)
            except ValueError:
                for target in targets:
                    if isinstance(target, ast.Name):
                        env.pop(target.id, None)
                        if target.id in wanted:
                            _reject('version assignment is dynamic in '+path)
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    env[target.id] = value
                    if target.id in wanted:
                        values.append(value)
        if len(values) != 1 or not isinstance(values[0], str):
            _reject('version assignment is unresolved or ambiguous in '+path)
        return values[0]


def _literal(node, env):
    """Small expression evaluator, deliberately without Python eval or imports."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in env:
        return env[node.id]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [_literal(value, env) for value in node.elts]
    if isinstance(node, ast.Dict):
        result = {}
        for key, value in zip(node.keys, node.values):
            if key is None:
                result.update(_literal(value, env))
            else:
                result[_literal(key, env)] = _literal(value, env)
        return result
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal(node.left, env), _literal(node.right, env)
        if isinstance(left, (str, list)) and type(left) is type(right):
            if len(left if isinstance(left, str) else repr(left)) + len(right if isinstance(right, str) else repr(right)) > 1024*1024:
                raise ValueError('static expression exceeds size limit')
            return left+right
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'dict':
        if node.args:
            raise ValueError('dynamic dict argument')
        result = {}
        for item in node.keywords:
            value = _literal(item.value, env)
            if item.arg is None:
                if not isinstance(value, dict):
                    raise ValueError('dynamic dict keyword expansion')
                result.update(value)
            else:
                result[item.arg] = value
        return result
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        value = _literal(node.func.value, env)
        args = [_literal(arg, env) for arg in node.args]
        if not node.keywords and isinstance(value, str):
            if node.func.attr == 'strip' and not args:
                return value.strip()
            if node.func.attr == 'splitlines' and not args:
                return value.splitlines()
            if node.func.attr == 'split' and len(args) <= 1 and all(isinstance(x, str) for x in args):
                return value.split(*args)
            if node.func.attr == 'join' and len(args) == 1 and isinstance(args[0], list):
                if (any(not isinstance(item, str) for item in args[0])
                        or sum(len(item)+len(value) for item in args[0]) > 1024*1024):
                    raise ValueError('static string join exceeds size limit')
                return value.join(args[0])
    raise ValueError('expression is not static')


def _setup_py(repo):
    path = 'setup.py'
    try:
        tree = ast.parse(repo.text(path), filename=path)
    except (SyntaxError, RecursionError):
        _reject('setup.py cannot be parsed statically')
    env, values, calls = {}, {}, []
    aliases = {'setup'}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in {'setuptools', 'distutils.core'}:
            aliases.update(item.asname or item.name for item in node.names if item.name == 'setup')

    def evaluate(node):
        # Common inert requirements/version file reads, never arbitrary methods.
        if isinstance(node, (ast.List, ast.Tuple)):
            return [evaluate(item) for item in node.elts]
        if isinstance(node, ast.Dict):
            result = {}
            for key, item in zip(node.keys, node.values):
                value = evaluate(item)
                if key is None:
                    if not isinstance(value, dict):
                        raise ValueError('dynamic dictionary expansion')
                    result.update(value)
                else:
                    result[evaluate(key)] = value
            return result
        if isinstance(node, ast.Call):
            function = node.func.id if isinstance(node.func, ast.Name) else (
                node.func.attr if isinstance(node.func, ast.Attribute) else '')
            if function == 'Extension':
                options = {item.arg: item.value for item in node.keywords}
                if None in options or len(node.args) > 2:
                    raise ValueError('dynamic extension declaration')
                name = evaluate(node.args[0]) if node.args else evaluate(options['name'])
                sources = evaluate(node.args[1]) if len(node.args) > 1 else evaluate(options['sources'])
                return {'name': name, 'sources': sources}
            if function == 'cythonize' and len(node.args) == 1:
                extensions = evaluate(node.args[0])
                if not isinstance(extensions, list) or any(not isinstance(item, dict) for item in extensions):
                    raise ValueError('cythonize needs static Extension declarations')
                return extensions
            if function == 'dict' and not node.args:
                result = {}
                for item in node.keywords:
                    value = evaluate(item.value)
                    if item.arg is None:
                        if not isinstance(value, dict):
                            raise ValueError('dynamic dictionary expansion')
                        result.update(value)
                    else:
                        result[item.arg] = value
                return result
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {'read', 'read_text'} and not node.args:
                base = node.func.value
                if (isinstance(base, ast.Call) and isinstance(base.func, ast.Name)
                        and base.func.id in {'open', 'Path'} and len(base.args) in {1, 2}):
                    filename = _literal(base.args[0], env)
                    if len(base.args) == 2 and _literal(base.args[1], env) not in {'r', 'rt'}:
                        raise ValueError('non-read metadata file')
                    return repo.text(_path(filename))
            if node.func.attr in {'strip', 'splitlines', 'split'}:
                base = evaluate(node.func.value)
                surrogate = ast.Call(func=ast.Attribute(value=ast.Constant(base), attr=node.func.attr),
                                     args=node.args, keywords=node.keywords)
                return _literal(surrogate, env)
        return _literal(node, env)

    def visit(nodes, conditional=False):
        for node in nodes:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(not isinstance(target, ast.Name) for target in targets):
                    env.clear()
                for target in targets:
                    if isinstance(target, ast.Name):
                        env.pop(target.id, None)
                        if not conditional:
                            try:
                                env[target.id] = evaluate(node.value)
                            except (ValueError, TypeError, KeyError):
                                pass
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                call = node.value
                is_setup = ((isinstance(call.func, ast.Name) and call.func.id in aliases)
                            or (isinstance(call.func, ast.Attribute) and call.func.attr == 'setup'
                                and isinstance(call.func.value, ast.Name)
                                and call.func.value.id in {'setuptools', 'distutils'}))
                if is_setup:
                    if conditional:
                        _reject('setup.py invokes setup under an unresolved condition')
                    calls.append((call, dict(env)))
                else:
                    # A call may mutate a previously defined requirements list
                    # or kwargs dict. Do not retain that value as static.
                    env.clear()
            if isinstance(node, (ast.AugAssign, ast.Delete, ast.Assert, ast.ClassDef)):
                env.clear()
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for item in node.names:
                    env.pop(item.asname or item.name.split('.')[0], None)
            if isinstance(node, ast.If):
                main = (isinstance(node.test, ast.Compare) and isinstance(node.test.left, ast.Name)
                        and node.test.left.id == '__name__' and len(node.test.ops) == 1
                        and isinstance(node.test.ops[0], ast.Eq) and len(node.test.comparators) == 1
                        and isinstance(node.test.comparators[0], ast.Constant)
                        and node.test.comparators[0].value == '__main__')
                visit(node.body, conditional=conditional or not main)
                visit(node.orelse, conditional=True)
            elif isinstance(node, (ast.For, ast.While, ast.Try, ast.With, ast.FunctionDef, ast.AsyncFunctionDef)):
                # Unknown control flow may mutate setup arguments; its assigned
                # names must not retain an earlier apparently static value.
                for child in ast.walk(node):
                    if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                        env.pop(child.id, None)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    env.pop(node.name, None)
                if any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                       and child.func.id in aliases for child in ast.walk(node)):
                    _reject('setup.py setup call is hidden in unsupported control flow')
    visit(tree.body)
    if len(calls) != 1:
        _reject('setup.py must contain one statically reachable setup call')
    call, captured = calls[0]
    env.clear()
    env.update(captured)
    if call.args:
        _reject('setup.py positional setup arguments are unsupported')
    relevant = {'name', 'version', 'python_requires', 'install_requires', 'setup_requires',
                'package_dir', 'packages', 'py_modules', 'entry_points', 'ext_modules'}
    for keyword in call.keywords:
        if keyword.arg is None:
            try:
                expanded = evaluate(keyword.value)
            except (ValueError, TypeError, KeyError):
                _reject('setup.py **kwargs are not statically resolvable')
            if not isinstance(expanded, dict):
                _reject('setup.py **kwargs must be a static dict')
            values.update({key: value for key, value in expanded.items() if key in relevant})
        elif keyword.arg in relevant:
            if keyword.arg == 'packages' and isinstance(keyword.value, ast.Call):
                finder = keyword.value
                name = finder.func.id if isinstance(finder.func, ast.Name) else (
                    finder.func.attr if isinstance(finder.func, ast.Attribute) else '')
                if name in {'find_packages', 'find_namespace_packages'}:
                    if len(finder.args) > 1 or any(k.arg not in {'where', 'include', 'exclude'} for k in finder.keywords):
                        _reject('setup.py package finder options are unsupported')
                    try:
                        options = {k.arg: evaluate(k.value) for k in finder.keywords}
                        if finder.args:
                            options['where'] = evaluate(finder.args[0])
                    except (ValueError, TypeError, KeyError):
                        _reject('setup.py package finder is dynamic')
                    values['package_find'] = options
                    continue
            try:
                values[keyword.arg] = evaluate(keyword.value)
            except (ValueError, TypeError, KeyError):
                _reject('setup.py '+keyword.arg+' is not statically resolvable')
    return values


def _setup_cfg(repo):
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(repo.text('setup.cfg'))
    except configparser.Error as exc:
        _reject('invalid setup.cfg: '+str(exc))
    result = {}
    for key in ('name', 'version'):
        if parser.has_option('metadata', key):
            result[key] = parser.get('metadata', key).strip()
    if 'version' in result:
        if result['version'].startswith('attr:'):
            result['version'] = repo.attribute(result['version'][5:].strip(), 'setup.cfg')
        elif result['version'].startswith('file:'):
            result['version'] = repo.version_file(result['version'][5:].strip())
    for key in ('python_requires', 'install_requires', 'setup_requires', 'py_modules', 'packages'):
        if parser.has_option('options', key):
            value = parser.get('options', key).strip()
            if key == 'python_requires':
                result[key] = value
            elif key == 'packages' and value in {'find:', 'find_namespace:'}:
                result['package_find'] = {}
            else:
                if value.startswith('file:'):
                    paths = [p.strip() for p in value[5:].split(',')]
                    if key not in {'install_requires', 'setup_requires'}:
                        _reject('setup.cfg file directive is unsupported for '+key)
                    result[key] = []
                    for path in paths:
                        reqs, cons = repo.requirements(_path(path))
                        if cons:
                            _reject('setup.cfg metadata requirements contain pip constraints: '+path)
                        result[key].extend(reqs)
                else:
                    result[key] = [line.strip() for line in value.splitlines() if line.strip()]
    if parser.has_option('options', 'package_dir'):
        result['package_dir'] = {}
        for line in parser.get('options', 'package_dir').splitlines():
            if not line.strip():
                continue
            key, separator, value = line.partition('=')
            if not separator:
                _reject('setup.cfg package_dir is not a static mapping')
            result['package_dir'][key.strip()] = value.strip()
    if parser.has_section('options.packages.find'):
        result['package_find'] = {key: value.strip() if key == 'where' else value.split()
                                  for key, value in parser.items('options.packages.find')}
    if parser.has_section('options.entry_points'):
        result['entry_points'] = {key: value.splitlines()
                                  for key, value in parser.items('options.entry_points')}
    return result


def _poetry_spec(value, label):
    if not isinstance(value, str):
        _reject(label+' must be a static version constraint')
    value = value.strip()
    if value in {'', '*'}:
        return ''
    if '||' in value or ' - ' in value:
        _reject(label+' uses an unsupported union/range constraint: '+value)
    result = []
    for token in re.split(r'\s*,\s*|\s+(?=[<>=!~^])', value):
        if token.startswith(('^', '~')) and not token.startswith('~='):
            match = re.fullmatch(r'([\^~])(\d+)(?:\.(\d+))?(?:\.(\d+))?', token)
            if not match:
                _reject(label+' has an unsupported compatible version: '+token)
            parts = [int(x) for x in match.groups()[1:] if x is not None]
            upper = parts+[0]*(3-len(parts))
            index = (next((i for i, n in enumerate(parts) if n), len(parts)-1)
                     if match[1] == '^' else min(1, len(parts)-1))
            upper[index] += 1
            upper[index+1:] = [0]*(2-index)
            result.extend(('>='+'.'.join(map(str, parts)), '<'+'.'.join(map(str, upper))))
        elif token.startswith(('<', '>', '=', '!', '~')):
            result.append(token if not token.startswith('=') or token.startswith('==') else '='+token)
        elif re.fullmatch(r'\d+(?:\.(?:\d+|\*))*[A-Za-z0-9.+_-]*', token):
            result.append('=='+token)
        else:
            _reject(label+' has an unsupported version constraint: '+value)
    return ','.join(result)


def _poetry_dependencies(repo, table):
    requirements, python = [], ''
    for name, value in _table(table, 'tool.poetry.dependencies').items():
        if name.lower() == 'python':
            python = _poetry_spec(value, 'tool.poetry.dependencies.python')
            continue
        if not _NAME.fullmatch(name):
            _reject('invalid Poetry dependency name: '+name)
        if isinstance(value, str):
            requirement = name+_poetry_spec(value, 'Poetry dependency '+name)
        elif isinstance(value, dict):
            unsupported = set(value)-{'version', 'optional', 'extras', 'markers', 'python', 'platform', 'allow-prereleases'}
            if unsupported:
                _reject('Poetry dependency '+name+' uses unsupported fields: '+', '.join(sorted(unsupported)))
            if value.get('optional') is True:
                continue
            if value.get('allow-prereleases'):
                _reject('Poetry allow-prereleases requires an explicit prerelease version: '+name)
            extras = _strings(value.get('extras', []), 'Poetry dependency extras')
            if any(not _NAME.fullmatch(extra) for extra in extras):
                _reject('invalid Poetry extra: '+name)
            requirement = name+('['+','.join(extras)+']' if extras else '')
            requirement += _poetry_spec(value.get('version', '*'), 'Poetry dependency '+name)
            markers = []
            if 'markers' in value:
                if not isinstance(value['markers'], str):
                    _reject('Poetry markers must be a string: '+name)
                markers.append('('+value['markers']+')')
            if 'python' in value:
                for spec in _poetry_spec(value['python'], 'Poetry Python marker').split(','):
                    match = re.fullmatch(r'(<=|>=|==|!=|~=|<|>)(.+)', spec)
                    if not match or match[1] == '~=':
                        _reject('unsupported Poetry Python marker: '+str(value['python']))
                    field = 'python_full_version' if match[2].count('.') >= 2 else 'python_version'
                    markers.append(field+' '+match[1]+' "'+match[2]+'"')
            if 'platform' in value:
                platform = value['platform']
                if platform not in {'linux', 'win32', 'darwin'}:
                    _reject('unsupported Poetry platform marker: '+str(platform))
                markers.append('sys_platform == "'+platform+'"')
            if markers:
                requirement += '; '+' and '.join(markers)
        else:
            _reject('Poetry dependency '+name+' has multiple/dynamic alternatives')
        requirements.append(repo.requirement(requirement, 'tool.poetry.dependencies'))
    return requirements, python


def _locks(repo, project_name):
    constraints = []
    normalized_project = re.sub(r'[-_.]+', '-', project_name).lower()
    versions = {}

    def add(name, version, markers, hashes, label):
        if not isinstance(name, str) or not _NAME.fullmatch(name) or not isinstance(version, str):
            _reject(label+' has an invalid locked package identity')
        normalized = re.sub(r'[-_.]+', '-', name).lower()
        if normalized == normalized_project:
            return
        try:
            Version(version)
        except InvalidVersion:
            _reject(label+' has an invalid locked version: '+version)
        if not isinstance(markers, str):
            _reject(label+' has a non-string resolution marker')
        previous = versions.setdefault(normalized, [])
        if any(old_version != version and (not markers or not old_markers)
               for old_version, old_markers in previous):
            _reject(label+' has multiple locked versions without disjoint resolution markers: '+name)
        previous.append((version, markers))
        value = name+'=='+version+('; '+markers if markers else '')
        constraints.append(repo.requirement(value, label, hashes=hashes))

    for path in ('uv.lock', 'poetry.lock'):
        if path not in repo.files:
            continue
        lock = repo.toml(path)
        packages = lock.get('package')
        if not isinstance(packages, list):
            _reject(path+' has an invalid package roster')
        for item in packages:
            item = _table(item, path+' package')
            name = item.get('name')
            source = _table(item.get('source', {}), path+' source')
            normalized = re.sub(r'[-_.]+', '-', name).lower() if isinstance(name, str) else ''
            if normalized == normalized_project:
                continue
            if path == 'uv.lock':
                if set(source)-{'registry'}:
                    _reject('uv.lock contains a non-registry dependency: '+str(name))
                registry = source.get('registry', 'https://pypi.org/simple')
                if registry.rstrip('/') not in {'https://pypi.org/simple', 'https://pypi.python.org/simple'}:
                    _reject('uv.lock requires an unsupported package registry: '+str(registry))
                markers = item.get('resolution-markers', [])
                markers = _strings(markers, path+' resolution-markers')
                marker = ' or '.join('('+value+')' for value in markers)
                files = item.get('wheels', [])
                if not isinstance(files, list):
                    _reject(path+' wheels must be a list')
                if item.get('sdist'):
                    files = [*files, item['sdist']]
            else:
                if source:
                    _reject('poetry.lock requires a non-default dependency source: '+str(name))
                marker = item.get('markers', '')
                if isinstance(marker, dict):
                    # Group-specific selectors cannot safely become one global
                    # pip constraint without knowing which groups are active.
                    _reject('poetry.lock has group-specific dependency markers: '+str(name))
                files = item.get('files', lock.get('metadata', {}).get('files', {}).get(name, []))
                if not isinstance(files, list):
                    _reject(path+' files must be a list')
            hashes = []
            for file in files:
                digest = _table(file, path+' artifact').get('hash')
                if digest is not None:
                    if not isinstance(digest, str) or not digest.startswith('sha256:'):
                        _reject(path+' contains an unsupported artifact hash')
                    hashes.append(digest[7:])
            add(name, item.get('version'), marker, hashes, path)
    if 'Pipfile.lock' in repo.files:
        try:
            lock = json.loads(repo.text('Pipfile.lock'))
        except (ValueError, RecursionError):
            _reject('Pipfile.lock is invalid JSON')
        lock = _table(lock, 'Pipfile.lock')
        sources = lock.get('_meta', {}).get('sources', [])
        indices = set()
        for source in sources:
            source = _table(source, 'Pipfile.lock source')
            if source.get('url', '').rstrip('/') not in {'https://pypi.org/simple', 'https://pypi.python.org/simple'}:
                _reject('Pipfile.lock requires an unsupported package index')
            indices.add(source.get('name'))
        for name, item in _table(lock.get('default', {}), 'Pipfile.lock default').items():
            item = _table(item, 'Pipfile.lock dependency')
            if set(item)-{'version', 'markers', 'hashes', 'index', 'extras'}:
                _reject('Pipfile.lock contains a non-registry dependency: '+name)
            if 'index' in item and item['index'] not in indices:
                _reject('Pipfile.lock references an unresolved package index: '+name)
            version = item.get('version', '')
            if not isinstance(version, str) or not version.startswith('=='):
                _reject('Pipfile.lock does not pin an exact dependency version: '+name)
            hashes = _strings(item.get('hashes', []), 'Pipfile.lock hashes')
            if any(not digest.startswith('sha256:') for digest in hashes):
                _reject('Pipfile.lock contains an unsupported hash')
            add(name, version[2:], item.get('markers', ''), [h[7:] for h in hashes], 'Pipfile.lock')
    return constraints


def _python_selectors(repo):
    explicit, exact, alternatives = [], [], []
    for path in ('.python-version', 'runtime.txt'):
        if path not in repo.files:
            continue
        initial = len(explicit)
        for line in repo.text(path).splitlines():
            line = line.split('#', 1)[0].strip()
            if not line:
                continue
            value = line.removeprefix('python-')
            if not re.fullmatch(r'3\.\d+(?:\.\d+)?', value):
                _reject(path+' has an unresolved/non-CPython runtime selector: '+line)
            explicit.append((path, value))
        if len(explicit) == initial:
            _reject(path+' has no explicit Python runtime selector')
    if 'Pipfile' in repo.files:
        requires = _table(repo.toml('Pipfile').get('requires', {}), 'Pipfile requires')
        for key in ('python_full_version', 'python_version'):
            if key in requires:
                explicit.append(('Pipfile '+key, requires[key]))
    if 'Pipfile.lock' in repo.files:
        try:
            lock = json.loads(repo.text('Pipfile.lock'))
        except (ValueError, RecursionError):
            _reject('Pipfile.lock is invalid JSON')
        requires = _table(lock.get('_meta', {}).get('requires', {}), 'Pipfile.lock requires')
        for key in ('python_full_version', 'python_version'):
            if key in requires:
                explicit.append(('Pipfile.lock '+key, requires[key]))
    preferred = None
    preferred_label = None
    for label, value in explicit:
        if not isinstance(value, str) or not re.fullmatch(r'3\.\d+(?:\.\d+)?', value):
            _reject(label+' has an unresolved/non-CPython runtime selector: '+str(value))
        if preferred is not None and not (value == preferred or value.startswith(preferred+'.')
                                          or preferred.startswith(value+'.')):
            _reject('contradictory explicit Python selectors: '+preferred_label+'='+preferred+', '+label+'='+value)
        if preferred is None or len(value.split('.')) > len(preferred.split('.')):
            preferred, preferred_label = value, label
    if preferred is not None:
        return [preferred]
    # Deliberately parse a small literal YAML surface, not arbitrary CI code.
    for path in sorted(repo.files):
        if not (path.startswith('.github/workflows/') and path.endswith(('.yml', '.yaml'))
                or path in {'.travis.yml', '.gitlab-ci.yml', 'azure-pipelines.yml'}):
            continue
        text = _linux_ci_text(repo.text(path))
        for match in re.finditer(r'(?m)^([ \t]*)(?:-\s*)?(?:python-version|python_version|python):\s*([^\n#]*)', text):
            raw = match[2].strip()
            candidates = []
            if raw.startswith('[') and raw.endswith(']'):
                candidates = raw[1:-1].split(',')
            elif not raw:
                for following in text[match.end():].splitlines():
                    if not following.strip():
                        continue
                    indentation = len(following)-len(following.lstrip())
                    if indentation <= len(match[1]):
                        break
                    item = re.match(r'\s*-\s*([^#]+)', following)
                    if item:
                        candidates.append(item[1])
            else:
                candidates = [raw]
            for candidate in candidates:
                value = candidate.strip().strip('"\'')
                if re.fullmatch(r'3\.\d+(?:\.\d+)?', value):
                    (exact if value.count('.') == 2 else alternatives).append(value)
    return list(dict.fromkeys((*exact, *alternatives)))


def _linux_ci_text(content):
    """Discard only CI blocks whose literal runner/condition excludes Linux.

    This is an indentation-bounded projection, not a YAML/shell interpreter.
    Unknown runner expressions and conditions remain subject to strict package
    parsing rather than being assumed irrelevant.
    """
    lines = content.splitlines()
    significant = [index for index, line in enumerate(lines)
                   if line.strip() and not line.lstrip().startswith('#')]
    indents = {index: len(lines[index])-len(lines[index].lstrip()) for index in significant}
    excluded = set()
    parents, ends, stack = {}, {}, []
    for index in significant:
        while stack and indents[stack[-1]] >= indents[index]:
            ends[stack.pop()] = index
        parents[index] = stack[-1] if stack else None
        stack.append(index)
    for index in stack:
        ends[index] = len(lines)
    matrix_scopes = {}

    def block(index, *, parent=True, levels=1):
        start = index
        if parent:
            for _ in range(levels):
                ancestor = parents[start]
                if ancestor is None:
                    return 0, len(lines)
                start = ancestor
        return start, ends[start]

    def non_linux(value):
        value = value.strip().strip('"\'').lower()
        if any(token in value for token in ('${', 'matrix.', 'runner.', 'parameters.', 'variables.')):
            return False
        tokens = re.findall(r'[a-z0-9_-]+', value)
        linux = any(token.startswith(('linux', 'ubuntu', 'debian')) for token in tokens)
        other = any(token.startswith(('macos', 'osx', 'windows', 'win32')) for token in tokens)
        return other and not linux

    def false_on_linux(value):
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1].strip()
        if value.startswith('${{') and value.endswith('}}'):
            value = value[3:-2].strip()
        if '||' in value:
            return all(false_on_linux(clause) for clause in value.split('||'))
        if re.search(r'\bor\b', value):
            return False
        # A false conjunction excludes Linux regardless of unknown conjuncts.
        for clause in re.split(r'\s*&&\s*', value):
            clause = clause.strip()
            function = re.fullmatch(r"(?:startsWith|contains)\(\s*(?:runner\.os|matrix\.os)\s*,\s*(['\"])([^'\"]+)\1\s*\)", clause)
            if function and non_linux(function[2]):
                return True
            clause = clause.strip('() ')
            match = re.fullmatch(r"(?:runner\.os|matrix\.os)\s*(==|!=)\s*(['\"])([^'\"]+)\2", clause)
            if match and ((match[1] == '==' and non_linux(match[3]))
                          or (match[1] == '!=' and match[3].lower() == 'linux')):
                return True
        return False

    for index in significant:
        if index in excluded:
            continue
        match = re.match(r'\s*(?:-\s*)?([A-Za-z_][\w.-]*)\s*:\s*(.*?)\s*$', lines[index])
        if not match:
            continue
        key, value = match.groups()
        if key not in {'runs-on', 'vmImage', 'os', 'if'}:
            continue
        value = re.split(r'\s+#', value, maxsplit=1)[0].strip()
        start, end = block(index, parent=not lines[index].lstrip().startswith('- '),
                           levels=2 if key == 'vmImage' else 1)
        irrelevant = (key in {'runs-on', 'vmImage', 'os'} and non_linux(value)
                      or key == 'if' and false_on_linux(value))
        if key == 'runs-on' and re.fullmatch(r'\$\{\{\s*matrix\.os\s*\}\}', value.strip('"\'')):
            # A matrix containing only macOS/Windows is equally unambiguous.
            if (start, end) not in matrix_scopes:
                selectors = [re.match(r'\s*os:\s*(\[[^\]]+\])\s*$', lines[candidate])
                             for candidate in range(start, end)]
                selectors = [match[1] for match in selectors if match]
                matrix_scopes[start, end] = len(selectors) == 1 and non_linux(selectors[0])
            irrelevant = irrelevant or matrix_scopes[start, end]
        if irrelevant:
            excluded.update(range(start, end))
    return '\n'.join('' if index in excluded or line.lstrip().startswith('#') else line
                     for index, line in enumerate(lines))


def _system_requirements(repo, native, mappings, build_requirements):
    packages = ['build-essential', 'pkg-config'] if native else []
    native_paths = [path for path in repo.files if not any(part in _PROTECTED for part in path.split('/'))
                    or any(path == item['source'] or path.startswith(item['source']+'/') for item in mappings)]
    supplied_tools = {re.sub(r'[-_.]+', '-', requirement.name).lower()
                      for value in build_requirements if (requirement := Requirement(value)).marker is None}
    if any(posixpath.basename(path) == 'CMakeLists.txt' for path in native_paths) and 'cmake' not in supplied_tools:
        packages.append('cmake')
    if any(posixpath.basename(path) == 'meson.build' for path in native_paths):
        if 'meson' not in supplied_tools:
            packages.append('meson')
        if 'ninja' not in supplied_tools:
            packages.append('ninja-build')
    if any(path.lower().endswith('.rs') for path in native_paths):
        packages.extend(('cargo', 'rustc'))
        for path in native_paths:
            if posixpath.basename(path) in {'rust-toolchain', 'rust-toolchain.toml'}:
                repo.text(path)
                _reject('Rust toolchain override cannot be translated into Debian package pins: '+path)
            if posixpath.basename(path) == 'Cargo.toml':
                cargo = repo.toml(path)
                if (cargo.get('package', {}).get('rust-version') is not None
                        or cargo.get('workspace', {}).get('package', {}).get('rust-version') is not None):
                    _reject('Cargo rust-version cannot be translated into Debian rustc package pins: '+path)
    if any(path.lower().endswith(_FORTRAN) for path in native_paths):
        packages.append('gfortran')
    if any(path.lower().endswith(('.m', '.mm')) for path in native_paths):
        packages.append('gobjc')
    if any(path.lower().endswith('.mm') for path in native_paths):
        packages.append('gobjc++')
    paths = [path for path in repo.files if posixpath.basename(path).startswith('Dockerfile')
             or path.startswith('.github/workflows/') and path.endswith(('.yaml', '.yml'))
             or path in {'.gitlab-ci.yml', '.travis.yml', 'azure-pipelines.yml'}]
    for path in sorted(paths):
        content = repo.text(path)
        if not posixpath.basename(path).startswith('Dockerfile'):
            content = _linux_ci_text(content)
        else:
            content = '\n'.join(line for line in content.splitlines() if not line.lstrip().startswith('#'))
        # Strip only a complete YAML scalar's enclosing quotes. Shell quotes
        # inside that scalar are retained for shlex's strict token validation.
        content = re.sub(r'(?m)^\s*(?:-\s*)?(?:run|script|before_script):\s*([\'"])(.*?)\1\s*$',
                         lambda match: match[2], content)
        content = re.sub(r'(?m)^\s*-\s*([\'"])(.*?)\1\s*$', lambda match: match[2], content)
        content = re.sub(r'\\\s*\n', ' ', content)
        for match in re.finditer(r'\b(?:apt-get|apt)\s+([^\n;&|]+)', content):
            try:
                tokens = shlex.split(match[1], comments=True)
            except ValueError:
                _reject(path+' has an uninterpretable apt declaration')
            if 'install' not in tokens:
                continue
            before, after = tokens[:tokens.index('install')], tokens[tokens.index('install')+1:]
            if any(token not in {'-y', '-q', '-qq', '--yes', '--no-install-recommends', '--no-install-suggests'}
                   for token in before):
                _reject(path+' has unsupported apt install options')
            for token in after:
                if token in {'-y', '-q', '-qq', '--yes', '--no-install-recommends', '--no-install-suggests'}:
                    continue
                if not _APT.fullmatch(token):
                    _reject(path+' has a dynamic/unsupported apt package: '+token)
                packages.append(token)
        if re.search(r'\b(?:apk\s+add|yum\s+install|dnf\s+install|brew\s+install)\b', content):
            _reject(path+' declares non-Debian system packages requiring explicit translation')
    versions = {}
    for package in packages:
        name, _, version = package.partition('=')
        if name in versions and versions[name] and version and versions[name] != version:
            _reject('contradictory system package versions: '+name)
        versions[name] = version or versions.get(name, '')
    return [name+('='+version if version else '') for name, version in sorted(versions.items())]


def _source_layout(repo, project_name, project_version, tool, legacy, backend):
    mappings = []
    assets = []

    def add(path, wheel=None, *, extension=False):
        path = _path(path)
        wheel = _path(wheel or posixpath.basename(path))
        if any(part in {'.venv', '.pytest_cache'} for part in path.split('/')):
            _reject('declared source layout includes a protected path: '+path)
        if not any(name == path or name.startswith(path+'/') for name in repo.files):
            _reject('declared source mapping has no repository files: '+path)
        if not any(name.lower().endswith(('.py', *_NATIVE)) and (name == path or name.startswith(path+'/'))
                   for name in repo.files):
            _reject('declared source mapping has no Python/native source: '+path)
        mappings.append({'source': path, 'wheel': wheel})

    hatch = tool.get('hatch', {}).get('build', {})
    target = hatch.get('targets', {}).get('wheel', {})
    selected = target.get('packages', hatch.get('packages'))
    source_settings = target.get('sources', hatch.get('sources'))
    source_rewrites = []
    if source_settings is not None:
        if isinstance(source_settings, list):
            source_settings = {path: '' for path in _strings(source_settings, 'Hatch sources')}
        for original, destination in _table(source_settings, 'Hatch sources').items():
            if not isinstance(original, str) or not isinstance(destination, str):
                _reject('Hatch source rewrites require static path strings')
            original = '' if original in {'', '.'} else _path(original)
            destination = '' if destination in {'', '.'} else _path(destination)
            source_rewrites.append((original, destination))
        for original, _ in source_rewrites:
            if any(other != original and (not other or original.startswith(other+'/'))
                   for other, _ in source_rewrites):
                _reject('overlapping Hatch source rewrite prefixes are ambiguous')

    def hatch_destination(path, default):
        if source_settings is None:
            return default
        path = _path(path)
        for original, destination in source_rewrites:
            if not original or path == original or path.startswith(original+'/'):
                suffix = path[len(original):].lstrip('/')
                result = '/'.join(filter(None, (destination, suffix)))
                if not result:
                    _reject('Hatch source rewrite removes the entire import root: '+path)
                return _path(result)
            if original.startswith(path+'/'):
                _reject('Hatch source rewrite splits a selected package subtree: '+original)
        return path

    if selected is not None:
        for path in _strings(selected, 'Hatch wheel packages'):
            add(path, hatch_destination(path, posixpath.basename(path)))
    poetry = tool.get('poetry', {})
    if not mappings and 'packages' in poetry:
        if not isinstance(poetry['packages'], list):
            _reject('Poetry packages must be a static list')
        for item in poetry['packages']:
            item = _table(item, 'Poetry package')
            formats = item.get('format', ['wheel', 'sdist'])
            if isinstance(formats, str):
                formats = [formats]
            if 'wheel' not in formats:
                continue
            if set(item)-{'include', 'from', 'format', 'to'}:
                _reject('unsupported Poetry package mapping fields')
            include = item.get('include')
            if not isinstance(include, str) or any(char in include for char in '*?['):
                _reject('Poetry package glob requires an explicit source mapping')
            add(posixpath.join(item.get('from', ''), include), item.get('to', include))
    setuptools = _table(tool.get('setuptools', {}), 'tool.setuptools')
    package_dir = setuptools.get('package-dir', legacy.get('package_dir', {}))
    package_dir = _table(package_dir, 'package directory mapping')
    base = package_dir.get('', '')
    if not isinstance(base, str):
        _reject('package root is not a string')
    named_directories = {}
    for name, value in package_dir.items():
        if name == '':
            continue
        if not isinstance(name, str) or not _IMPORT.fullmatch(name):
            _reject('package-dir contains an invalid import namespace: '+str(name))
        if value in {'', '.'}:
            _reject('named package-dir cannot map the repository root as a package: '+name)
        named_directories[name] = _path(value)

    def package_source(name):
        prefixes = [prefix for prefix in named_directories if name == prefix or name.startswith(prefix+'.')]
        if not prefixes:
            return posixpath.join(base, name.replace('.', '/'))
        prefix = max(prefixes, key=len)
        suffix = name[len(prefix):].lstrip('.').replace('.', '/')
        return posixpath.join(named_directories[prefix], suffix) if suffix else named_directories[prefix]

    explicit = setuptools.get('packages', legacy.get('packages'))
    find = legacy.get('package_find', {})
    if isinstance(explicit, dict):
        if set(explicit) != {'find'}:
            _reject('unsupported setuptools package discovery configuration')
        find = _table(explicit['find'], 'setuptools package finder')
        explicit = None
    if not mappings and explicit is not None:
        for name in _strings(explicit, 'setuptools packages'):
            if not _IMPORT.fullmatch(name):
                _reject('invalid declared package: '+name)
            add(package_source(name), name.replace('.', '/'))
    if not mappings and explicit is None and named_directories:
        if find:
            _reject('named package-dir combined with a package finder requires explicit package names')
        for name in sorted(named_directories):
            add(package_source(name), name.replace('.', '/'))
    modules = setuptools.get('py-modules', legacy.get('py_modules', []))
    for name in _strings(modules, 'Python modules'):
        if not _IMPORT.fullmatch(name) or '.' in name:
            _reject('py_modules must contain top-level identifiers: '+name)
        add(posixpath.join(base, name+'.py'), name+'.py')
    maturin = _table(tool.get('maturin', {}), 'tool.maturin')
    maturin_module = None
    if backend == 'maturin' or maturin:
        cargo_path = _path(maturin.get('manifest-path', 'Cargo.toml'))
        cargo = repo.toml(cargo_path)
        package = _table(cargo.get('package', {}), 'Cargo package')
        library = _table(cargo.get('lib', {}), 'Cargo lib')
        if package.get('rust-version') is not None:
            _reject('Cargo rust-version cannot be translated into Debian rustc package pins: '+cargo_path)
        kinds = _strings(library.get('crate-type', []), 'Cargo crate types')
        if 'cdylib' not in kinds:
            _reject('maturin import mapping requires a declared Cargo cdylib')
        module = maturin.get('module-name', library.get('name', package.get('name')))
        if not isinstance(module, str):
            _reject('maturin module name is unresolved')
        module = module.replace('-', '_')
        if not _IMPORT.fullmatch(module):
            _reject('maturin module name is not a Python import: '+module)
        library_path = _path(library.get('path', 'src/lib.rs'), posixpath.dirname(cargo_path))
        if library_path not in repo.files or not library_path.lower().endswith('.rs'):
            _reject('maturin Cargo library source is absent or unresolved: '+library_path)
        maturin_module = (library_path, module.replace('.', '/'))
        python_source = maturin.get('python-source', '.' if '.' in module else None)
        if python_source is not None:
            python_source = '' if python_source in {'', '.'} else _path(python_source)
            package_path = posixpath.join(python_source, module.split('.')[0])
            if any(path.startswith(package_path+'/') and path.endswith('.py') for path in repo.files):
                add(package_path, module.split('.')[0])
            elif '.' in module:
                _reject('maturin python-source lacks its declared Python package: '+package_path)
    if not mappings:
        flit = tool.get('flit', {}).get('module', {}).get('name')
        if flit is not None:
            if not isinstance(flit, str) or not _IMPORT.fullmatch(flit):
                _reject('invalid Flit module name')
            candidates = [prefix+flit.replace('.', '/')+suffix for prefix in ('', 'src/')
                          for suffix in ('', '.py')]
            existing = [path for path in candidates if path in repo.files
                        or any(name.startswith(path+'/') for name in repo.files)]
            if len(existing) != 1:
                _reject('Flit module layout is missing or ambiguous')
            add(existing[0], flit.replace('.', '/')+('.py' if existing[0].endswith('.py') else ''))
    discovery = (maturin_module is None and explicit is None and selected is None and 'packages' not in poetry
                 and 'py-modules' not in setuptools and 'py_modules' not in legacy)
    if not mappings and discovery:
        stripped_roots = [original for original, destination in source_rewrites if original and not destination]
        default_where = (stripped_roots if stripped_roots and len(stripped_roots) == len(source_rewrites)
                         else ('src' if any(p.startswith('src/') for p in repo.files) else ''))
        where = find.get('where', base or default_where)
        locations = [where] if isinstance(where, str) else _strings(where, 'package discovery roots')
        includes = _strings(find.get('include', ['*']), 'package discovery include')
        excludes = _strings(find.get('exclude', []), 'package discovery exclude')
        for location in locations:
            prefix = '' if location in {'', '.'} else _path(location)+'/'
            candidates = set()
            for path in repo.files:
                if not path.startswith(prefix) or not path.endswith('.py'):
                    continue
                relative = path[len(prefix):]
                parts = relative.split('/')
                if any(part in _PROTECTED or part.startswith('.') for part in parts[:-1]):
                    continue
                name = parts[0]
                module = name[:-3] if name.endswith('.py') else name
                if module in {'setup', 'conftest', '__init__', '__main__'} or not _IMPORT.fullmatch(module):
                    continue
                dotted = relative.removesuffix('.py').replace('/', '.').removesuffix('.__init__')
                if not any(fnmatch.fnmatchcase(dotted, pattern) or fnmatch.fnmatchcase(module, pattern)
                           for pattern in includes):
                    continue
                if any(fnmatch.fnmatchcase(dotted, pattern) or fnmatch.fnmatchcase(module, pattern)
                       for pattern in excludes):
                    continue
                if len(parts) == 1 and not name.endswith('.py'):
                    continue
                candidates.add(name)
            for name in sorted(candidates):
                add(prefix+name, hatch_destination(prefix+name, name))
    extensions = setuptools.get('ext-modules', legacy.get('ext_modules', []))
    if not isinstance(extensions, list):
        _reject('native extension declarations must be a static list')
    for item in extensions:
        item = _table(item, 'native extension')
        name = item.get('name')
        sources = _strings(item.get('sources', []), 'native extension sources')
        if not isinstance(name, str) or not _IMPORT.fullmatch(name) or not sources:
            _reject('native extension name/sources are unresolved')
        paths = [_path(path) for path in sources]
        if any(path not in repo.files for path in paths):
            _reject('native extension sources are missing or contain unresolved globs: '+name)
        wheel = name.replace('.', '/')
        if any(wheel == item['wheel'] or wheel.startswith(item['wheel']+'/') for item in mappings):
            continue
        primary = next((path for path in paths if path.lower().endswith(_NATIVE)), None)
        if primary is None:
            _reject('native extension has no recognized native source: '+name)
        add(primary, wheel, extension=True)
    if maturin_module is not None and not any(maturin_module[1] == item['wheel']
                                            or maturin_module[1].startswith(item['wheel']+'/') for item in mappings):
        add(*maturin_module, extension=True)
    unique = {}
    for mapping in mappings:
        prior = unique.get(mapping['source'])
        if prior is not None and prior['wheel'] != mapping['wheel']:
            _reject('one source directory maps to inconsistent wheel namespaces: '+mapping['source'])
        unique[mapping['source']] = mapping
    mappings = []
    for mapping in sorted(unique.values(), key=lambda value: (len(value['source'].split('/')), value['source'])):
        if any(mapping['source'].startswith(parent['source']+'/')
               and mapping['wheel'] == parent['wheel']+mapping['source'][len(parent['source']):]
               for parent in mappings):
            continue
        mappings.append(mapping)
    mappings.sort(key=lambda value: value['source'])
    if not mappings or len(mappings) > 32:
        _reject('source layout is missing or exceeds 32 source mappings')
    for first in mappings:
        for second in mappings:
            if first != second and (first['source'].startswith(second['source']+'/')
                                    or first['wheel'].startswith(second['wheel']+'/')
                                    or first['wheel'] == second['wheel']):
                _reject('source layout contains overlapping/ambiguous wheel mappings')
    imports = []
    for mapping in mappings:
        components = mapping['wheel'].removesuffix('.py').split('/')
        module = '.'.join(components)
        if any(not re.fullmatch(r'[A-Za-z_]\w*', component, re.ASCII) for component in components):
            _reject('source mapping has no valid import module: '+mapping['wheel'])
        imports.append(module)
    forced = target.get('force-include', hatch.get('force-include', {}))
    schemes = re.sub(r'[-_.]+', '_', project_name).lower()+'-'+str(Version(project_version))+'.data'

    def package_destination(path):
        return any(path == mapping['wheel'] or path.startswith(mapping['wheel']+'/')
                   for mapping in mappings)

    def safe_asset_destination(path):
        parts = path.split('/')
        if (any(part in {'.pytest_cache', '.venv', 'deps', 'controller_checks', 'authoring_sessions', 'reference'}
                for part in parts) or path.endswith('.pth') or any(part.endswith('.dist-info') for part in parts)):
            return False
        if parts[0] != schemes:
            return package_destination(path) and not any(part.endswith('.data') for part in parts)
        if len(parts) < 3 or parts[1] not in {'purelib', 'platlib', 'data', 'scripts', 'headers'}:
            return False
        relative = '/'.join(parts[2:])
        if any(part.endswith(('.dist-info', '.data')) for part in parts[2:]):
            return False
        if parts[1] in {'purelib', 'platlib'}:
            return package_destination(relative)
        if parts[1] == 'scripts':
            return len(parts) == 3
        if parts[1] == 'data' and relative.endswith(('.py', '.pyi', '.so', '.pyd')):
            return package_destination(relative)
        return True

    destinations = {}
    for original, destination in _table(forced, 'Hatch force-include').items():
        original, destination = _path(original), _path(destination)
        if any(part in {'.pytest_cache', '.venv', 'venv', 'env', 'node_modules'} for part in original.split('/')):
            _reject('Hatch force-include reads a protected source path: '+original)
        files = [name for name in repo.files if name == original or name.startswith(original+'/')]
        if not files:
            _reject('Hatch force-include source is absent: '+original)
        for name in files:
            installed = destination+name[len(original):]
            if not safe_asset_destination(installed):
                _reject('Hatch force-include is outside declared imports/safe wheel schemes: '+installed)
            if installed in destinations and destinations[installed] != name:
                _reject('Hatch force-include has conflicting destination files: '+installed)
            destinations[installed] = name
        if not any(original == mapping['source'] or original.startswith(mapping['source']+'/') for mapping in mappings):
            assets.append(original)
    return mappings, imports, list(dict.fromkeys(assets))


def _entries(project, poetry, legacy, imports):
    values = []
    for group in ('scripts', 'gui-scripts'):
        entries = _table(project.get(group, {}), 'project.'+group)
        values.extend(entries.values())
    if not values:
        for value in _table(poetry.get('scripts', {}), 'Poetry scripts').values():
            if isinstance(value, dict):
                if value.get('type', 'console') != 'console':
                    _reject('Poetry file script cannot be represented as an import entry point')
                value = value.get('reference')
            values.append(value)
    if not values and legacy.get('entry_points'):
        for group, entries in _table(legacy['entry_points'], 'setup entry_points').items():
            if group not in {'console_scripts', 'gui_scripts'}:
                continue
            if isinstance(entries, str):
                entries = entries.splitlines()
            for entry in _strings(entries, 'setup entry points'):
                if not entry.strip():
                    continue
                _, separator, reference = entry.partition('=')
                if not separator:
                    _reject('setup entry point lacks a callable reference: '+entry)
                values.append(reference.strip())
    result = []
    for value in values:
        if not isinstance(value, str):
            _reject('entry point reference is not static text')
        module = value.split(':', 1)[0].strip()
        if not _IMPORT.fullmatch(module) or not any(module == name or module.startswith(name+'.') for name in imports):
            _reject('entry point is outside the inferred source modules: '+value)
        result.append(module)
    return list(dict.fromkeys(result or imports))


def infer_repository(source: SourceArchive) -> dict:
    """Return source-backed JSON metadata, rejecting unresolved declarations."""
    if not isinstance(source, SourceArchive) or len(source.files) > 10000:
        _reject('expected a bounded SourceArchive')
    repo = _Repository(source)
    try:
        return _infer(repo)
    except (RecursionError, TypeError, KeyError, AttributeError, IndexError) as exc:
        _reject('malformed or excessively nested static metadata: '+str(exc))


def _infer(repo):
    manifest = repo.toml('pyproject.toml') if 'pyproject.toml' in repo.files else {}
    project = _table(manifest.get('project', {}), 'project')
    tool = _table(manifest.get('tool', {}), 'tool')
    poetry = _table(tool.get('poetry', {}), 'tool.poetry')
    legacy = _setup_cfg(repo) if 'setup.cfg' in repo.files else {}
    if 'setup.py' in repo.files:
        setup = _setup_py(repo)
        legacy.update(setup)
    manifest_path = next((path for path in ('pyproject.toml', 'setup.cfg', 'setup.py') if path in repo.files), None)
    if manifest_path is None:
        _reject('no supported project build manifest (pyproject.toml, setup.cfg, setup.py)')
    build = _table(manifest.get('build-system', {}), 'build-system')
    if build.get('backend-path'):
        _reject('in-tree build backend requires unsupported controller-independent bootstrap')
    backend = build.get('build-backend', 'setuptools.build_meta:__legacy__')
    if not isinstance(backend, str) or not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?::[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)?', backend, re.ASCII):
        _reject('build backend is not a static import reference')
    build_requirements = _strings(build.get('requires', ['setuptools>=40.8.0']), 'build-system.requires')
    build_requirements += _strings(legacy.get('setup_requires', []), 'setup_requires')
    if backend == 'setuptools.build_meta:__legacy__' and not any(
            re.match(r'(?i)^wheel(?:\s|\[|[<>=!~;(]|$)', value) and ';' not in value
            for value in build_requirements):
        # Legacy PEP 517 setuptools permits versions which obtain bdist_wheel
        # from the separate wheel distribution. Its hook normally requests it;
        # include that hook's bootstrap requirement without running the hook.
        build_requirements.append('wheel')
    build_requirements = [repo.requirement(value, 'build requirements') for value in build_requirements]
    build_requirements = list(dict.fromkeys(value for value in build_requirements if value))
    if not build_requirements:
        _reject('build-system has no resolvable build requirements')
    name = project.get('name', poetry.get('name', legacy.get('name')))
    version = project.get('version', poetry.get('version', legacy.get('version')))
    requires_python = project.get('requires-python', legacy.get('python_requires', ''))
    requirements = _strings(project.get('dependencies', []), 'project.dependencies')
    if 'dependencies' not in project and poetry.get('dependencies'):
        requirements, poetry_python = _poetry_dependencies(repo, poetry['dependencies'])
        requires_python = requires_python or poetry_python
    if 'dependencies' not in project and not poetry.get('dependencies'):
        requirements += _strings(legacy.get('install_requires', []), 'install_requires')
    dynamic = _strings(project.get('dynamic', []), 'project.dynamic')
    dynamic_fields = _table(tool.get('setuptools', {}).get('dynamic', {}), 'setuptools dynamic metadata')
    for field in dynamic:
        if field in project:
            _reject('project field is both static and dynamic: '+field)
        directive = dynamic_fields.get(field)
        if field == 'version':
            if directive is not None:
                directive = _table(directive, 'dynamic version')
                if set(directive) == {'attr'}:
                    version = repo.attribute(directive['attr'], 'pyproject.toml')
                elif set(directive) == {'file'}:
                    files = directive['file']
                    files = [files] if isinstance(files, str) else _strings(files, 'dynamic version files')
                    if len(files) != 1:
                        _reject('dynamic version requires exactly one source file')
                    version = repo.version_file(files[0])
                else:
                    _reject('unsupported setuptools dynamic version directive')
            elif tool.get('hatch', {}).get('version', {}).get('path'):
                settings = tool['hatch']['version']
                if settings.get('source', 'regex') not in {'regex', 'code'}:
                    _reject('unsupported Hatch dynamic version source')
                version = repo.version_file(settings['path'], pattern=settings.get('pattern'))
            elif version is None:
                _reject('dynamic project version has no static file/attribute declaration')
        elif field in {'dependencies', 'optional-dependencies'}:
            if field == 'optional-dependencies':
                # Optional extras are not activated by runtime construction.
                continue
            if directive is not None:
                directive = _table(directive, 'dynamic dependencies')
                if set(directive) != {'file'}:
                    _reject('dynamic dependencies require static file declarations')
                files = directive['file']
                files = [files] if isinstance(files, str) else _strings(files, 'dynamic dependency files')
                requirements = []
                for path in files:
                    reqs, cons = repo.requirements(_path(path))
                    if cons:
                        _reject('dynamic project dependencies contain pip-only constraints: '+path)
                    requirements.extend(reqs)
            elif 'install_requires' not in legacy and not poetry.get('dependencies'):
                _reject('dynamic project dependencies cannot be resolved statically')
        elif field == 'requires-python':
            if not requires_python:
                _reject('dynamic Python version requirement cannot be resolved statically')
        elif field in {'scripts', 'gui-scripts', 'entry-points', 'name'}:
            _reject('dynamic project '+field+' cannot be resolved statically')
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        _reject('project name is missing or unresolved')
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        _reject('project version is missing or unresolved')
    try:
        version = str(Version(version))
    except InvalidVersion:
        _reject('project version is not PEP 440: '+version)
    selectors = _python_selectors(repo)
    constraints = []
    if not requires_python and poetry.get('dependencies', {}).get('python'):
        requires_python = _poetry_spec(poetry['dependencies']['python'], 'Poetry Python requirement')
    for path in ('uv.lock', 'poetry.lock'):
        if path not in repo.files:
            continue
        lock = repo.toml(path)
        value = lock.get('requires-python') if path == 'uv.lock' else lock.get('metadata', {}).get('python-versions')
        if value and value != '*':
            value = _poetry_spec(value, path+' Python requirement') if path == 'poetry.lock' else value
            if not isinstance(value, str):
                _reject(path+' has an unresolved Python requirement')
            requires_python = ','.join(filter(None, (requires_python, value)))
    if 'Pipfile' in repo.files:
        pipfile = repo.toml('Pipfile')
        indices = set()
        for source in pipfile.get('source', []):
            if source.get('url', '').rstrip('/') not in {'https://pypi.org/simple', 'https://pypi.python.org/simple'}:
                _reject('Pipfile requires an unsupported package index')
            indices.add(source.get('name'))
        packages = {}
        for package, value in _table(pipfile.get('packages', {}), 'Pipfile packages').items():
            if isinstance(value, dict) and 'index' in value:
                if value['index'] not in indices:
                    _reject('Pipfile references an unresolved package index: '+package)
                value = {key: setting for key, setting in value.items() if key != 'index'}
            packages[package] = value
        pip_requirements, _ = _poetry_dependencies(repo, packages)
        requirements.extend(pip_requirements)
    for path in ('requirements.txt', 'requirements.in', 'requirements/base.txt', 'requirements/main.txt'):
        if path in repo.files:
            reqs, cons = repo.requirements(path)
            requirements.extend(reqs)
            constraints.extend(cons)
    groups = _table(manifest.get('dependency-groups', {}), 'dependency-groups')
    requirements.extend(_strings(groups.get('tests', []), 'dependency-groups.tests'))
    for path in ('constraints.txt', 'constraints.in'):
        if path in repo.files:
            reqs, cons = repo.requirements(path, constraint=True)
            constraints.extend(cons)
    if not isinstance(requires_python, str):
        _reject('requires-python is not a static string')
    if not requires_python and not selectors:
        _reject('Python version is unresolved: requires-python, .python-version, runtime.txt, or literal CI version required')
    try:
        SpecifierSet(requires_python)
    except InvalidSpecifier:
        _reject('requires-python is not a PEP 440 specifier: '+requires_python)
    requirements = [repo.requirement(value, 'runtime requirements') for value in requirements]
    constraints.extend(_locks(repo, name))
    mappings, imports, assets = _source_layout(repo, name, version, tool, legacy, backend)
    def implementation_path(path):
        return (not any(part in _PROTECTED for part in path.split('/'))
                or any(path == item['source'] or path.startswith(item['source']+'/') for item in mappings))
    native = any(path.lower().endswith(_NATIVE) and implementation_path(path) for path in repo.files)
    system = _system_requirements(repo, native, mappings, build_requirements)
    for path in sorted(repo.files):
        if (posixpath.basename(path) in {'CMakeLists.txt', 'meson.build', 'meson_options.txt',
                                         'MANIFEST.in', 'Makefile', 'configure.ac', 'Cargo.toml', 'Cargo.lock'}
                and implementation_path(path)):
            repo.text(path)
    paths = sorted(repo.evidence)
    roots = list(dict.fromkeys([*(item['source'] for item in mappings), *assets, *paths]))
    if native:
        for path in sorted(repo.files):
            if path.lower().endswith((*_NATIVE, '.h', '.hpp')) and implementation_path(path):
                root = path.split('/')[0]
                if root not in roots:
                    roots.append(root)
    return {'project_name': name, 'project_version': version,
            'requires_python': requires_python, 'python_versions': list(dict.fromkeys(selectors)),
            'manifest_path': manifest_path, 'build_backend': backend,
            'build_requirements': build_requirements,
            'requirements': list(dict.fromkeys(value for value in requirements if value)),
            'constraints': list(dict.fromkeys(constraints)), 'source_roots': roots,
            'source_mappings': mappings, 'import_modules': list(dict.fromkeys(imports)),
            'entry_points': _entries(project, poetry, legacy, imports),
            'system_requirements': system, 'manifest_paths': paths,
            'evidence': dict(sorted(repo.evidence.items())),
            'dependency_hashes': {name: sorted(values) for name, values in sorted(repo.hashes.items())}}

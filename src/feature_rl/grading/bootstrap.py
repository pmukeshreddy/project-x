"""Fixed worker startup; candidate paths become visible after trusted imports.

This prevents startup/import-path hijacking of the observation transport. The
adapter still shares a Python process with candidate code; neither its source
nor its mutable Python state is a confidentiality or tamper-proof boundary.
"""
from pathlib import PurePosixPath

from feature_rl.artifacts import canonical_json


BOOTSTRAP = '''def _feature_rl_start_adapter():
    import sys
    sys.path[:] = [path for path in sys.path if path != '']
    import builtins, importlib, io, json, os, pathlib, subprocess, traceback
    code = compile(sys.argv[1], '<string>', 'exec')
    paths = json.loads(sys.argv[2])
    namespace = sys.modules['__main__'].__dict__
    original = {name: namespace[name] for name in (
        '__name__', '__doc__', '__package__', '__loader__', '__spec__', '__annotations__', '__builtins__'
    ) if name in namespace}
    sys.argv[:] = ['-c']
    sys.path[:0] = paths
    namespace.clear()
    namespace.update(original)
    exec(code, namespace)
_feature_rl_start_adapter()
'''


def adapter_argv(adapter, environment):
    """Use the validated active profile/recipe environment, never guessed paths.

    -I would also discard the fixed PYTHONHASHSEED. Instead remove PYTHONPATH
    before interpreter startup and disable site initialization (including
    .pth/sitecustomize) with -S. The bootstrap removes -c's implicit working
    directory before any non-builtin import; unlike -P this also works before
    Python 3.11 and with or without PYTHONSAFEPATH.
    """
    if type(adapter) is not bytes:
        raise ValueError('adapter bytes required')
    environment = tuple(environment)
    values = dict(environment)
    if len(values) != len(environment):
        raise ValueError('duplicate adapter environment variable')
    paths = () if 'PYTHONPATH' not in values else tuple(values['PYTHONPATH'].split(':'))
    if any(not path.startswith('/') or str(PurePosixPath(path)) != path
           or '..' in PurePosixPath(path).parts or '\x00' in path for path in paths):
        raise ValueError('adapter Python paths must be explicit canonical absolute paths')
    return ('/usr/bin/env', '-u', 'PYTHONPATH', '/usr/local/bin/python', '-S', '-c',
            BOOTSTRAP, adapter.decode('utf-8'), canonical_json(paths).decode('utf-8'))


def recipe_adapter_argv(checked):
    """Reconstruct the exact command from the already validated frozen recipe."""
    return adapter_argv(checked.adapter, ((item.name, item.value) for item in checked.recipe.environment))

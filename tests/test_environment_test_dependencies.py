import pytest

from feature_rl.environments import PolicyRejected, SourceArchive, SourceFile
from feature_rl.environments.inference import infer_repository


def repository(groups):
    manifest = '''\
[build-system]
requires = ["flit_core>=3.11,<4"]
build-backend = "flit_core.buildapi"
[project]
name = "example"
version = "1.0"
requires-python = ">=3.10"
'''
    return SourceArchive({
        'pyproject.toml': SourceFile((manifest + groups).encode(), False),
        'src/example/__init__.py': SourceFile(b'"""Example."""\n', False),
    })


def test_declared_test_dependencies_join_the_environment_requirements():
    metadata = infer_repository(repository('''\
[dependency-groups]
tests = ["pytest>=8"]
docs = ["sphinx"]
dev = ["ruff"]
'''))
    assert metadata['requirements'] == ['pytest>=8']


def test_unsupported_test_group_includes_are_rejected():
    with pytest.raises(PolicyRejected, match='dependency-groups.tests'):
        infer_repository(repository('''\
[dependency-groups]
tests = [{include-group = "shared"}]
shared = ["pytest"]
'''))

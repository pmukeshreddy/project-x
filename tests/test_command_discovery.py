import pytest

from feature_rl.requirements.runtime_discovery import CommandRuntimeProbeObservation


def observation(**changes):
    return CommandRuntimeProbeObservation(**dict(language='go', project_name='example.org/cli',
        project_version='', interpreter_version='1.25.0', entry_points=('bin/cli',),
        entry_point_files={'bin/cli': '/workspace/site/bin/cli'},
        supported_observables=('standard output',)) | changes)


def test_command_discovery_exposes_actual_built_entry_paths():
    assert observation().entry_point_files == {'bin/cli': '/workspace/site/bin/cli'}


@pytest.mark.parametrize('changes', [
    {'entry_point_files': {'bin/cli': '/workspace/source/bin/cli'}},
    {'entry_point_files': {'bin/other': '/workspace/site/bin/other'}},
    {'entry_points': ('bin/cli', 'bin/cli')},
    {'entry_points': ('../private',)},
])
def test_command_discovery_rejects_entry_path_and_roster_drift(changes):
    with pytest.raises(ValueError):
        observation(**changes)

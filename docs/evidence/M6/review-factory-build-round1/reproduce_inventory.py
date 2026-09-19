"""Reproducer for the observed stdin diagnostic; this file form was not rerun.

Trusted inert fixture and test-only wheel pins, no source/Docker/model execution.
PYTHONPATH=src:tests .venv/bin/python docs/evidence/M6/review-factory-build-round1/reproduce_inventory.py
"""
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from feature_rl.environments import SourceArchive, SourceFile
from feature_rl.pipeline import BuildRecoveryRequired
from test_factory import fixture


files = {
    'src/click/__init__.py': SourceFile(b'# inert diagnostic\n', False),
    'pyproject.toml': SourceFile(
        b'[project]\nname="click"\nversion="8.3.3"\n[build-system]\n'
        b'requires=["flit_core>=3.11,<4"]\nbuild-backend="flit_core.buildapi"\n', False),
}
prefix = 'src/' + 'a' * 150 + '/' + 'b' * 150 + '/' + 'c' * 150 + '/'
files.update({prefix + f'f{index:04}.py': SourceFile(b'# inert\n', False) for index in range(1900)})
archive = SourceArchive(files).to_tar()

with TemporaryDirectory(prefix='m6-build-review-') as directory, pytest.MonkeyPatch.context() as patch:
    _, store, registry, builder, inputs, *_ = fixture(
        Path(directory), patch, archive=archive, files=tuple(files))
    print('source files:', len(files), 'archive bytes:', len(archive),
          'request bytes:', len(inputs.model_dump_json().encode()))
    try:
        result = builder.build(inputs, owner='diagnostic-review', claim_key='inventory-bound')
        print('build result:', result.model_dump_json())
    except BuildRecoveryRequired as failure:
        print('build:', type(failure).__name__, type(failure.__cause__).__name__, str(failure.__cause__))
        print('job:', registry.job(failure.claim.job_id).state,
              'observation revision:', registry.accounting(failure.claim.job_id).observations[0].observation.revision)
        try:
            builder.recover(failure.claim)
        except BuildRecoveryRequired as recovery:
            print('recover:', type(recovery).__name__, type(recovery.__cause__).__name__, str(recovery.__cause__))

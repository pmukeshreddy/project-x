"""Optional CI metadata must not make valid Git histories unrecoverable."""
from datetime import datetime, timezone
import json

import pytest

from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole, Partition
from feature_rl.history import GitHistory
from feature_rl.intake import CachedSourceCatalog, GitHubPullRequestIntake, PullRequestIntakeSpec
from feature_rl.intake.request_document import parse_authoring_request
from feature_rl.splits import SplitPlanner
from test_click_intake import commit, git, write_cache


@pytest.mark.parametrize('with_ci', [False, True])
def test_intake_records_absent_or_existing_ci_metadata(tmp_path, with_ci):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '--initial-branch=main')
    (repo / 'LICENSE.txt').write_text('BSD diagnostic\n')
    (repo / 'src').mkdir()
    (repo / 'src/core.py').write_text('VALUE = 1\n')
    if with_ci:
        (repo / '.github/workflows').mkdir(parents=True)
        (repo / '.github/workflows/tests.yml').write_text('name: diagnostic\n')
    baseline = commit(repo, 'baseline')
    (repo / 'src/core.py').write_text('VALUE = 2\n')
    reference = commit(repo, 'implement feature')
    api = 'https://api.github.com/repos/example/project'
    sources = {
        'pr': (api + '/pulls/7', json.dumps(dict(number=7, title='Change value', body='Change the "value" to 2.\nPreserve the API.',
            created_at='2026-02-01T00:00:00Z', updated_at='2026-03-01T00:00:00Z',
            merged_at='2026-03-01T00:00:00Z', merge_commit_sha=reference,
            base={'sha': baseline}, head={'sha': reference})).encode()),
        'commits': (api + '/pulls/7/commits', json.dumps([{'sha': reference}]).encode()),
        'files': (api + '/pulls/7/files', b'[{"filename":"src/core.py"}]'),
        'license': (api + '/license', json.dumps(dict(sha=git(repo, 'rev-parse', f'{baseline}:LICENSE.txt'),
            license={'spdx_id': 'BSD-3-Clause'})).encode()),
        'license-text': ('https://raw.githubusercontent.com/example/project/main/LICENSE.txt', b'BSD diagnostic\n'),
    }
    manifest = write_cache(tmp_path / 'capture', sources)
    store = ArtifactStore(tmp_path / 'objects', ActorRole.CONTROLLER)
    intake = GitHubPullRequestIntake(store=store,
        catalog=CachedSourceCatalog(tmp_path / 'capture', manifest, max_bytes=1_000_000),
        history=GitHistory(repo / '.git'), factory_revision='a' * 40)
    spec = PullRequestIntakeSpec(repository_url='https://github.com/example/project',
        repository_family='example-project', request_lineage=('pr-7',), partition_source_ids=('pr-7',),
        source_names=('pr', 'commits', 'files', 'license'), pr_name='pr',
        issue_name=None, comments_name=None, commits_name='commits', files_name='files',
        license_name='license', license_text_name='license-text', license_path='LICENSE.txt',
        integration='linear', admissible_cutoff=datetime(2026, 2, 1, tzinfo=timezone.utc),
        recorded_at=datetime(2026, 9, 19, tzinfo=timezone.utc), provenance_label='reconstructed_specification',
        mixed_paths={}, max_tree_archive_bytes=1_000_000)
    partitions = SplitPlanner(()).assign({'pr-7': Partition.DEVELOPMENT})
    result = intake.ingest(spec, partitions)
    readable_request = store.get_bytes(result.authoring.request_evidence)
    assert b'Change the "value" to 2.\nPreserve the API.' in readable_request
    assert parse_authoring_request(readable_request)['pull_request']['body'] == 'Change the "value" to 2.\nPreserve the API.'
    pair = store.get_artifact(result.source_pair)
    proof = json.loads(store.get_bytes(pair.verification[0].artifacts[0]))
    expected = git(repo, 'rev-parse', f'{baseline}:.github') if with_ci else None
    assert proof['ci_config_baseline_object'] == expected
    assert proof['ci_config_reference_object'] == expected
    assert pair.baseline_commit == baseline and pair.reference_commit == reference
    assert intake.ingest(spec, partitions) == result

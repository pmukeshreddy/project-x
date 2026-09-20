"""Capture one GitHub feature for the existing cached-input workflow."""
import base64
from datetime import datetime, timezone
import csv
import hashlib
import io
import os
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

from pydantic import Field, TypeAdapter, field_validator
from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.environments.archive import safe_path
from feature_rl.history import GitHistory, HistoryError
from .github import (PullRequestIntakeSpec, GitHubPullRequestIntake, _json,
                     _parse_time, _reconstruct_pull_request)
from .sources import BoundedHttpFetcher, CachedSourceCatalog, SourceTooLarge


class GitHubPreparationRequest(c.StrictModel):
    version: Literal['github-preparation-request-v1'] = 'github-preparation-request-v1'
    repository_url: Annotated[str, Field(pattern=r'^https://github\.com/[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9._-]*$')]
    pull_request: Annotated[int, Field(ge=1)]
    issue: Annotated[int, Field(ge=1)]
    repository_family: c.Identifier
    request_lineage: Annotated[tuple[c.Identifier, ...], Field(min_length=1, max_length=64)]
    partition_source_ids: Annotated[tuple[c.Identifier, ...], Field(min_length=1, max_length=64)]
    integration: Literal['merge', 'squash', 'rebase', 'linear']
    admissible_cutoff: c.UTCDateTime
    license_path: str = 'LICENSE'
    token_env: Literal['GH_TOKEN', 'GITHUB_TOKEN'] | None = Field(default=None, exclude_if=lambda value: value is None)
    mixed_paths: dict[str, c.Text] = Field(default_factory=dict)
    max_response_bytes: Annotated[int, Field(ge=1024, le=64*1024*1024)] = 8*1024*1024
    max_capture_bytes: Annotated[int, Field(ge=1024, le=64*1024*1024)] = 64*1024*1024
    max_pages: Annotated[int, Field(ge=1, le=30)] = 10
    http_timeout_seconds: Annotated[float, Field(gt=0, le=120)] = 30.0
    git_timeout_seconds: Annotated[float, Field(gt=0, le=600)] = 120.0
    git_depth: Annotated[int, Field(ge=2, le=4096)] = 256
    history_timeout_seconds: Annotated[float, Field(gt=0, le=120)] = 30.0
    max_tree_archive_bytes: Annotated[int, Field(ge=1024, le=64*1024*1024)] = 16*1024*1024

    @field_validator('license_path')
    @classmethod
    def canonical_license(cls, value):
        if safe_path(value) != value:
            raise ValueError('canonical repository license path required')
        return value


def _write(path, data):
    with path.open('xb') as stream:
        stream.write(data)


def _read(path, cap):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > cap:
        raise ValueError('capture must contain bounded regular files')
    return path.read_bytes()


def _verify(root, request, prepared):
    """Reuse captured bytes and exact Git objects; never refresh a selected capture."""
    if (prepared['version'] != 'github-preparation-v1'
            or prepared['request'] != request.model_dump(mode='json')
            or _read(root/'request.json', 1024*1024) != canonical_json(prepared['request'])):
        raise ValueError('capture belongs to another preparation request')
    workflow = prepared['workflow']
    manifest = _read(root/'sources.tsv', 1024*1024)
    expected = dict(cache_root=str(root), cache_manifest_path=str(root/'sources.tsv'),
        cache_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
        git_directory=str(root/'repository.git'), source_max_bytes=request.max_response_bytes,
        git_timeout_seconds=request.history_timeout_seconds)
    if workflow != expected:
        raise ValueError('captured manifest or local input paths changed')
    catalog = CachedSourceCatalog(root, root/'sources.tsv', max_bytes=request.max_response_bytes)
    spec = TypeAdapter(PullRequestIntakeSpec).validate_json(canonical_json(prepared['intake']))
    bound = dict(repository_url=request.repository_url, repository_family=request.repository_family,
        request_lineage=request.request_lineage, partition_source_ids=request.partition_source_ids,
        integration=request.integration, admissible_cutoff=request.admissible_cutoff,
        license_path=request.license_path, mixed_paths=request.mixed_paths,
        max_tree_archive_bytes=request.max_tree_archive_bytes, provenance_label='reconstructed_specification',
        pr_name='pr', issue_name='issue', comments_name='comments', commits_name='commits',
        files_name='files', license_name='license', license_text_name='license-text')
    if any(getattr(spec, key) != value for key, value in bound.items()):
        raise ValueError('captured intake differs from the preparation request')
    if set(catalog.entries) != {*spec.source_names, spec.license_text_name, 'pr-confirmed', 'issue-confirmed'}:
        raise ValueError('captured source roster changed')
    loaded = {name: catalog.load(name, edit_history='unavailable') for name in catalog.entries}
    if sum(len(source.body) for source in loaded.values()) > request.max_capture_bytes:
        raise SourceTooLarge('aggregate source capture byte limit exceeded')
    if any(source.retrieved_at > spec.recorded_at for source in loaded.values()):
        raise ValueError('capture recording predates a source retrieval')
    pr = _json(loaded['pr'].body); issue = _json(loaded['issue'].body)
    for name, count in (('commits', pr['commits']), ('files', pr['changed_files']), ('comments', issue['comments'])):
        if len(GitHubPullRequestIntake._array(loaded, spec, name)) != count:
            raise ValueError('captured page selection differs from the recorded response count')
    history = GitHistory(root/'repository.git', timeout_seconds=request.history_timeout_seconds)
    commits = GitHubPullRequestIntake._array(loaded, spec, spec.commits_name)
    reconstruction, _ = _reconstruct_pull_request(history, spec.integration, _json(loaded[spec.pr_name].body), commits)
    if (reconstruction.baseline_commit != prepared['baseline_commit']
            or reconstruction.reference_commit != prepared['reference_commit']):
        raise ValueError('captured Git history changed')
    return prepared


def load_prepared(path: Path):
    """Verify a completed preparation document without network or acquisition."""
    path = Path(path)
    if path.name != 'prepared.json' or path.is_symlink():
        raise ValueError('the completed capture prepared.json is required')
    root = path.parent.resolve()
    request = GitHubPreparationRequest.model_validate_json(_read(root/'request.json', 1024*1024))
    return _verify(root, request, _json(_read(root/'prepared.json', 1024*1024)))


def prepare_github(request: GitHubPreparationRequest, output: Path):
    """Write a new private capture, or verify/reuse a completed one offline.

    No checkout, repository hook, task construction, model or admission runs here.
    An incomplete capture is retained and requires a fresh output directory.
    """
    request = GitHubPreparationRequest.model_validate_json(request.model_dump_json())
    output = Path(output)
    if not output.is_absolute() or output.is_symlink():
        raise ValueError('an absolute nonsymlink output directory is required')
    root = output.resolve()
    if root.exists():
        if not (root/'prepared.json').is_file():
            raise ValueError('capture is incomplete; use a new output directory')
        return _verify(root, request, _json(_read(root/'prepared.json', 1024*1024)))
    token = None if request.token_env is None else os.environ.get(request.token_env)
    if request.token_env is not None and not token:
        raise ValueError('the selected GitHub token environment variable is empty')
    fetcher = BoundedHttpFetcher(max_bytes=request.max_response_bytes,
                                timeout_seconds=request.http_timeout_seconds,bearer_token=token)
    root.mkdir(mode=0o700, parents=True)
    _write(root/'request.json', canonical_json(request.model_dump(mode='json')))
    (root/'responses').mkdir(mode=0o700)
    captured = {}; total = 0

    def fetch(name, url, *, text=False):
        nonlocal total
        source = fetcher.fetch(url, edit_history='unavailable' if not text else 'not_applicable',
                               media_type='text/plain' if text else 'application/json',
                               accept='application/vnd.github.raw+json' if text else 'application/vnd.github+json')
        total += len(source.body)
        if total > request.max_capture_bytes:
            raise SourceTooLarge('aggregate source capture byte limit exceeded')
        _write(root/'responses'/name, source.body)
        captured[name] = source
        return source.body if text else _json(source.body)

    slug = request.repository_url.removeprefix('https://github.com/')
    api = 'https://api.github.com/repos/'+slug
    pr_url = api+'/pulls/'+str(request.pull_request)
    issue_url = api+'/issues/'+str(request.issue)
    pr = fetch('pr', pr_url)
    issue = fetch('issue', issue_url)
    if (pr.get('number') != request.pull_request or not pr.get('merged')
            or pr.get('state') != 'closed'
            or pr['base']['repo']['full_name'].lower() != slug.lower()
            or issue.get('number') != request.issue or 'pull_request' in issue
            or issue.get('repository_url', '').lower() != api.lower()):
        raise ValueError('a merged PR and a matching repository issue are required')
    additional = {}

    def pages(name, url, count, key, limit):
        if type(count) is not int or not 0 <= count <= min(limit, request.max_pages*100):
            raise ValueError('source response count exceeds the supported capture bound')
        values = []; names = []
        for page in range(1, max(1, (count+99)//100)+1):
            page_name = name if page == 1 else name+'-'+str(page)
            items = fetch(page_name, url+'?per_page=100&page='+str(page))
            if not isinstance(items, list) or len(items) != min(100, count-len(values)):
                raise ValueError('source pagination is incomplete or changed during capture')
            names.append(page_name); values.extend(items)
        identities = [item[key] for item in values]
        if len(identities) != len(set(identities)):
            raise ValueError('source pages overlap or contain duplicate identities')
        if len(names) > 1:
            additional[name] = tuple(names[1:])
        return values

    commits = pages('commits', pr_url+'/commits', pr['commits'], 'sha', 250)
    files = pages('files', pr_url+'/files', pr['changed_files'], 'filename', 3000)
    pages('comments', issue_url+'/comments', issue['comments'], 'id', 3000)
    # Recheck mutable metadata; all actual HTTP bodies, including these, are retained.
    confirmed_pr = fetch('pr-confirmed', pr_url)
    confirmed_issue = fetch('issue-confirmed', issue_url)
    if (any(confirmed_pr.get(key) != pr.get(key) for key in (
            'number', 'merged', 'state', 'merge_commit_sha', 'commits', 'changed_files',
            'created_at', 'updated_at', 'merged_at', 'title', 'body'))
            or confirmed_pr['head']['sha'] != pr['head']['sha']
            or confirmed_pr['base']['repo']['full_name'] != pr['base']['repo']['full_name']
            or any(confirmed_issue.get(key) != issue.get(key) for key in (
                'number', 'repository_url', 'created_at', 'updated_at', 'comments', 'title', 'body'))):
        raise ValueError('PR or issue changed during capture; use a new capture')

    git_root = root/'repository.git'; git_root.mkdir(mode=0o700)
    history = GitHistory(git_root, timeout_seconds=request.git_timeout_seconds)
    integrated = history._revision(pr['merge_commit_sha'])
    head = history._revision(pr['head']['sha'])
    # Reuse M1's bounded process runner/cleanup with a new, empty bare repository.
    history._run_bytes('init', '--bare', '--template=', str(git_root))
    environment = {}
    encoded = None
    if token is not None:
        encoded = base64.b64encode(('x-access-token:'+token).encode()).decode('ascii')
        environment = {'GIT_CONFIG_COUNT':'1', 'GIT_CONFIG_KEY_0':'http.https://github.com/.extraheader',
                       'GIT_CONFIG_VALUE_0':'Authorization: Basic '+encoded}
    try:
        history._run_bytes('-c', 'protocol.allow=never', '-c', 'protocol.https.allow=always',
            '-c', 'credential.helper=', '-c', 'http.followRedirects=false',
            '-c', 'fetch.fsckObjects=true', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false',
            'fetch', '--no-tags', '--no-recurse-submodules', '--no-write-fetch-head',
            '--depth='+str(request.git_depth), request.repository_url+'.git',
            '+'+integrated+':refs/feature-rl/integrated', '+'+head+':refs/feature-rl/head',
            environment=environment)
    except HistoryError as exc:
        if token is None:raise
        raise HistoryError(str(exc).replace(token,'[redacted]').replace(encoded,'[redacted]')) from None
    history = GitHistory(git_root, timeout_seconds=request.history_timeout_seconds)
    reconstruction, source_commits = _reconstruct_pull_request(history, request.integration, pr, commits)
    changed = history.changed_paths(reconstruction.baseline_commit, reconstruction.reference_commit)
    if set(changed) != {item['filename'] for item in files}:
        raise ValueError('captured PR file list differs from Git history')
    source_objects = tuple(history.commit(revision) for revision in source_commits)
    started = min(stamp for item in source_objects for stamp in (item.authored_at, item.committed_at))
    if not _parse_time(issue['created_at']) <= request.admissible_cutoff <= started <= _parse_time(pr['merged_at']):
        raise ValueError('requested cutoff is not between issue creation and implementation')
    baseline = reconstruction.baseline_commit
    license_data = fetch('license', api+'/license?ref='+baseline)
    if license_data.get('path') != request.license_path or (license_data.get('license') or {}).get('spdx_id') in {None, '', 'NOASSERTION'}:
        raise ValueError('baseline license path or SPDX identity is unavailable')
    license_text = fetch('license-text', api+'/contents/'+quote(request.license_path, safe='/')+'?ref='+baseline, text=True)
    for revision in (baseline, reconstruction.reference_commit):
        if (history.path_object(revision, request.license_path) != license_data['sha']
                or history.path_bytes(revision, request.license_path,
                    max_bytes=request.max_tree_archive_bytes) != license_text):
            raise ValueError('captured license must match both baseline and reference')
    spec = PullRequestIntakeSpec(repository_url=request.repository_url,
        repository_family=request.repository_family, request_lineage=request.request_lineage,
        partition_source_ids=request.partition_source_ids,
        source_names=tuple(name for name in captured if name not in {'license-text', 'pr-confirmed', 'issue-confirmed'}),
        license_text_name='license-text', pr_name='pr', issue_name='issue', comments_name='comments',
        commits_name='commits', files_name='files', license_name='license',
        integration=request.integration, admissible_cutoff=request.admissible_cutoff,
        recorded_at=datetime.now(timezone.utc), provenance_label='reconstructed_specification',
        mixed_paths=request.mixed_paths, max_tree_archive_bytes=request.max_tree_archive_bytes,
        license_path=request.license_path, additional_pages=additional)
    manifest = io.StringIO(newline='')
    writer = csv.writer(manifest, delimiter='\t', lineterminator='\n')
    writer.writerow(CachedSourceCatalog._FIELDS)
    for name, source in captured.items():
        writer.writerow((name, source.status_code, source.retrieved_at.isoformat(), source.url,
                         'responses/'+name, hashlib.sha256(source.body).hexdigest()))
    manifest_bytes = manifest.getvalue().encode()
    _write(root/'sources.tsv', manifest_bytes)
    prepared = dict(version='github-preparation-v1', request=request.model_dump(mode='json'),
        intake=TypeAdapter(PullRequestIntakeSpec).dump_python(spec, mode='json'),
        baseline_commit=baseline, reference_commit=reconstruction.reference_commit,
        workflow=dict(cache_root=str(root), cache_manifest_path=str(root/'sources.tsv'),
            cache_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
            git_directory=str(git_root), source_max_bytes=request.max_response_bytes,
            git_timeout_seconds=request.history_timeout_seconds))
    _write(root/'prepared.json', canonical_json(prepared))
    return prepared

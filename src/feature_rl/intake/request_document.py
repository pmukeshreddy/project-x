"""One readable request artifact from the intake's verified evidence projection.

Metadata retains JSON types; natural-language fields occur once as raw UTF-8.
Length framing preserves arbitrary source quotes, newlines and delimiter-like
text. This renderer does not establish admissibility: GitHub intake must first
perform its source, chronology and historical/reconstructed evidence checks.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import re
from urllib.parse import urlsplit


MAX_REQUEST_BYTES = 1024 * 1024
_VERSION = b'authoring-request-readable-v1\n'
_PROVENANCE = {'source_url', 'source_response_sha256', 'retrieved_at', 'edit_history'}
_SUBJECT = _PROVENANCE | {'number', 'html_url', 'title', 'body', 'state', 'created_at',
    'updated_at', 'merged_at', 'merge_commit_sha', 'user', 'author_association', 'labels'}
_DISCUSSION = _PROVENANCE | {'id', 'html_url', 'body', 'user', 'author_association', 'state',
    'created_at', 'updated_at', 'submitted_at', 'commit_id', 'original_commit_id',
    'pull_request_review_id', 'in_reply_to_id', 'path', 'line', 'original_line',
    'start_line', 'side', 'start_side', 'position', 'original_position'}
_FILE = _PROVENANCE | {'status', 'previous_filename', 'additions', 'deletions', 'changes',
    'renamed_to', 'path', 'category', 'evidence_scope', 'rationale'}
_COMMIT = _PROVENANCE | {'sha', 'message', 'parents', 'authored_at', 'committed_at'}
_DISCUSSIONS = ('comments', 'pr_comments', 'reviews', 'review_comments')
_REQUIRED = {'provenance_label', 'admissible_cutoff', 'pull_request', 'issue',
    *_DISCUSSIONS, 'caveat', 'changed_files'}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _bound(max_bytes):
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_REQUEST_BYTES:
        raise ValueError('invalid authoring request byte limit')


def _json_types(value, depth=0):
    if depth > 64:
        raise ValueError('authoring request metadata nesting limit')
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_types(item, depth+1)
    elif type(value) is list:
        for item in value:
            _json_types(item, depth+1)
    elif type(value) not in {str, int, float, bool, type(None)} or (
            type(value) is float and not math.isfinite(value)):
        raise ValueError('authoring request requires exact JSON value types')


def _utc(value):
    if type(value) is not str:
        raise ValueError('authoring request timestamp must be a UTC string')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('authoring request timestamp must be a UTC string') from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError('authoring request timestamp must use UTC')


def _entry(value, allowed):
    if type(value) is not dict or not _PROVENANCE <= value.keys() or not value.keys() <= allowed:
        raise ValueError('authoring request contains fields outside the permitted intake projection')
    url = urlsplit(value['source_url']) if type(value['source_url']) is str else None
    if url is None or url.scheme != 'https' or not url.netloc or url.username or url.password:
        raise ValueError('authoring request source URL must be credential-free HTTPS')
    if type(value['source_response_sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', value['source_response_sha256']):
        raise ValueError('authoring request requires its exact source response SHA-256')
    _utc(value['retrieved_at'])
    if value['edit_history'] not in {'available', 'unavailable', 'not_applicable'}:
        raise ValueError('authoring request edit history is invalid')
    for name in ('number', 'id', 'line', 'original_line', 'start_line', 'position',
                 'original_position', 'additions', 'deletions', 'changes',
                 'pull_request_review_id', 'in_reply_to_id'):
        if name in value and value[name] is not None and type(value[name]) is not int:
            raise ValueError('authoring request numeric metadata must retain integer types')
    for name in ('title', 'body', 'message', 'rationale'):
        if name in value and value[name] is not None and type(value[name]) is not str:
            raise ValueError('authoring request prose must be a string or null')


def _validate(payload):
    _json_types(payload)
    if type(payload) is not dict or not _REQUIRED <= payload.keys() or not payload.keys() <= _REQUIRED | {'commits', 'history'}:
        raise ValueError('authoring request projection has unknown or missing fields')
    if payload['provenance_label'] not in {'historical_request', 'reconstructed_specification'}:
        raise ValueError('authoring request provenance label is invalid')
    if type(payload['caveat']) is not str or not payload['caveat']:
        raise ValueError('authoring request must retain its provenance caveat')
    _utc(payload['admissible_cutoff'])
    for name in ('pull_request', 'issue'):
        if payload[name] is not None:
            _entry(payload[name], _SUBJECT)
    for name in (*_DISCUSSIONS, 'changed_files', 'commits'):
        if name not in payload:
            continue
        if type(payload[name]) is not list:
            raise ValueError('authoring request evidence collections must be arrays')
        allowed = _FILE if name == 'changed_files' else _COMMIT if name == 'commits' else _DISCUSSION
        for entry in payload[name]:
            _entry(entry, allowed)
    if ('commits' in payload) != ('history' in payload):
        raise ValueError('authoring request commit metadata and history must remain paired')
    if payload['provenance_label'] == 'historical_request' and 'history' in payload:
        raise ValueError('historical authoring request cannot include implementation history')
    if 'history' in payload and (type(payload['history']) is not dict or set(payload['history']) != {
            'baseline_commit', 'reference_commit', 'patch_sha256', 'integration'}):
        raise ValueError('authoring request history projection is invalid')


def _locations(payload):
    """Fixed admitted prose locations, never arbitrary nested user metadata."""
    locations = {'/caveat': (payload, 'caveat')}
    for name in ('pull_request', 'issue'):
        value = payload.get(name)
        if type(value) is dict:
            for field in ('title', 'body'):
                if field in value:
                    locations[f'/{name}/{field}'] = (value, field)
    for name in (*_DISCUSSIONS, 'commits', 'changed_files'):
        field = 'message' if name == 'commits' else 'rationale' if name == 'changed_files' else 'body'
        for index, value in enumerate(payload.get(name, ())):
            if field in value:
                locations[f'/{name}/{index}/{field}'] = (value, field)
    return locations


def render_authoring_request(payload: dict, *, max_bytes=MAX_REQUEST_BYTES) -> bytes:
    """Render a verified projection without losing, normalizing or duplicating prose."""
    _bound(max_bytes)
    _validate(payload)
    if len(_canonical(payload)) > max_bytes:
        raise ValueError('authoring request exceeds byte limit')
    metadata = deepcopy(payload)
    texts = []
    for path, (parent, key) in _locations(metadata).items():
        if parent[key] is not None:
            texts.append((path, parent[key].encode('utf-8')))
            parent[key] = None
    header = _canonical({'metadata': metadata, 'text_paths': [path for path, _ in texts]})
    pieces = [_VERSION, f'Metadata UTF8-Bytes: {len(header)}\n'.encode(), header, b'\n']
    for path, text in texts:
        pieces.extend((f'Text {path} UTF8-Bytes: {len(text)}\n'.encode(), text, b'\n'))
    result = b''.join(pieces)
    if len(result) > max_bytes:
        raise ValueError('authoring request exceeds byte limit')
    return result


def parse_authoring_request(data: bytes, *, max_bytes=MAX_REQUEST_BYTES) -> dict:
    """Read this exact document version; malformed/legacy formats are rejected."""
    _bound(max_bytes)
    if type(data) is not bytes or len(data) > max_bytes:
        raise ValueError('authoring request exceeds byte limit or is not bytes')
    if not data.startswith(_VERSION):
        raise ValueError('unsupported authoring request document version')
    offset = len(_VERSION)

    def block(label):
        nonlocal offset
        end = data.find(b'\n', offset, offset+256)
        prefix = label.encode()+b' UTF8-Bytes: '
        line = data[offset:end] if end >= 0 else b''
        length = line[len(prefix):]
        if not line.startswith(prefix) or not re.fullmatch(b'0|[1-9][0-9]{0,6}', length):
            raise ValueError('malformed authoring request section header')
        start, stop = end+1, end+1+int(length)
        if stop >= len(data) or data[stop:stop+1] != b'\n':
            raise ValueError('truncated authoring request section')
        offset = stop+1
        return data[start:stop]

    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate authoring request metadata key')
            result[key] = value
        return result

    try:
        header = json.loads(block('Metadata'), object_pairs_hook=unique)
        if type(header) is not dict or set(header) != {'metadata', 'text_paths'}:
            raise ValueError('invalid authoring request metadata header')
        payload, paths = header['metadata'], header['text_paths']
        if type(payload) is not dict or type(paths) is not list or any(type(path) is not str for path in paths) or len(set(paths)) != len(paths):
            raise ValueError('invalid authoring request prose locations')
        locations = _locations(payload)
        for path in paths:
            if path not in locations:
                raise ValueError('authoring request text is outside permitted prose locations')
            parent, key = locations[path]
            if parent[key] is not None:
                raise ValueError('authoring request duplicates prose in its metadata')
            parent[key] = block('Text '+path).decode('utf-8')
        if offset != len(data) or render_authoring_request(payload, max_bytes=max_bytes) != data:
            raise ValueError('authoring request document is not canonical or has trailing bytes')
    except (TypeError, KeyError, IndexError, UnicodeError, RecursionError) as exc:
        raise ValueError('malformed authoring request document') from exc
    return payload


def request_ranking_prose(data: bytes, *, max_bytes=MAX_REQUEST_BYTES) -> str:
    """Extract only PR/issue/discussion prose; URL exclusion belongs to ranking."""
    payload = parse_authoring_request(data, max_bytes=max_bytes)
    return '\n'.join(parent[key] for path, (parent, key) in _locations(payload).items()
                     if key in {'title', 'body'} and parent[key] is not None)

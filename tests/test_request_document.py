"""Readable request evidence preserves exact prose and verified provenance."""
from copy import deepcopy
import json

import pytest

from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole, EvidenceLink, Visibility
from feature_rl.requirements import GroundedSource
from feature_rl.requirements.finalize import GroundingError, validate_link


def payload():
    provenance = dict(source_url='https://api.github.com/repos/example/project/pulls/7',
        source_response_sha256='a'*64, retrieved_at='2026-09-20T00:00:00Z', edit_history='unavailable')
    def item(**values):
        return provenance | values
    return dict(provenance_label='reconstructed_specification',
        admissible_cutoff='2026-01-01T00:00:00Z',
        caveat='Captured after implementation; this is reconstructed evidence.',
        pull_request=item(number=7, title='Suggest commands', body='This PR adds suggestions to the `"No such command"` error message.\r\nUse `resolve_command`.\nPath: C:\\new\\thing — café.',
            user={'login': 'person', 'id': 34, 'site_admin': False}, labels=[{'name': 'feature'}]),
        issue=item(number=6, title='Misspelled commands', body=None),
        comments=[item(id=1, body='Keep the "old" behavior.\nText /comments/0/body 100\nEnd.')],
        pr_comments=[item(id=2, body='Approved.')],
        reviews=[item(id=3, state='PENDING', body=None)],
        review_comments=[item(id=4, body='Use `NoSuchOption`.', line=12, original_line=None)],
        changed_files=[item(path='src/core.py', category='implementation', additions=2,
            evidence_scope='postimplementation_file_inventory', rationale='Implementation path')],
        commits=[item(sha='b'*40, message='Implement "suggestions"\n\nPreserve behavior.', parents=['c'*40],
            authored_at='2026-02-01T00:00:00Z', committed_at='2026-02-01T00:00:00Z')],
        history={'baseline_commit': 'c'*40, 'reference_commit': 'b'*40,
            'patch_sha256': 'd'*64, 'integration': 'linear'})


def test_readable_request_makes_verbatim_pr_quote_groundable_without_relaxing_validation(tmp_path):
    from feature_rl.intake.request_document import render_authoring_request
    value = payload()
    quote = 'This PR adds suggestions to the `"No such command"` error message.'
    assert quote not in json.dumps(value)
    rendered = render_authoring_request(value)
    store = ArtifactStore(tmp_path/'store', ActorRole.CONTROLLER)
    ref = store.put_bytes(rendered, 'authoring-request', Visibility.AUTHORING)
    source = GroundedSource(context_id='FEATURE_REQUEST', role='request', source=ref,
        locator='authoring-request:whole', text=store.get_bytes(ref).decode(),
        provenance_label='reconstructed_specification')
    link = EvidenceLink(source=ref, locator=source.locator, quote=quote,
        provenance_label=source.provenance_label)
    validate_link(link, (source,))
    with pytest.raises(GroundingError, match='quote is not present'):
        validate_link(link.model_copy(update={'quote': quote.replace('suggestions', 'recommendations')}), (source,))


def test_document_preserves_all_evidence_and_metadata_types_without_duplicate_prose():
    from feature_rl.intake.request_document import render_authoring_request, parse_authoring_request
    value = payload()
    encoded = render_authoring_request(value)
    assert parse_authoring_request(encoded) == value
    assert parse_authoring_request(encoded)['pull_request']['user']['site_admin'] is False
    assert type(parse_authoring_request(encoded)['pull_request']['user']['id']) is int
    for text in [value['pull_request']['body'], value['comments'][0]['body'],
                 value['commits'][0]['message'], value['review_comments'][0]['body']]:
        assert encoded.count(text.encode()) == 1
    assert value == payload(), 'rendering must not mutate the verified projection'
    reordered = dict(reversed(tuple(value.items())))
    assert render_authoring_request(reordered) == encoded


def test_ranking_reads_only_request_title_and_body_sections():
    from feature_rl.intake.request_document import render_authoring_request, request_ranking_prose
    value = payload()
    value['pull_request']['user']['title'] = 'metadata_symbol'
    value['commits'][0]['message'] = 'commit_symbol'
    value['changed_files'][0]['rationale'] = 'inventory_symbol'
    prose = request_ranking_prose(render_authoring_request(value))
    assert 'resolve_command' in prose and 'NoSuchOption' in prose and 'Misspelled commands' in prose
    assert 'metadata_symbol' not in prose and 'commit_symbol' not in prose and 'inventory_symbol' not in prose


def test_document_rejects_oversized_or_malformed_bytes_without_truncation_or_json_fallback():
    from feature_rl.intake.request_document import render_authoring_request, parse_authoring_request
    encoded = render_authoring_request(payload())
    assert render_authoring_request(payload(), max_bytes=len(encoded)) == encoded
    with pytest.raises(ValueError, match='byte limit'):
        render_authoring_request(payload(), max_bytes=len(encoded)-1)
    with pytest.raises(ValueError, match='byte limit'):
        parse_authoring_request(encoded, max_bytes=len(encoded)-1)
    for bad in (encoded[:-1], encoded+b'additional evidence', json.dumps(payload()).encode(),
                encoded.replace(b'authoring-request-readable-v1', b'authoring-request-readable-v2', 1)):
        with pytest.raises(ValueError):
            parse_authoring_request(bad)


@pytest.mark.parametrize('mutation', ['private_patch', 'bad_hash', 'bool_number', 'wrong_body'])
def test_document_rejects_projection_expansion_and_invalid_provenance(mutation):
    from feature_rl.intake.request_document import render_authoring_request
    value = deepcopy(payload())
    if mutation == 'private_patch':
        value['changed_files'][0]['patch'] = 'private implementation diff'
    elif mutation == 'bad_hash':
        value['pull_request']['source_response_sha256'] = 'not-a-source-hash'
    elif mutation == 'bool_number':
        value['pull_request']['number'] = True
    else:
        value['comments'][0]['body'] = {'text': 'not a source string'}
    with pytest.raises(ValueError):
        render_authoring_request(value)


def test_historical_document_does_not_synthesize_postimplementation_history():
    from feature_rl.intake.request_document import render_authoring_request, parse_authoring_request
    value = payload()
    value['provenance_label'] = 'historical_request'
    del value['commits'], value['history']
    assert parse_authoring_request(render_authoring_request(value)) == value

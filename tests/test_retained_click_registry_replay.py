"""Opt-in, read-only replay of the exact retained Click 3228 v2 stop.

Set FEATURE_RL_RETAINED_BATCH_ROOT to the astra-batch-20260920 directory.
The immutable first 497 events are selected even if recovery later appends events.
No provider dispatch, registry initialization, or artifact publication is allowed.
"""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from feature_rl.artifacts import ArtifactSizeLimitError, ArtifactStore, canonical_json
from feature_rl.contracts import ActorRole, ArtifactRef
from feature_rl.generation import GenerationCallRecord, GenerationRequest, GenerationResult
from feature_rl.pipeline.authoring import provider_outcome
from feature_rl.pipeline.packaging import MAX_DOCUMENT, read_bytes
from feature_rl.registry import Registry, RegistryLimits
from feature_rl.registry.models import RegistryEvent
from feature_rl.registry.state import State, identity
from feature_rl.requirements import RequirementContractProposal
from feature_rl.scenarios import ScenarioPlanProposal
from feature_rl.verifiers import ControlProposal


WORKFLOW = '14f0f5d3cf6db88d96f4a2e851ad02eadf203d93dbc3c906e9ffc9e8cc66cf3e'
PREFIX_SHA256 = '69ea1871ea56c3e06576866e383314bf95acd0305d5a2650d554a9e478028371'
FAILED_REF = '48b83e98c788c923557ad15400140559653da3c537507360fa0b3a481ad90837'


@pytest.fixture(scope='module')
def retained():
    configured = os.environ.get('FEATURE_RL_RETAINED_BATCH_ROOT')
    if configured is None:
        pytest.skip('requires the exact retained Click 3228 v2 evidence directory')
    root = Path(configured).resolve()
    # These are the original run's declared limits, not the current registry's
    # potentially expanded capacity policy.
    limits = RegistryLimits(lock_timeout_seconds=30.0,
        max_closure_bytes=33554432, max_closure_artifacts=256)
    state = State(limits)
    digest = hashlib.sha256()
    previous = '0' * 64
    with (root / 'final/click-3228-registry-v2/events.jsonl').open('rb') as stream:
        for sequence in range(1, 498):
            raw = stream.readline(limits.max_event_bytes + 1)
            assert raw.endswith(b'\n') and len(raw) <= limits.max_event_bytes
            event = RegistryEvent.model_validate_json(raw)
            assert canonical_json(event.model_dump(mode='json')) + b'\n' == raw
            assert event.sequence == sequence and event.previous == previous
            assert event.event_id == identity(event.model_dump(mode='json', exclude={'event_id'}))
            state.apply(event.action, event.data)
            previous = event.event_id
            digest.update(raw)
    assert digest.hexdigest() == PREFIX_SHA256
    # Bypass constructors which create directories/recover journals. All reads
    # below still use the production ArtifactStore integrity and size checks.
    store = object.__new__(ArtifactStore)
    store.root, store.role = root / 'store', ActorRole.CONTROLLER
    return state, store


def test_actual_workflow_accounting_closure_exceeds_original_capacity(retained, monkeypatch):
    state, store = retained
    registry = object.__new__(Registry)
    registry.store = store
    registry._storage = SimpleNamespace(limits=state.limits)
    report = state.accounting(WORKFLOW)
    roots = tuple(ref for item in report.observations for ref in item.observation.receipts)
    assert len(report.observations) == 17 and len(roots) == 51
    original = store.get_bytes
    failed = []
    verified = set()

    def tracked(ref, **limits):
        try:
            value = original(ref, **limits)
            verified.add(ref)
            return value
        except ArtifactSizeLimitError:
            failed.append(ref)
            raise

    monkeypatch.setattr(store, 'get_bytes', tracked)
    original_artifact = store.get_artifact

    def tracked_artifact(ref, **limits):
        value = original_artifact(ref, **limits)
        verified.add(ref)
        return value

    monkeypatch.setattr(store, 'get_artifact', tracked_artifact)
    with pytest.raises(ArtifactSizeLimitError) as caught:
        registry._verify(state, roots)
    assert (caught.value.limit_name, caught.value.limit, caught.value.observed_bytes) == (
        'max_envelope_bytes', 365820, 554225)
    assert [(ref.kind, ref.sha256) for ref in failed] == [('m6-authoring-request', FAILED_REF)]

    # Capacity changes do not change the artifact/schema bounds, identity,
    # retained claims, observed costs, or bytes checked by the registry reader.
    registry._storage.limits = state.limits.model_copy(update={'max_closure_bytes': 64 * 1024 * 1024})
    verified.clear()
    registry._verify(state, roots)
    assert len(verified) == 248
    assert sum((store.root / (ref.sha256 + '.json')).stat().st_size
               for ref in verified) == 33742837
    assert state.accounting(WORKFLOW) == report


def test_all_actual_generation_archives_replay_with_declared_limits(retained, monkeypatch):
    from feature_rl.generation import CodexGenerationProvider

    def forbidden(*args, **kwargs):
        pytest.fail('retained replay must never dispatch a model call')

    monkeypatch.setattr(CodexGenerationProvider, 'generate', forbidden)
    state, store = retained
    schemas = {'initial_authoring': RequirementContractProposal,
        'scenario_planning': ScenarioPlanProposal, 'control_authoring': ControlProposal}
    counts = Counter()
    for item in state.artifacts.values():
        if item.ref.kind != 'generation-status':
            continue
        status = json.loads(read_bytes(store, item.ref, MAX_DOCUMENT))
        request_ref = ArtifactRef.model_validate_json(canonical_json(status['archive_refs']['request']))
        request = GenerationRequest.model_validate_json(read_bytes(store, request_ref, MAX_DOCUMENT + 4096))
        record = GenerationCallRecord.model_validate_json(canonical_json({
            key: status[key] for key in ('attempt_id', 'recorded_at', 'request_id',
                'response_id', 'success', 'generation_succeeded', 'publication_complete')
        } | {'error_code': status['error_type'], 'archives': status['archive_refs'] |
            {'status': item.ref.model_dump(mode='json')}}))
        outcome = provider_outcome(store, request, record, schemas[request.stage.value])
        assert isinstance(outcome, GenerationResult) and outcome.record == record
        counts[request.stage.value] += 1
    assert counts == {'initial_authoring': 2, 'scenario_planning': 2, 'control_authoring': 10}

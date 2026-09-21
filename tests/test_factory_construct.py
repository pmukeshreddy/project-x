"""Actual Factory→TaskBuilder join with diagnostic inert M0/M3 artifact bytes."""
import pytest

from feature_rl import contracts as c
from feature_rl.pipeline import Factory
from feature_rl.pipeline.packaging import read_record
from feature_rl.pipeline.construction import ConstructionResult
from test_factory import fixture, mutate


def configured(tmp_path, monkeypatch):
    values = fixture(tmp_path,monkeypatch)
    _, store, registry, builder, inputs, *_ = values
    assert hasattr(Factory,'construct'), 'actual construct orchestration is missing'
    factory = Factory(store=store,registry=registry,builder=builder,revision='e'*40)
    candidate = store.get_artifact(inputs.source_pair).candidate
    return factory,candidate,inputs


def test_construction_selects_real_built_root_and_receipt(tmp_path,monkeypatch):
    factory,candidate,inputs = configured(tmp_path,monkeypatch)
    result = factory.construct(candidate,inputs=inputs)
    assert result.disposition==c.Disposition.SUCCESS
    built = factory.store.get_artifact(result.artifacts[0])
    assert built.state==c.TaskState.BUILT and built.qualification is None
    assert built.source_pair==inputs.source_pair
    assert tuple(ref.kind for ref in result.artifacts)==('TaskBundle','m6-construction-result')
    receipt = read_record(factory.store,result.artifacts[1],ConstructionResult,'m6-construction-result')
    assert receipt.build_result.artifacts[0]==result.artifacts[0]
    assert 'history' not in ConstructionResult.model_fields
    before=factory.registry.events(limit=1000)
    assert factory.construct(candidate,inputs=inputs)==result
    assert factory.registry.events(limit=1000)==before
    jobs=[factory.registry.job(j) for j in factory.registry.trace(candidate).jobs]
    parent=next(j for j in jobs if j.spec.invocation=='m6-construct')
    assert parent.result==result and result.artifacts[1] in parent.result.artifacts
    assert len([j for j in jobs if j.spec.invocation=='m6-source-admission'])==1


def test_missing_authored_inputs_produces_selected_blocked_operation(tmp_path,monkeypatch):
    factory,candidate,_ = configured(tmp_path,monkeypatch)
    result=factory.construct(candidate)
    assert result.disposition==c.Disposition.BLOCKED
    assert not any(r.kind=='TaskBundle' for r in result.artifacts)
    assert 'inputs' in result.reason.lower()
    assert factory.construct(candidate)==result


def test_rejected_source_never_enters_builder(tmp_path,monkeypatch):
    factory,candidate,inputs=configured(tmp_path,monkeypatch)
    old=factory.store.get_artifact(candidate)
    candidate=mutate(factory.store,candidate,screening=old.screening.model_copy(update={
        'disposition':c.Disposition.REJECTED,'reason':'TEST ONLY rejected candidate'}))
    def forbidden(*args,**kwargs): raise AssertionError('rejected source entered builder')
    monkeypatch.setattr(factory.builder,'build',forbidden)
    assert factory.construct(candidate,inputs=inputs).disposition==c.Disposition.REJECTED


def test_wrong_candidate_source_pair_is_recorded_invalid_before_assembly(tmp_path,monkeypatch):
    factory,candidate,inputs=configured(tmp_path,monkeypatch)
    different=mutate(factory.store,candidate,repository_family='different-diagnostic')
    result=factory.construct(different,inputs=inputs)
    assert result.disposition==c.Disposition.INVALID
    assert not any(r.kind=='TaskBundle' for r in result.artifacts)


def test_parent_publication_recovers_selected_build_without_reassembly(tmp_path,monkeypatch):
    from feature_rl.pipeline import FactoryPublicationFailed
    factory,candidate,inputs=configured(tmp_path,monkeypatch)
    put=factory.store.put_bytes
    def lost(data,kind,visibility):
        ref=put(data,kind,visibility)
        if kind=='m6-construction-result':raise OSError('TEST ONLY lost parent result reply')
        return ref
    monkeypatch.setattr(factory.store,'put_bytes',lost)
    with pytest.raises(FactoryPublicationFailed) as pending:factory.construct(candidate,inputs=inputs)
    monkeypatch.setattr(factory.store,'put_bytes',put)
    def forbidden(*args,**kwargs):raise AssertionError('completed builder reran assembly')
    monkeypatch.setattr(factory.builder,'build',forbidden)
    result=factory.retry_publication(pending.value)
    assert result.disposition==c.Disposition.SUCCESS
    assert factory.recover(pending.value.claim)==result


def test_pre_parent_freeze_recovers_completed_builder_and_keeps_overhead_unknown(tmp_path,monkeypatch):
    from feature_rl.pipeline import FactoryRecoveryRequired
    factory,candidate,inputs=configured(tmp_path,monkeypatch)
    put=factory.store.put_bytes
    def stop(data,kind,visibility):
        if kind=='m6-construction-result':raise SystemExit('TEST ONLY process stop before parent freeze')
        return put(data,kind,visibility)
    monkeypatch.setattr(factory.store,'put_bytes',stop)
    with pytest.raises(SystemExit):factory.construct(candidate,inputs=inputs)
    job=next(factory.registry.job(j) for j in factory.registry.trace(candidate).jobs
        if factory.registry.job(j).spec.invocation=='m6-construct')
    claim=factory.registry.attempts(job.job_id)[-1].claim
    monkeypatch.setattr(factory.store,'put_bytes',put)
    result=factory.recover(claim)
    assert result.disposition==c.Disposition.SUCCESS
    assert all(x.measurement=='unknown' for x in result.costs)


def test_original_source_quarantine_traces_constructed_root(tmp_path,monkeypatch):
    factory,candidate,inputs=configured(tmp_path,monkeypatch)
    result=factory.construct(candidate,inputs=inputs)
    source=factory.screen_source(candidate).artifacts[0]
    proof=factory.store.put_bytes(b'TEST ONLY source defect','construct-test-proof',c.Visibility.PRIVATE)
    factory.registry.quarantine(source,notice_id='source-diagnostic',reason='TEST ONLY',evidence=(proof,))
    assert result.artifacts[0] in factory.registry.trace(source).artifacts


def test_builder_publication_pending_replays_through_actual_builder(tmp_path,monkeypatch):
    from feature_rl.pipeline import FactoryUpstreamPending
    factory,candidate,inputs=configured(tmp_path,monkeypatch)
    put=factory.store.put_bytes
    def outage(data,kind,visibility):
        if kind=='m6-frozen-build':raise OSError('TEST ONLY builder freeze publication')
        return put(data,kind,visibility)
    monkeypatch.setattr(factory.store,'put_bytes',outage)
    with pytest.raises(FactoryUpstreamPending) as pending:factory.construct(candidate,inputs=inputs)
    monkeypatch.setattr(factory.store,'put_bytes',put)
    result=factory.retry_publication(pending.value)
    assert result.disposition==c.Disposition.SUCCESS
    assert result==factory.recover(pending.value.claim)


def test_unknown_builder_attempt_is_not_assembled_again(tmp_path,monkeypatch):
    from feature_rl.pipeline import FactoryRecoveryRequired
    import feature_rl.pipeline.packaging as packaging
    factory,candidate,inputs=configured(tmp_path,monkeypatch)
    def stop(*args,**kwargs):raise SystemExit('TEST ONLY stop inside builder before freeze')
    monkeypatch.setattr(packaging,'assemble',stop)
    with pytest.raises(SystemExit):factory.construct(candidate,inputs=inputs)
    job=next(factory.registry.job(j) for j in factory.registry.trace(candidate).jobs
        if factory.registry.job(j).spec.invocation=='m6-construct')
    claim=factory.registry.attempts(job.job_id)[-1].claim
    with pytest.raises(FactoryRecoveryRequired,match='unresolved'):factory.recover(claim)
    assert factory.registry.job(job.job_id).state=='running'

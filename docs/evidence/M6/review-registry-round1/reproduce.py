"""Retained stdin diagnostic: trusted synthetic registry inputs; no feature execution.

Originally executed as PYTHONPATH=src .venv/bin/python - via a quoted heredoc.
This file form has not been rerun. The two cases demonstrate observed defects;
exit 0 means the diagnostic ran, not that the registry passed acceptance.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timezone
import sqlite3
from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole, Visibility, CostRecord, EvidenceRecord, OperationResult, Disposition
from feature_rl.registry import Registry, RegistryLimits, JobSpec, CostObservation

with TemporaryDirectory(prefix='m6-review-row-') as d:
    root = Path(d).resolve()
    store = ArtifactStore(root / 'objects', ActorRole.CONTROLLER)
    a = store.put_bytes(b'a', 'input', Visibility.PRIVATE)
    b = store.put_bytes(b'b', 'config', Visibility.PRIVATE)
    reg = Registry(root / 'registry', store, limits=RegistryLimits(max_attempts_per_job=100, max_event_bytes=2200))
    job = reg.enqueue(JobSpec(operation='construct', inputs=(a,), configuration=b, implementation='a'*40, invocation='row', attempt_limit=100))
    for i in range(100):
        try:
            claim = reg.claim(job.job_id, owner='worker', claim_key=f'claim-{i}')
            with sqlite3.connect(reg.root / 'index.sqlite3') as con:
                maximum = con.execute('SELECT max(length(body)) FROM records').fetchone()[0]
                event_max = con.execute('SELECT max(length(body)) FROM events').fetchone()[0]
            if maximum > 2200:
                print('ROW accepted claim', i+1, 'record bytes', maximum, 'largest event', event_max)
                try:
                    reg.recover()
                except Exception as e:
                    print('ROW recover failed', type(e).__name__, str(e))
                break
            reg.abandon(claim, reason='synthetic interruption', evidence=(a,))
            reg.retry(job.job_id, reason='synthetic diagnosed retry', evidence=(b,))
        except Exception as e:
            print('ROW earlier failure', i+1, type(e).__name__, str(e))
            break

with TemporaryDirectory(prefix='m6-review-evidence-') as d:
    root = Path(d).resolve()
    store = ArtifactStore(root / 'objects', ActorRole.CONTROLLER)
    a,b,e,x = [store.put_bytes(v.encode(), v, Visibility.PRIVATE) for v in ('input','config','proof','output')]
    reg = Registry(root / 'registry', store)
    job = reg.enqueue(JobSpec(operation='construct',inputs=(a,),configuration=b,implementation='a'*40,invocation='proof',attempt_limit=1))
    claim = reg.claim(job.job_id,owner='worker',claim_key='claim')
    cost = CostRecord(category='construction',wall_seconds=None,cpu_seconds=None,gpu_seconds=None,input_tokens=None,output_tokens=None,human_minutes=None,usd=None,measurement='unknown',note='synthetic')
    obs = reg.reconcile(claim, CostObservation(source='review',upstream_attempt_id='call',revision=1,receipts=(a,),costs=(cost,)))
    reg.register(e)
    reg.quarantine(e,notice_id='bad-proof',reason='synthetic evidence defect',evidence=(b,))
    result = OperationResult(operation='construct',disposition=Disposition.SUCCESS,artifacts=(x,),evidence=(EvidenceRecord(producer='review',command=('inert',),recorded_at=datetime(2026,9,19,tzinfo=timezone.utc),exit_status=0,artifacts=(e,),revision='a'*40,scope='unit_diagnostic'),),costs=(cost,),reason='synthetic')
    reg.complete(claim,result,observations=(obs.observation_id,))
    reg.assert_usable(x)
    print('PROOF accepted completed output using quarantined evidence; trace',reg.trace(e).model_dump_json())

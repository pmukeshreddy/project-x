"""Deterministic replay and registry state invariants; no filesystem operations."""
from __future__ import annotations

import hashlib
from pydantic import BaseModel

from feature_rl.artifacts import canonical_json
from feature_rl.contracts import OperationResult
from .models import (
    AccountingReport, ArtifactRecord, AttemptLimit, AttemptRecord, Backpressure,
    Claim, ClaimConflict, CostObservation, JobRecord, JobSpec, ObservationRecord,
    QuarantinedError, QuarantineNotice, RegistryConflict, RegistryIntegrityError,
    RegistryLimit, StaleClaim, TraceReport, UnknownIdentity,
)
from .historical import ignored_roots


def document(value):
    return value.model_dump(mode='json') if isinstance(value, BaseModel) else value


def identity(value):
    return hashlib.sha256(canonical_json(document(value))).hexdigest()


def validated(model, value):
    return model.model_validate_json(canonical_json(document(value)))


def updated(value, **changes):
    return validated(type(value), document(value) | {k: document(v) for k, v in changes.items()})


def _result_evidence(result):
    return tuple(ref for evidence in result.evidence for ref in evidence.artifacts)


class State:
    def __init__(self, limits):
        self.limits = limits
        self.artifacts = {}
        self.jobs = {}
        self.attempts = {}
        self.observations = {}
        self.notices = {}

    def rows(self):
        return {(namespace, key): canonical_json(document(value))
                for namespace in ('artifacts', 'jobs', 'attempts', 'observations', 'notices')
                for key, value in getattr(self, namespace).items()}

    def get_job(self, job_id):
        try:
            return self.jobs[job_id]
        except KeyError as exc:
            raise UnknownIdentity('unknown job identity') from exc

    def authenticate(self, claim, *, running=False):
        attempt = self.attempts.get(claim.attempt_id)
        if attempt is None or attempt.claim != claim:
            raise StaleClaim('unknown or mismatched claim capability')
        job = self.get_job(claim.job_id)
        if running and (attempt.state != 'running' or job.state != 'running'
                        or job.attempts[-1] != claim.attempt_id):
            raise StaleClaim('attempt no longer owns the running job')
        return attempt, job

    def install(self, records):
        for record in records:
            old = self.artifacts.get(record.ref.sha256)
            if old is not None and old != record:
                raise RegistryConflict('immutable artifact identity/dependency declaration drift')
            self.artifacts[record.ref.sha256] = record
        if len(self.artifacts) > self.limits.max_artifacts:
            raise RegistryLimit('artifact index capacity exceeded')
        remaining, children = {}, {}
        edge_count = 0
        for key, record in self.artifacts.items():
            parents = {r.sha256 for r in record.dependencies}
            if len(parents) != len(record.dependencies):
                raise RegistryConflict('duplicate dependency')
            edge_count += len(parents)
            remaining[key] = len(parents)
            for parent in record.dependencies:
                if parent.sha256 not in self.artifacts or self.artifacts[parent.sha256].ref != parent:
                    raise RegistryConflict('unregistered or inconsistent dependency reference')
                children.setdefault(parent.sha256, set()).add(key)
        if edge_count > self.limits.max_graph_edges:
            raise RegistryLimit('dependency edge capacity exceeded')
        ready = [key for key, count in remaining.items() if count == 0]
        visited = 0
        while ready:
            key = ready.pop()
            visited += 1
            for child in children.get(key, ()):
                remaining[child] -= 1
                if remaining[child] == 0:
                    ready.append(child)
        if visited != len(remaining):
            raise RegistryConflict('dependency cycle')

    def affected(self, root):
        if root.sha256 not in self.artifacts or self.artifacts[root.sha256].ref != root:
            raise UnknownIdentity('unregistered exact artifact reference')
        seen, jobs = {root.sha256}, set()
        changed = True
        while changed:
            size = len(seen) + len(jobs)
            for key, record in self.artifacts.items():
                if any(ref.sha256 in seen for ref in record.dependencies):
                    seen.add(key)
            for key, job in self.jobs.items():
                dependencies = (*job.spec.inputs, job.spec.configuration)
                if job.result is not None:
                    dependencies += _result_evidence(job.result)
                if any(ref.sha256 in seen for ref in dependencies):
                    jobs.add(key)
                    if job.result is not None:
                        seen.update(ref.sha256 for ref in job.result.artifacts)
            changed = len(seen) + len(jobs) != size
        return seen, jobs

    def usable(self, refs, *, ignored=frozenset()):
        for ref in refs:
            if ref.sha256 not in self.artifacts or self.artifacts[ref.sha256].ref != ref:
                raise UnknownIdentity('unregistered exact artifact reference')
            for notice in self.notices.values():
                if notice.active and notice.root.sha256 not in ignored and ref.sha256 in self.affected(notice.root)[0]:
                    raise QuarantinedError('artifact is affected by quarantine ' + notice.notice_id)

    def job_usable(self,spec,refs):
        self.usable(refs,ignored=ignored_roots(self,spec))

    def trace(self, ref):
        seen, jobs = self.affected(ref)
        refs = tuple(self.artifacts[key].ref for key in sorted(seen))
        if len(refs) > 1024:
            raise RegistryLimit('trace exceeds bounded response; use smaller registry partition')
        notices = tuple(notice for _, notice in sorted(self.notices.items())
                        if self.affected(notice.root)[0] & seen)
        return TraceReport(artifacts=refs, jobs=tuple(sorted(jobs)),
                           runs=tuple(r for r in refs if r.kind == 'RolloutRecord'),
                           checkpoints=tuple(r for r in refs if r.kind == 'TrainingCheckpoint'), notices=notices)

    def latest(self):
        result = {}
        for item in self.observations.values():
            key = (item.observation.source, item.observation.upstream_attempt_id)
            if key not in result or item.observation.revision > result[key].observation.revision:
                result[key] = item
        return result

    def accounting(self, job_id):
        job = self.get_job(job_id)
        observations = tuple(sorted((item for item in self.latest().values() if item.attempt_id in job.attempts),
                                    key=lambda item: item.observation_id))
        covered = {item.attempt_id for item in observations}
        return AccountingReport(observations=observations,
                                unobserved_attempts=tuple(a for a in job.attempts if a not in covered))

    def apply(self, action, data):
        shapes = {
            'register': {'artifacts'}, 'enqueue': {'artifacts', 'spec'},
            'claim': {'artifacts', 'claim'}, 'abandon': {'artifacts', 'claim', 'reason', 'evidence'},
            'retry': {'artifacts', 'job_id', 'reason', 'evidence'},
            'reconcile': {'artifacts', 'claim', 'observation'},
            'complete': {'artifacts', 'claim', 'result', 'observations'},
            'quarantine': {'artifacts', 'notice'},
            'lift': {'artifacts', 'notice_id', 'reason', 'evidence'},
        }
        if action not in shapes or set(data) != shapes[action] or type(data['artifacts']) is not list:
            raise RegistryIntegrityError('unexpected event payload shape')
        records = tuple(validated(ArtifactRecord, r) for r in data['artifacts'])
        if len(records) > self.limits.max_closure_artifacts:
            raise RegistryLimit('event artifact closure exceeded')
        self.install(records)
        if action == 'register':
            return
        if action == 'enqueue':
            spec = validated(JobSpec, data['spec'])
            key = identity(spec)
            if key in self.jobs:
                raise RegistryConflict('duplicate enqueue event')
            if len(self.jobs) >= self.limits.max_jobs:
                raise RegistryLimit('job index capacity exceeded')
            if sum(job.state in ('queued', 'running', 'paused') for job in self.jobs.values()) >= self.limits.max_active_jobs:
                raise Backpressure('active job queue is full')
            if spec.attempt_limit > self.limits.max_attempts_per_job:
                raise AttemptLimit('job attempt limit exceeds registry policy')
            self.job_usable(spec,(*spec.inputs, spec.configuration))
            self.jobs[key] = JobRecord(job_id=key, spec=spec, state='queued', attempts=(), result=None, result_observations=())
            return
        if action in ('claim', 'abandon', 'reconcile', 'complete'):
            claim = validated(Claim, data['claim'])
            job = self.get_job(claim.job_id)
        if action == 'claim':
            if job.state != 'queued':
                raise ClaimConflict('job is not queued; paused jobs require explicit retry')
            if any(attempt.claim.claim_key == claim.claim_key for attempt in self.attempts.values()):
                raise ClaimConflict('claim key already belongs to an attempt')
            if len(job.attempts) >= job.spec.attempt_limit:
                raise AttemptLimit('job attempt limit exhausted')
            if claim.attempt_id != identity({'job_id': job.job_id, 'claim_key': claim.claim_key, 'owner': claim.owner}):
                raise RegistryConflict('attempt identity mismatch')
            self.job_usable(job.spec,(*job.spec.inputs, job.spec.configuration))
            self.attempts[claim.attempt_id] = AttemptRecord(claim=claim, state='running', reason=None, evidence=())
            self.jobs[job.job_id] = updated(job, state='running', attempts=[*job.attempts, claim.attempt_id])
        elif action == 'abandon':
            attempt, job = self.authenticate(claim, running=True)
            candidate = updated(attempt, state='abandoned', reason=data['reason'], evidence=data['evidence'])
            if not candidate.evidence:
                raise RegistryConflict('abandonment requires supervisor evidence')
            self.attempts[claim.attempt_id] = candidate
            self.jobs[job.job_id] = updated(job, state='exhausted' if len(job.attempts) >= job.spec.attempt_limit else 'paused')
        elif action == 'retry':
            job = self.get_job(data['job_id'])
            if len(job.attempts) >= job.spec.attempt_limit:
                raise AttemptLimit('job attempt limit exhausted')
            if job.state != 'paused':
                raise ClaimConflict('only paused interrupted jobs can be explicitly retried')
            # Validate reason/evidence using the same strict envelope as abandonment.
            record = updated(self.attempts[job.attempts[-1]], reason=data['reason'], evidence=data['evidence'])
            if not record.evidence:
                raise RegistryConflict('retry requires diagnosed cause/change evidence')
            self.job_usable(job.spec,(*job.spec.inputs, job.spec.configuration))
            self.jobs[job.job_id] = updated(job, state='queued')
        elif action == 'reconcile':
            self.authenticate(claim)
            observation = validated(CostObservation, data['observation'])
            latest = self.latest().get((observation.source, observation.upstream_attempt_id))
            if latest is None:
                if observation.revision != 1:
                    raise RegistryConflict('first accounting snapshot must be revision 1')
            else:
                old = latest.observation
                if latest.attempt_id != claim.attempt_id:
                    raise RegistryConflict('upstream attempt already attributed to another registry attempt')
                if observation.revision != old.revision + 1:
                    raise RegistryConflict('accounting revisions must be contiguous')
                if not set(old.receipts) <= set(observation.receipts):
                    raise RegistryConflict('new snapshot cannot drop existing receipts')
                channels = {cost.category: cost for cost in observation.costs}
                for cost in old.costs:
                    if cost.category not in channels:
                        raise RegistryConflict('new snapshot cannot drop incurred cost channels')
                    newer = channels[cost.category]
                    for field in ('wall_seconds', 'cpu_seconds', 'gpu_seconds', 'input_tokens', 'output_tokens', 'human_minutes', 'usd'):
                        previous, current = getattr(cost, field), getattr(newer, field)
                        if previous is not None and (current is None or current < previous):
                            raise RegistryConflict('new snapshot cannot erase or reduce known incurred cost')
            key = identity({'attempt_id': claim.attempt_id, 'observation': document(observation)})
            self.observations[key] = ObservationRecord(observation_id=key, attempt_id=claim.attempt_id, observation=observation)
        elif action == 'complete':
            attempt, job = self.authenticate(claim, running=True)
            result = validated(OperationResult, data['result'])
            if result.operation != job.spec.operation:
                raise RegistryConflict('operation result does not match job operation')
            wanted = tuple(sorted(item.observation_id for item in self.latest().values() if item.attempt_id == claim.attempt_id))
            if not wanted or tuple(data['observations']) != wanted:
                raise RegistryConflict('completion must bind all current accounting snapshots of its attempt')
            costs = tuple(cost for key in wanted for cost in self.observations[key].observation.costs)
            if result.costs != costs:
                raise RegistryConflict('result costs do not match the declared snapshot records')
            self.job_usable(job.spec,(*job.spec.inputs, job.spec.configuration, *result.artifacts, *_result_evidence(result)))
            self.attempts[claim.attempt_id] = updated(attempt, state='completed')
            self.jobs[job.job_id] = updated(job, state='completed', result=result, result_observations=list(wanted))
        elif action == 'quarantine':
            notice = validated(QuarantineNotice, data['notice'])
            if notice.notice_id in self.notices or not notice.active or notice.resolution_reason is not None or notice.resolution_evidence:
                raise RegistryConflict('new quarantine must be an unresolved unique notice')
            self.affected(notice.root)
            self.notices[notice.notice_id] = notice
        elif action == 'lift':
            try:
                notice = self.notices[data['notice_id']]
            except KeyError as exc:
                raise UnknownIdentity('unknown quarantine notice') from exc
            candidate = updated(notice, active=False, resolution_reason=data['reason'], resolution_evidence=data['evidence'])
            if not notice.active or not candidate.resolution_evidence:
                raise RegistryConflict('lifting requires an active notice and resolution evidence')
            self.notices[notice.notice_id] = candidate

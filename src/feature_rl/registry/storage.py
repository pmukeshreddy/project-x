"""Bounded SQLite event authority and verified append-only JSONL projection.

Only trusted local controllers may access this private directory. SQLite and the
JSONL file do not share a transaction. No physical power-loss guarantee is made.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import stat
import time

from pydantic import ValidationError
from feature_rl.artifacts import canonical_json
from .models import (ArtifactRecord, LimitExpansion, RegistryEvent, RegistryIntegrityError,
                     RegistryIOError, RegistryLimit, RegistryLimits, RegistryConflict, RecoveryReport)
from .state import State, document, identity, validated

FILES = {'registry.lock', 'index.sqlite3', 'index.sqlite3-journal', 'events.jsonl'}


def write_all(fd, data):
    while data:
        count = os.write(fd, data)
        if count <= 0:
            raise OSError('file write made no progress')
        data = data[count:]


def read_bounded(fd, limit):
    if os.fstat(fd).st_size > limit:
        raise RegistryLimit('registry file exceeds configured byte limit')
    out = bytearray()
    while len(out) <= limit:
        chunk = os.read(fd, min(65536, limit + 1 - len(out)))
        if not chunk:
            return bytes(out)
        out.extend(chunk)
    raise RegistryLimit('registry file grew beyond configured byte limit')


class Storage:
    def __init__(self, root, limits, store_root):
        if not isinstance(root, Path):
            raise TypeError('registry root must be a Path')
        self.root = root.absolute()
        self.limits = limits
        self.requested_limits = limits
        self.store_root = str(store_root.absolute())
        self.configuration = self.config(limits)
        self._verified_snapshot = None
        # Creation is limited to missing directories; an existing unsafe shape is rejected.
        with self.directory(create=True):
            pass
        self.recover()

    def config(self, limits):
        return canonical_json({'version': 1, 'limits': document(limits), 'store_root': self.store_root})

    @contextmanager
    def directory(self, create=False):
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in self.root.parts[1:]:
                if component in ('.', '..'):
                    raise RegistryIntegrityError('relative path components are forbidden')
                if create:
                    try:
                        os.mkdir(component, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                    else:
                        os.fsync(fd)
                next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            metadata = os.fstat(fd)
            if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
                raise RegistryIntegrityError('registry directory must be private and owned by the controller user')
            yield fd
        except OSError as exc:
            raise RegistryIntegrityError('unsafe or unavailable registry directory') from exc
        finally:
            os.close(fd)

    def shape(self, directory, *, opening=False):
        names = set(os.listdir(directory))
        if not names <= FILES:
            raise RegistryIntegrityError('unexpected registry state-directory members')
        for name in names:
            meta = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1 or meta.st_uid != os.getuid()
                    or stat.S_IMODE(meta.st_mode) & 0o077):
                raise RegistryIntegrityError('registry files must be private regular files without links')
            # An existing client may have old limits. Permit only the schema's
            # absolute file ceiling until locked metadata + history authenticate
            # the current capacity; normal operations recheck the active limits.
            cap = 1073741824 if opening else self.limits.max_journal_bytes if name == 'events.jsonl' else self.limits.max_database_bytes
            if meta.st_size > cap:
                raise RegistryLimit('registry file capacity exceeded')
        return names

    @contextmanager
    def session(self):
        with self.directory() as directory:
            lock = None
            con = None
            try:
                initial = self.shape(directory, opening=True)
                if 'index.sqlite3' in initial and 'registry.lock' not in initial:
                    raise RegistryIntegrityError('writer lock missing beside existing database')
                lock = os.open('registry.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                deadline = time.monotonic() + self.limits.lock_timeout_seconds
                while True:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise RegistryIOError('registry writer lock deadline exceeded')
                        time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
                names = self.shape(directory, opening=True)
                if 'events.jsonl' not in names:
                    if 'index.sqlite3' in names and os.stat('index.sqlite3', dir_fd=directory).st_size:
                        raise RegistryIntegrityError('journal missing beside existing database; preserve and reconcile')
                    fd = os.open('events.jsonl', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                    os.fsync(fd)
                    os.close(fd)
                    os.fsync(directory)
                if 'index.sqlite3' not in names:
                    if os.stat('events.jsonl', dir_fd=directory).st_size:
                        raise RegistryIntegrityError('database missing beside nonempty journal')
                    fd = os.open('index.sqlite3', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                    os.fsync(fd)
                    os.close(fd)
                    os.fsync(directory)
                before = os.stat('index.sqlite3', dir_fd=directory, follow_symlinks=False)
                con = sqlite3.connect(self.root / 'index.sqlite3', isolation_level=None, timeout=self.limits.lock_timeout_seconds)
                if hasattr(con, 'autocommit'):
                    con.autocommit = sqlite3.LEGACY_TRANSACTION_CONTROL
                con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, max(65536, self.limits.max_event_bytes * 2))
                con.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 65536)
                con.execute('PRAGMA journal_mode=DELETE')
                con.execute('PRAGMA synchronous=EXTRA')
                con.execute('PRAGMA fullfsync=ON')
                con.execute('PRAGMA trusted_schema=OFF')
                after = os.stat(self.root / 'index.sqlite3', follow_symlinks=False)
                root_meta = os.stat(self.root, follow_symlinks=False)
                if ((before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                        or (root_meta.st_dev, root_meta.st_ino) != (os.fstat(directory).st_dev, os.fstat(directory).st_ino)):
                    raise RegistryIntegrityError('registry path changed during database opening')
                self.initialize(con, directory)
                self.connection_limits(con)
                self.shape(directory)
                yield con, directory
            except (sqlite3.Error, OSError) as exc:
                raise RegistryIOError('registry storage failed; recover/query before retrying work') from exc
            finally:
                if con is not None:
                    con.close()  # Pending SQLite transactions roll back; external file bytes remain.
                if lock is not None:
                    os.close(lock)

    def initialize(self, con, directory):
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not names:
            if os.stat('events.jsonl', dir_fd=directory).st_size:
                raise RegistryIntegrityError('uninitialized database has existing journal history')
            con.execute('BEGIN IMMEDIATE')
            con.execute('CREATE TABLE meta(configuration BLOB NOT NULL, acknowledged INTEGER NOT NULL)')
            con.execute('CREATE TABLE events(sequence INTEGER PRIMARY KEY, semantic_key TEXT UNIQUE NOT NULL, body BLOB NOT NULL)')
            con.execute('CREATE TABLE records(namespace TEXT NOT NULL, key TEXT NOT NULL, body BLOB NOT NULL, PRIMARY KEY(namespace,key))')
            con.execute("CREATE TRIGGER events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'immutable event'); END")
            con.execute("CREATE TRIGGER events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'immutable event'); END")
            con.execute('INSERT INTO meta VALUES(?, 0)', (self.configuration,))
            con.execute('COMMIT')
            os.fsync(directory)
        elif names != {'meta', 'events', 'records'}:
            raise RegistryIntegrityError('unexpected SQLite schema')
        rows = con.execute('SELECT configuration, acknowledged FROM meta').fetchall()
        if len(rows) != 1 or type(rows[0][1]) is not int or rows[0][1] < 0:
            raise RegistryIntegrityError('registry configuration/version or acknowledgement mismatch')
        try:
            if len(rows[0][0]) > 65536:
                raise ValueError('configuration byte limit')
            configuration = json.loads(rows[0][0])
            if (set(configuration) != {'version', 'limits', 'store_root'} or type(configuration['version']) is not int or configuration['version'] != 1
                    or configuration['store_root'] != self.store_root
                    or canonical_json(configuration) != rows[0][0]):
                raise ValueError('configuration identity mismatch')
            self.limits = validated(RegistryLimits, configuration['limits'])
            self.configuration = rows[0][0]
        except (ValueError, TypeError, KeyError) as exc:
            raise RegistryIntegrityError('registry configuration/version mismatch') from exc
        # load() must authenticate the complete expansion chain before callers
        # may use these metadata limits or publish/recover journal bytes.

    def connection_limits(self, con):
        con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, max(65536, self.limits.max_event_bytes * 2))
        page_size = con.execute('PRAGMA page_size').fetchone()[0]
        page_cap = self.limits.max_database_bytes // page_size
        if con.execute('PRAGMA max_page_count=' + str(page_cap)).fetchone()[0] > page_cap:
            raise RegistryLimit('database page capacity exceeded')

    def check_record_bounds(self, count, size, longest):
        if (count > self.limits.max_artifacts + self.limits.max_jobs + self.limits.max_events * 2
                or size > self.limits.max_journal_bytes * 4 or longest > self.limits.max_event_bytes):
            raise RegistryLimit('materialized index exceeds configured bounds')

    def load(self, con):
        count, size, longest = con.execute('SELECT count(*), coalesce(sum(length(body)),0), coalesce(max(length(body)),0) FROM events').fetchone()
        if count > self.limits.max_events or size > self.limits.max_journal_bytes or longest > self.limits.max_event_bytes:
            raise RegistryLimit('authoritative history exceeds configured bounds')
        if con.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise RegistryIntegrityError('SQLite integrity check failed')
        events, raw = [], []
        previous = '0' * 64
        try:
            for sequence, key, body in con.execute('SELECT sequence, semantic_key, body FROM events ORDER BY sequence'):
                event = RegistryEvent.model_validate_json(body)
                if (sequence != len(events) + 1 or event.sequence != sequence or event.semantic_key != key
                        or event.previous != previous or canonical_json(document(event)) + b'\n' != body
                        or event.event_id != identity(event.model_dump(mode='json', exclude={'event_id'}))):
                    raise RegistryIntegrityError('event ordering, canonical bytes or hash chain mismatch')
                previous = event.event_id
                events.append(event)
                raw.append(body)
            expansions = [event for event in events if event.action == 'expand_limits']
            initial = validated(RegistryLimits, expansions[0].data['previous_limits']) if expansions else self.limits
            state = State(initial)
            capacities = [initial]
            for event in events:
                state.apply(event.action, event.data)
                if event.action == 'expand_limits':
                    capacities.append(state.limits)
            if state.limits != self.limits or self.requested_limits not in capacities:
                raise RegistryIntegrityError('registry limits drift from the audited capacity chain')
        except (ValidationError, ValueError, KeyError, RecursionError, RegistryConflict) as exc:
            raise RegistryIntegrityError('invalid authoritative event history') from exc
        self.check_record_bounds(*con.execute('SELECT count(*), coalesce(sum(length(body)),0), coalesce(max(length(body)),0) FROM records').fetchone())
        actual = {(namespace, key): body for namespace, key, body in con.execute('SELECT namespace,key,body FROM records')}
        if state.rows() != actual:
            raise RegistryIntegrityError('materialized index differs from authoritative history')
        return state, events, raw

    def project(self, con, directory, raw):
        fd = os.open('events.jsonl', os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW, dir_fd=directory)
        try:
            original = read_bounded(fd, self.limits.max_journal_bytes)
            pieces = original.split(b'\n')
            complete, tail = [line + b'\n' for line in pieces[:-1]], pieces[-1]
            ack = con.execute('SELECT acknowledged FROM meta').fetchone()[0]
            if ack > len(complete) or ack > len(raw):
                raise RegistryIntegrityError('acknowledged journal history is missing; preserve and block')
            if len(complete) > len(raw) or complete != raw[:len(complete)]:
                raise RegistryIntegrityError('journal differs from committed outbox; preserve and block')
            if tail and (len(complete) == len(raw) or not raw[len(complete)].startswith(tail)):
                raise RegistryIntegrityError('unknown or corrupt journal tail; preserve and block')
            suffix = b''.join(raw[len(complete):])[len(tail):]
            write_all(fd, suffix)
            os.fsync(fd)  # Required even if another process appended all complete bytes.
            con.execute('UPDATE meta SET acknowledged=?', (len(raw),))
            return RecoveryReport(event_count=len(raw), appended_bytes=len(suffix), completed_tail_bytes=len(tail))
        finally:
            os.close(fd)

    def fingerprint(self, directory):
        # All callers hold the controller lock. Inode and change time detect
        # replacement and same-size edits even when modification time is restored.
        return tuple((meta.st_dev, meta.st_ino, meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns)
            for name in ('index.sqlite3', 'events.jsonl')
            for meta in (os.stat(name, dir_fd=directory, follow_symlinks=False),))

    def remember(self, directory, state, events, raw):
        self._verified_snapshot = (self.fingerprint(directory), deepcopy((state, events, raw)))

    def recovered(self, con, directory, *, copy_snapshot=True):
        fingerprint = self.fingerprint(directory)
        if self._verified_snapshot is not None and self._verified_snapshot[0] == fingerprint:
            snapshot = self._verified_snapshot[1]
            state, events, raw = deepcopy(snapshot) if copy_snapshot else snapshot
            return state, events, raw, RecoveryReport(event_count=len(raw), appended_bytes=0, completed_tail_bytes=0)
        con.execute('BEGIN IMMEDIATE')
        state, events, raw = self.load(con)
        report = self.project(con, directory, raw)
        con.execute('COMMIT')
        self.remember(directory, state, events, raw)
        return state, events, raw, report

    def recover(self):
        with self.session() as (con, directory):
            return self.recovered(con, directory, copy_snapshot=False)[3]

    def view(self, reader):
        # Registry's internal readers do not mutate state. Copy their selected
        # result, so callers cannot alter cached models or nested event data.
        with self.session() as (con, directory):
            state, events, _, _ = self.recovered(con, directory, copy_snapshot=False)
            return deepcopy(reader(state, events))

    def persist(self, con, event, body, state):
        rows = state.rows()
        sizes = [len(body) for body in rows.values()]
        self.check_record_bounds(len(rows), sum(sizes), max(sizes, default=0))
        con.execute('INSERT INTO events VALUES(?,?,?)', (event.sequence, event.semantic_key, body))
        con.execute('DELETE FROM records')
        con.executemany('INSERT INTO records VALUES(?,?,?)', [(namespace, key, body) for (namespace, key), body in rows.items()])

    def change(self, builder):
        with self.session() as (con, directory):
            state, events, raw, _ = self.recovered(con, directory)
            built = builder(state)
            if built is None:
                return state
            action, key, data = built
            prior = next((e for e in events if e.semantic_key == key), None)
            if prior is not None:
                # Artifact rows are an index delta, not part of semantic request
                # identity. Verify the full closure on every retry, then require
                # every submitted/prior row to match immutable registered state.
                records = {r['ref']['sha256']: validated(ArtifactRecord, r) for r in data['artifacts']}
                if (prior.action != action
                        or canonical_json({k:v for k,v in prior.data.items() if k!='artifacts'})
                           != canonical_json({k:v for k,v in data.items() if k!='artifacts'})
                        or any(state.artifacts.get(key) != value for key,value in records.items())
                        or any(records.get(r['ref']['sha256']) != validated(ArtifactRecord,r) for r in prior.data['artifacts'])):
                    raise RegistryConflict('semantic event key reused with different content')
                return state
            # New events retain only new records; _verify already read and
            # validated all ancestor bytes. Changed known rows remain in the
            # payload so State.install rejects immutable-dependency drift.
            data = dict(data, artifacts=[r for r in data['artifacts']
                if state.artifacts.get(r['ref']['sha256']) != validated(ArtifactRecord,r)])
            if action == 'expand_limits':
                state.apply(action, data)
                self.limits = state.limits
                self.connection_limits(con)
            if len(events) >= self.limits.max_events:
                raise RegistryLimit('event capacity exceeded')
            prototype = RegistryEvent(sequence=len(events) + 1, event_id='0' * 64,
                                      previous=events[-1].event_id if events else '0' * 64,
                                      semantic_key=key, recorded_at=datetime.now(timezone.utc), action=action, data=data)
            event = validated(RegistryEvent, document(prototype) | {'event_id': identity(prototype.model_dump(mode='json', exclude={'event_id'}))})
            body = canonical_json(document(event)) + b'\n'
            if len(body) > self.limits.max_event_bytes or sum(map(len, raw)) + len(body) > self.limits.max_journal_bytes:
                raise RegistryLimit('event or journal byte capacity exceeded')
            if action != 'expand_limits':
                state.apply(action, data)
            con.execute('BEGIN IMMEDIATE')
            self.persist(con, event, body, state)
            if action == 'expand_limits':
                con.execute('UPDATE meta SET configuration=?', (self.config(state.limits),))
            con.execute('COMMIT')  # JSONL publication happens only AFTER authoritative commit.
            self.configuration = self.config(state.limits)
            con.execute('BEGIN IMMEDIATE')
            self.project(con, directory, [*raw, body])
            con.execute('COMMIT')
            self.shape(directory)
            self.remember(directory, state, [*events, event], [*raw, body])
            return state

    def expand_limits(self, limits, reason):
        def build(state):
            if state.limits == limits:
                return None
            try:
                expansion = LimitExpansion(previous_limits=state.limits, limits=limits, reason=reason)
            except ValueError as exc:
                raise RegistryConflict(str(exc)) from exc
            return 'expand_limits', 'expand_limits/'+identity(limits), {'artifacts': [], **document(expansion)}
        return self.change(build).limits

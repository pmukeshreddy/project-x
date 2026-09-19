#!/usr/bin/env python3
"""TRUSTED, stdlib-only M6 diagnostic. Never imports or executes product/task code.

Every database, artifact and journal is NEW ignored research state. This is a
small protocol experiment, not a registry implementation or qualification test.
No real task, approval, reward, model call or paid service is represented here.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import sqlite3
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
STATE = ROOT / ".feature-rl/research/M6/durability-preflight-20260919"
RECEIPT = ROOT / "docs/evidence/M6/durability-preflight-receipts.json"
SCOPE = "TRUSTED_SYNTHETIC_DIAGNOSTIC_NO_REAL_TASK_APPROVAL_OR_REWARD"
INPUTS = (
    "feature_rl_pipeline.md", "codex_multi_agent_implementation_prompt.md",
    "docs/progress.md", "docs/briefs/M6.md", "docs/lifecycle-admission.md",
    "docs/interfaces.md", "src/feature_rl/artifacts/store.py",
    "src/feature_rl/contracts/models.py", "docs/reports/M0.md",
    "docs/reviews/M0-round1.md", "docs/reviews/M0-provenance-v2.md", ".gitignore",
)
POINTS = (
    "before_attempt_commit", "after_attempt_commit", "after_start_projection",
    "after_work_before_observation", "after_observation_commit",
    "after_artifact_temp_fsync", "after_artifact_link_before_dir_fsync",
    "after_artifact_publication", "inside_completion_transaction",
    "after_completion_commit", "during_jsonl_append_prefix",
    "after_jsonl_append_before_fsync", "after_jsonl_fsync_before_ack",
    "inside_ack_transaction", "after_ack_before_reply", "clean",
)


def now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def stream(data):
    return {"bytes": len(data), "sha256": sha(data),
            "base64": base64.b64encode(data).decode()}


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_all(fd, data):
    while data:
        count = os.write(fd, data)
        if count <= 0:
            raise OSError("write made no progress")
        data = data[count:]


def crash(selected, point):
    if selected == point:
        os.write(2, ("DIAGNOSTIC_PROCESS_EXIT_AT=" + point + "\n").encode())
        os._exit(91)  # Abrupt process exit only: kernel/storage stay running.


def connect(path):
    # Explicit SQL transactions, including on Python 3.11. No executescript in a tx.
    con = sqlite3.connect(path / "index.sqlite3", isolation_level=None, timeout=1)
    if sys.version_info >= (3, 12):
        con.autocommit = sqlite3.LEGACY_TRANSACTION_CONTROL
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute("PRAGMA synchronous=EXTRA")
    con.execute("PRAGMA fullfsync=ON")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=1000")
    return con


def initialize(path):
    path.mkdir(mode=0o700)
    (path / "objects").mkdir(mode=0o700)
    fd = os.open(path / "events.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.fsync(fd)
    os.close(fd)
    con = connect(path)
    con.executescript("""
        CREATE TABLE events(seq INTEGER PRIMARY KEY, event_key TEXT UNIQUE NOT NULL,
                            body BLOB NOT NULL);
        CREATE TABLE jobs(job_id TEXT PRIMARY KEY, manifest BLOB NOT NULL,
                          result_digest TEXT, selected_attempt TEXT);
        CREATE TABLE attempts(attempt_id TEXT PRIMARY KEY,
                              job_id TEXT NOT NULL REFERENCES jobs(job_id));
        CREATE TABLE projection(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                                acknowledged_seq INTEGER NOT NULL);
        INSERT INTO projection VALUES(1, 0);
    """)
    sync_dir(path)
    sync_dir(path.parent)
    return con


def event(con, key, kind, payload):
    prior = con.execute("SELECT seq, body FROM events WHERE event_key=?", (key,)).fetchone()
    seq = prior[0] if prior else con.execute("SELECT coalesce(max(seq), 0)+1 FROM events").fetchone()[0]
    body = encoded({"seq": seq, "key": key, "kind": kind, "scope": SCOPE, "payload": payload}) + b"\n"
    if prior:
        if prior[1] != body:
            raise ValueError("conflicting reuse of immutable event key")
    else:
        con.execute("INSERT INTO events VALUES(?, ?, ?)", (seq, key, body))
    return seq


def project(con, path, selected=""):
    """Serialize all compliant writers with BEGIN IMMEDIATE; compare exact bytes.

    A partial tail is completed ONLY when it is an exact prefix of the next
    committed outbox line and lies after the acknowledged prefix. No truncation.
    Any other disagreement raises before touching the log or acknowledgement.
    """
    con.execute("BEGIN IMMEDIATE")
    try:
        rows = con.execute("SELECT seq, body FROM events ORDER BY seq").fetchall()
        if [row[0] for row in rows] != list(range(1, len(rows) + 1)):
            raise ValueError("outbox sequence gap")
        expected = [row[1] for row in rows]
        ack = con.execute("SELECT acknowledged_seq FROM projection").fetchone()[0]
        original = (path / "events.jsonl").read_bytes()
        pieces = original.split(b"\n")
        complete, tail = [part + b"\n" for part in pieces[:-1]], pieces[-1]
        if ack > len(complete) or ack > len(expected):
            raise ValueError("acknowledged journal prefix missing; preserve and block")
        if len(complete) > len(expected) or complete != expected[:len(complete)]:
            raise ValueError("journal differs from authoritative outbox; preserve and block")
        if tail and (len(complete) == len(expected) or not expected[len(complete)].startswith(tail)):
            raise ValueError("unknown or corrupt incomplete tail; preserve and block")
        suffix = b"".join(expected[len(complete):])[len(tail):]
        fd = os.open(path / "events.jsonl", os.O_WRONLY | os.O_APPEND)
        try:
            if selected == "during_jsonl_append_prefix" and suffix:
                first_remaining = expected[len(complete)][len(tail):]
                write_all(fd, first_remaining[:max(1, len(first_remaining) // 2)])
                crash(selected, selected)
            write_all(fd, suffix)
            crash(selected, "after_jsonl_append_before_fsync")
            # Always fsync even if the previous process appended complete lines.
            os.fsync(fd)
            crash(selected, "after_jsonl_fsync_before_ack")
        finally:
            os.close(fd)
        con.execute("UPDATE projection SET acknowledged_seq=?", (len(expected),))
        crash(selected, "inside_ack_transaction")
        con.execute("COMMIT")
        crash(selected, "after_ack_before_reply")
        return {"old_ack": ack, "new_ack": len(expected), "original": stream(original),
                "tail_bytes_completed": len(tail), "appended_bytes": len(suffix),
                "final": stream((path / "events.jsonl").read_bytes())}
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise


MANIFEST = encoded({"scope": SCOPE, "operation": "inert_durability_probe", "version": 1})
JOB = sha(MANIFEST)
ARTIFACT = encoded({"scope": SCOPE, "inert_payload": "publication bytes only"})


def start(con, path, attempt, selected=""):
    con.execute("BEGIN IMMEDIATE")
    con.execute("INSERT INTO jobs(job_id, manifest) VALUES(?, ?) ON CONFLICT(job_id) DO NOTHING", (JOB, MANIFEST))
    con.execute("INSERT INTO attempts VALUES(?, ?)", (attempt, JOB))
    event(con, attempt + "/start", "attempt_started", {
        "job_id": JOB, "attempt_id": attempt,
        "cost": {"measurement": "unknown", "wall_seconds": None, "cpu_seconds": None,
                 "gpu_seconds": None, "input_tokens": None, "output_tokens": None,
                 "human_minutes": None, "usd": None,
                 "note": "Launch intent only; work and its cost may be unknown after interruption."}})
    crash(selected, "before_attempt_commit")
    con.execute("COMMIT")
    crash(selected, "after_attempt_commit")
    project(con, path)
    crash(selected, "after_start_projection")


def observe_work(con, attempt, selected=""):
    wall, cpu = time.perf_counter(), time.process_time()
    result = sha(b"trusted diagnostic hashing only" * 1024)
    cpu_elapsed, wall_elapsed = time.process_time() - cpu, time.perf_counter() - wall
    crash(selected, "after_work_before_observation")
    con.execute("BEGIN IMMEDIATE")
    event(con, attempt + "/observation", "attempt_observed", {
        "job_id": JOB, "attempt_id": attempt, "diagnostic_hash": result,
        "cost": {"measurement": "partial", "wall_seconds": wall_elapsed,
                 "cpu_seconds": cpu_elapsed, "gpu_seconds": None, "input_tokens": None,
                 "output_tokens": None, "human_minutes": None, "usd": None,
                 "note": "Measured local diagnostic hashing interval only; no task economics."}})
    con.execute("COMMIT")
    crash(selected, "after_observation_commit")


def publish(path, attempt, data, selected=""):
    directory = path / "objects"
    pending, final = directory / (".pending-" + attempt), directory / (sha(data) + ".json")
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    write_all(fd, data)
    os.fsync(fd)
    os.close(fd)
    crash(selected, "after_artifact_temp_fsync")
    try:
        os.link(pending, final)
    except FileExistsError:
        if final.read_bytes() != data:
            raise ValueError("immutable artifact conflict")
    crash(selected, "after_artifact_link_before_dir_fsync")
    sync_dir(directory)
    pending.unlink()
    sync_dir(directory)
    crash(selected, "after_artifact_publication")
    return sha(data)


def complete(con, path, attempt, digest, selected=""):
    if sha((path / "objects" / (digest + ".json")).read_bytes()) != digest:
        raise ValueError("artifact integrity failure")
    con.execute("BEGIN IMMEDIATE")
    prior = con.execute("SELECT result_digest FROM jobs WHERE job_id=?", (JOB,)).fetchone()[0]
    decision = "selected" if prior is None else ("same_output" if prior == digest else "conflict")
    prior_event = con.execute("SELECT body FROM events WHERE event_key=?", (attempt + "/completion",)).fetchone()
    if prior_event:
        old = json.loads(prior_event[0])["payload"]
        if old["result_digest"] != digest:
            raise ValueError("conflicting same-attempt completion")
        decision = old["decision"]
    event(con, attempt + "/completion", "attempt_completed", {
        "job_id": JOB, "attempt_id": attempt, "result_digest": digest,
        "decision": decision, "real_operation_result": False})
    if prior is None:
        con.execute("UPDATE jobs SET result_digest=?, selected_attempt=? WHERE job_id=? AND result_digest IS NULL", (digest, attempt, JOB))
    crash(selected, "inside_completion_transaction")
    con.execute("COMMIT")
    crash(selected, "after_completion_commit")
    return decision


def snapshot(con, path):
    return {
        "integrity_check": con.execute("PRAGMA integrity_check").fetchall(),
        "foreign_key_check": con.execute("PRAGMA foreign_key_check").fetchall(),
        "pragmas": {name: con.execute("PRAGMA " + name).fetchone()[0]
                    for name in ("journal_mode", "synchronous", "fullfsync", "foreign_keys", "busy_timeout")},
        "events": [json.loads(row[0]) for row in con.execute("SELECT body FROM events ORDER BY seq")],
        "attempts": con.execute("SELECT attempt_id, job_id FROM attempts ORDER BY attempt_id").fetchall(),
        "jobs": con.execute("SELECT job_id, result_digest, selected_attempt FROM jobs").fetchall(),
        "ack": con.execute("SELECT acknowledged_seq FROM projection").fetchone()[0],
        "journal": stream((path / "events.jsonl").read_bytes()),
        "objects": {p.name: stream(p.read_bytes()) for p in sorted((path / "objects").iterdir())},
    }


def child(path, selected):
    con = initialize(path)
    start(con, path, "diagnostic-attempt-1", selected)
    observe_work(con, "diagnostic-attempt-1", selected)
    digest = publish(path, "diagnostic-attempt-1", ARTIFACT, selected)
    complete(con, path, "diagnostic-attempt-1", digest, selected)
    project(con, path, selected)
    con.close()
    print(json.dumps({"scope": SCOPE, "diagnostic_result": digest}))


def run(argv):
    started, wall = now(), time.perf_counter()
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, timeout=15)
    return {"argv": argv, "started_at": started, "ended_at": now(),
            "wall_seconds": time.perf_counter() - wall, "exit_status": proc.returncode,
            "stdout": stream(proc.stdout), "stderr": stream(proc.stderr)}


def capture_inputs():
    records = []
    for name in INPUTS:
        before = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
        data = (ROOT / name).read_bytes()
        captured_at = now()
        after = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
        committed_blob = subprocess.check_output(["git", "rev-parse", before + ":" + name], cwd=ROOT).decode().strip()
        blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        target = STATE / "inputs" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        records.append({"path": name, "captured_at": captured_at,
                        "head_before": before, "head_after": after, "bytes": len(data),
                        "sha256": sha(data), "captured_git_blob": blob,
                        "blob_at_head_before": committed_blob,
                        "matches_head_before": blob == committed_blob,
                        "snapshot_path": str(target.relative_to(ROOT))})
    return records


def main():
    if RECEIPT.exists():
        raise FileExistsError("Never overwrite a prior raw receipt")
    if subprocess.run(["git", "check-ignore", "--quiet", str(STATE)], cwd=ROOT).returncode:
        raise RuntimeError("Research state must be ignored before creation")
    STATE.mkdir(parents=True, exist_ok=False, mode=0o700)
    begin, elapsed = now(), time.perf_counter()
    receipt = {"scope": SCOPE, "started_at": begin, "script": stream(Path(__file__).read_bytes()),
               "command": [sys.executable, str(Path(__file__).resolve())], "state": str(STATE),
               "inputs": capture_inputs(), "python": sys.version, "executable": sys.executable,
               "sqlite_runtime": sqlite3.sqlite_version, "platform": platform.platform(),
               "cases": [], "negative_cases": [], "checks": []}
    check = lambda label, ok: receipt["checks"].append({"label": label, "passed": bool(ok)})
    memory = sqlite3.connect(":memory:")
    receipt["sqlite_source_id"] = memory.execute("SELECT sqlite_source_id()").fetchone()[0]
    receipt["sqlite_compile_options"] = [row[0] for row in memory.execute("PRAGMA compile_options")]
    memory.close()
    receipt["df"] = run(["/bin/df", "-P", str(STATE)])
    df_text = base64.b64decode(receipt["df"]["stdout"]["base64"]).decode()
    device = df_text.splitlines()[-1].split()[0]
    receipt["diskutil"] = run(["/usr/sbin/diskutil", "info", "-plist", device])
    if receipt["diskutil"]["exit_status"] == 0:
        disk = plistlib.loads(base64.b64decode(receipt["diskutil"]["stdout"]["base64"]))
        receipt["filesystem"] = {key: disk.get(key) for key in (
            "FilesystemType", "FileSystemPersonality", "MountPoint", "DeviceIdentifier",
            "Internal", "SolidState", "ReadOnlyVolume", "VolumeName")}
    receipt["stat"] = {"st_dev": STATE.stat().st_dev, "f_bsize": os.statvfs(STATE).f_bsize,
                       "f_frsize": os.statvfs(STATE).f_frsize, "f_bavail": os.statvfs(STATE).f_bavail}
    for point in POINTS:
        path = STATE / point
        command = run([sys.executable, str(Path(__file__).resolve()), "--child", str(path), point])
        con = connect(path)
        before = snapshot(con, path)
        recovery = project(con, path)
        after = snapshot(con, path)
        again = project(con, path)
        check(point + "/expected_exit", command["exit_status"] == (0 if point == "clean" else 91))
        check(point + "/sqlite_integrity", after["integrity_check"] == [("ok",)] and not after["foreign_key_check"])
        check(point + "/outbox_unchanged", before["events"] == after["events"])
        check(point + "/index_unchanged", before["jobs"] == after["jobs"] and before["attempts"] == after["attempts"])
        check(point + "/projection_exact", base64.b64decode(after["journal"]["base64"]) == b"".join(encoded(e) + b"\n" for e in after["events"]))
        check(point + "/repeat_no_append", again["appended_bytes"] == 0 and again["final"] == recovery["final"])
        check(point + "/objects_preserved", before["objects"] == after["objects"])
        check(point + "/ack_matches_events", after["ack"] == len(after["events"]))
        position = POINTS.index(point)
        expected_events = 0 if position == 0 else (1 if position <= 3 else (2 if position <= 8 else 3))
        expected_result_count = 0 if position <= 8 else 1
        check(point + "/crash_boundary_state", len(after["events"]) == expected_events
              and len(after["attempts"]) == (0 if position == 0 else 1)
              and sum(job[1] is not None for job in after["jobs"]) == expected_result_count)
        check(point + "/artifact_boundary_state",
              sum(not name.startswith(".pending-") for name in after["objects"]) == (1 if position >= 6 else 0)
              and sum(name.startswith(".pending-") for name in after["objects"]) == (1 if position in (5, 6) else 0))
        receipt["cases"].append({"point": point, "command": command, "before": before,
                                 "recovery": recovery, "after": after, "repeat": again})
        con.close()
    # Completion replay is distinct from executing another measured attempt.
    path = STATE / "clean"
    con = connect(path)
    before = snapshot(con, path)
    same_decision = complete(con, path, "diagnostic-attempt-1", sha(ARTIFACT))
    project(con, path)
    replay = snapshot(con, path)
    check("same_attempt_replay_no_event_or_cost", before == replay)
    start(con, path, "diagnostic-attempt-2")
    observe_work(con, "diagnostic-attempt-2")
    digest = publish(path, "diagnostic-attempt-2", ARTIFACT)
    same_output_decision = complete(con, path, "diagnostic-attempt-2", digest)
    project(con, path)
    second = snapshot(con, path)
    check("distinct_attempt_same_output_keeps_both_costs", len(second["attempts"]) == 2 and sum(e["kind"] == "attempt_observed" for e in second["events"]) == 2)
    check("distinct_attempt_single_selected_output", before["jobs"] == second["jobs"] and same_output_decision == "same_output")
    start(con, path, "diagnostic-attempt-3")
    observe_work(con, "diagnostic-attempt-3")
    conflicting = publish(path, "diagnostic-attempt-3", encoded({"scope": SCOPE, "conflicting": True}))
    conflict_decision = complete(con, path, "diagnostic-attempt-3", conflicting)
    project(con, path)
    conflict = snapshot(con, path)
    check("conflict_preserves_selected_result_and_costs", conflict["jobs"] == before["jobs"] and conflict_decision == "conflict" and sum(e["kind"] == "attempt_observed" for e in conflict["events"]) == 3)
    receipt["idempotence"] = {"before": before, "same_attempt_decision": same_decision,
                              "same_attempt_replay": replay, "distinct_same_output": second,
                              "distinct_conflicting_output": conflict}
    con.close()
    for name in ("corrupt_partial_tail", "unknown_complete_suffix", "acknowledged_tail_lost"):
        path = STATE / name
        point = "after_completion_commit" if name == "corrupt_partial_tail" else "clean"
        command = run([sys.executable, str(Path(__file__).resolve()), "--child", str(path), point])
        con = connect(path)
        original = (path / "events.jsonl").read_bytes()
        if name == "corrupt_partial_tail":
            injected = original + b'{"UNRECOGNIZED_DIAGNOSTIC_TAIL":'
        elif name == "unknown_complete_suffix":
            injected = original + b'{"UNCOMMITTED_DIAGNOSTIC_EVENT":true}\n'
        else:
            injected = original[:-20]  # Explicit loss injection into NEW diagnostic state only.
        (path / "events.jsonl").write_bytes(injected)
        before = snapshot(con, path)
        error = None
        try:
            project(con, path)
        except ValueError as exc:
            error = str(exc)
        after = snapshot(con, path)
        check(name + "/refused_and_preserved", error is not None and before == after)
        receipt["negative_cases"].append({"name": name, "command": command,
                                           "uninjected_journal": stream(original), "before": before,
                                           "error": error, "after": after})
        con.close()
    check("unknown_work_gap_retained", next(c for c in receipt["cases"] if c["point"] == "after_work_before_observation")["after"]["events"][0]["payload"]["cost"]["measurement"] == "unknown")
    check("partial_append_observed_and_completed", next(c for c in receipt["cases"] if c["point"] == "during_jsonl_append_prefix")["recovery"]["tail_bytes_completed"] > 0)
    receipt["finished_at"] = now()
    receipt["driver_wall_seconds"] = time.perf_counter() - elapsed
    receipt["subprocess_wall_seconds_sum"] = sum(c["command"]["wall_seconds"] for c in receipt["cases"] + receipt["negative_cases"])
    receipt["unmeasured_cost_fields"] = ["total_cpu_seconds", "gpu_seconds", "input_tokens", "output_tokens", "human_minutes", "usd"]
    receipt["head_at_finish"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    receipt["input_observations_at_finish"] = [{"path": record["path"], "sha256": sha((ROOT / record["path"]).read_bytes()),
                                              "matches_captured": sha((ROOT / record["path"]).read_bytes()) == record["sha256"]}
                                             for record in receipt["inputs"]]
    receipt["check_count"] = len(receipt["checks"])
    receipt["passed"] = all(item["passed"] for item in receipt["checks"])
    with RECEIPT.open("xb") as out:
        out.write(encoded(receipt) + b"\n")
        out.flush()
        os.fsync(out.fileno())
    print(json.dumps({"scope": SCOPE, "receipt": str(RECEIPT.relative_to(ROOT)),
                      "sha256": sha(RECEIPT.read_bytes()), "checks": len(receipt["checks"]),
                      "passed": receipt["passed"], "wall_seconds": receipt["driver_wall_seconds"]}))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--child":
        owned = Path(sys.argv[2]).resolve()
        if owned.parent != STATE.resolve() or sys.argv[3] not in POINTS:
            raise ValueError("child path/crash point must be task-private")
        child(owned, sys.argv[3])
    elif len(sys.argv) == 1:
        raise SystemExit(main())
    else:
        raise ValueError("unsupported diagnostic invocation")

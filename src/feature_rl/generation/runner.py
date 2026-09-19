"""Fresh-process execution with explicit kernel and sampled resource bounds."""

from __future__ import annotations

import ctypes
import math
import os
import resource
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .models import GenerationLimits


class _RUsageInfoV4(ctypes.Structure):
    _fields_ = [("uuid", ctypes.c_uint8 * 16), ("values", ctypes.c_uint64 * 35)]


@dataclass(frozen=True)
class FootprintSample:
    wired_bytes: int
    resident_bytes: int
    physical_footprint_bytes: int
    lifetime_max_physical_footprint_bytes: int


@dataclass(frozen=True)
class ProcessOutcome:
    termination: str
    exit_status: int
    wall_seconds: float
    cpu_seconds: float
    stdout: bytes
    stderr: bytes
    memory_samples: int
    max_sampled_physical_footprint_bytes: int | None
    max_reported_lifetime_physical_footprint_bytes: int | None
    breach_sample: FootprintSample | None
    process_group_cleanup_verified: bool
    cpu_limit_enforcement: str = "kernel_rlimit_cpu"
    file_size_limit_enforcement: str = "kernel_rlimit_fsize"
    memory_limit_enforcement: str = "sampled_proc_pid_rusage_20ms"


class ProcessBoundaryError(RuntimeError):
    pass


def _libproc():
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    except OSError as error:
        raise ProcessBoundaryError("macOS proc_pid_rusage is unavailable") from error
    library.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    library.proc_pid_rusage.restype = ctypes.c_int
    return library


def _footprint(library, pid: int) -> FootprintSample | None:
    info = _RUsageInfoV4()
    if library.proc_pid_rusage(pid, 4, ctypes.byref(info)) != 0:
        return None
    return FootprintSample(
        wired_bytes=int(info.values[5]),
        resident_bytes=int(info.values[6]),
        physical_footprint_bytes=int(info.values[7]),
        lifetime_max_physical_footprint_bytes=int(info.values[28]),
    )


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _group_absent(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _child_limits(cpu_seconds: int, file_size_bytes: int) -> None:
    os.setsid()
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (file_size_bytes, file_size_bytes))


class BoundedProcessRunner:
    """Run one command in a new process group and fail closed on monitor loss."""

    def run(
        self,
        *,
        command: Sequence[str],
        stdin: bytes,
        cwd: Path,
        environment: Mapping[str, str],
        limits: GenerationLimits,
    ) -> ProcessOutcome:
        if not command or any(not isinstance(value, str) or not value for value in command):
            raise ProcessBoundaryError("command must contain non-empty strings")
        if len(stdin) > limits.stdin_bytes:
            raise ProcessBoundaryError("stdin exceeds the configured byte cap")
        if not cwd.is_dir():
            raise ProcessBoundaryError("worker cwd must be an existing directory")
        library = _libproc()
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="feature-rl-run-", dir=cwd) as capture:
            capture_path = Path(capture)
            stdin_path = capture_path / "stdin"
            stdout_path = capture_path / "stdout"
            stderr_path = capture_path / "stderr"
            stdin_path.write_bytes(stdin)
            with stdin_path.open("rb") as input_stream, stdout_path.open("wb") as stdout_stream, stderr_path.open("wb") as stderr_stream:
                process = subprocess.Popen(
                    tuple(command),
                    stdin=input_stream,
                    stdout=stdout_stream,
                    stderr=stderr_stream,
                    cwd=cwd,
                    env=dict(environment),
                    close_fds=True,
                    preexec_fn=lambda: _child_limits(limits.cpu_seconds, limits.file_size_bytes),
                )
                termination = "process_exit"
                samples = 0
                failed_samples = 0
                max_physical = 0
                max_lifetime = 0
                breach: FootprintSample | None = None
                while process.poll() is None:
                    elapsed = time.monotonic() - started
                    if elapsed >= limits.wall_seconds:
                        termination = "deadline"
                        _kill_group(process.pid)
                        break
                    combined_size = stdout_path.stat().st_size + stderr_path.stat().st_size
                    if combined_size >= limits.output_bytes:
                        termination = "output_cap"
                        _kill_group(process.pid)
                        break
                    sample = _footprint(library, process.pid)
                    if sample is None:
                        failed_samples += 1
                        if failed_samples >= 3:
                            termination = "monitoring_failure"
                            _kill_group(process.pid)
                            break
                    else:
                        failed_samples = 0
                        samples += 1
                        max_physical = max(max_physical, sample.physical_footprint_bytes)
                        max_lifetime = max(
                            max_lifetime, sample.lifetime_max_physical_footprint_bytes
                        )
                        if sample.physical_footprint_bytes > limits.physical_footprint_kill_bytes:
                            breach = sample
                            termination = "memory_cap"
                            _kill_group(process.pid)
                            break
                    time.sleep(limits.physical_footprint_poll_seconds)
                try:
                    exit_status = process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    termination = "cleanup_failure"
                    _kill_group(process.pid)
                    exit_status = process.wait(timeout=2)
            if not _group_absent(process.pid):
                _kill_group(process.pid)
                time.sleep(0.05)
            cleanup = _group_absent(process.pid)
            stdout = stdout_path.read_bytes()
            stderr = stderr_path.read_bytes()
            combined = len(stdout) + len(stderr)
            if combined > limits.output_bytes:
                excess = combined - limits.output_bytes
                if excess <= len(stderr):
                    stderr = stderr[:-excess] if excess else stderr
                else:
                    stdout = stdout[: limits.output_bytes]
                    stderr = b""
                termination = "output_cap"
            if exit_status == -signal.SIGXFSZ:
                termination = "output_cap"
            elif exit_status == -signal.SIGXCPU:
                termination = "cpu_cap"
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        cpu = max(
            0.0,
            (after.ru_utime + after.ru_stime) - (before.ru_utime + before.ru_stime),
        )
        return ProcessOutcome(
            termination=termination,
            exit_status=exit_status,
            wall_seconds=time.monotonic() - started,
            cpu_seconds=cpu,
            stdout=stdout,
            stderr=stderr,
            memory_samples=samples,
            max_sampled_physical_footprint_bytes=max_physical if samples else None,
            max_reported_lifetime_physical_footprint_bytes=max_lifetime if samples else None,
            breach_sample=breach,
            process_group_cleanup_verified=cleanup,
        )

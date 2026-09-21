"""Codex process execution with CPU, output, deadline and sampled memory bounds."""

from __future__ import annotations

import ctypes
import os
import platform
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
    wired_bytes: int | None
    resident_bytes: int
    physical_footprint_bytes: int | None
    lifetime_max_physical_footprint_bytes: int | None


@dataclass(frozen=True)
class ProcessOutcome:
    termination: str
    exit_status: int | None
    wall_seconds: float
    cpu_seconds: float
    stdout: bytes
    stderr: bytes
    memory_samples: int
    max_sampled_physical_footprint_bytes: int | None
    max_reported_lifetime_physical_footprint_bytes: int | None
    breach_sample: FootprintSample | None
    process_group_cleanup_verified: bool
    monitoring_failures: int = 0
    monitor_error_type: str | None = None
    monitor_error: str | None = None
    cleanup_error: str | None = None
    cpu_limit_enforcement: str = "kernel_rlimit_cpu"
    output_limit_enforcement: str = "captured_output_and_final_response"
    memory_limit_enforcement: str = "sampled_process_memory"
    max_sampled_resident_bytes: int | None = None


class ProcessBoundaryError(RuntimeError):
    pass


def _libproc():
    if platform.system() == "Linux":
        return None
    if platform.system() != "Darwin":
        raise ProcessBoundaryError("process memory monitoring requires Linux or macOS")
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    except OSError as error:
        raise ProcessBoundaryError("macOS proc_pid_rusage is unavailable") from error
    library.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    library.proc_pid_rusage.restype = ctypes.c_int
    return library


def _footprint(library, pid: int) -> FootprintSample | None:
    if library is None:
        try:
            status = Path(f"/proc/{pid}/status").read_text()
        except FileNotFoundError:
            return None
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                _, value, unit = line.split()
                if unit != "kB":
                    raise ProcessBoundaryError("unexpected Linux RSS unit")
                return FootprintSample(None, int(value) * 1024, None, None)
        return None
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
    except OSError:
        pass


def _group_absent(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def _child_limits(cpu_seconds: int) -> None:
    os.setsid()
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))


class BoundedProcessRunner:
    """Run one command in a new process group and fail closed on monitor loss."""

    def run(
        self,
        *,
        command: Sequence[str],
        stdin: bytes,
        cwd: Path,
        response_path: Path,
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
                    preexec_fn=lambda: _child_limits(limits.cpu_seconds),
                )
                termination = "process_exit"
                samples = 0
                monitoring_failures = 0
                max_physical = 0
                max_lifetime = 0
                max_resident = 0
                breach: FootprintSample | None = None
                monitor_error_type: str | None = None
                monitor_error: str | None = None
                cleanup_error: str | None = None
                exit_status: int | None = None
                try:
                    while process.poll() is None:
                        elapsed = time.monotonic() - started
                        if elapsed >= limits.wall_seconds:
                            termination = "deadline"
                            break
                        combined_size = stdout_path.stat().st_size + stderr_path.stat().st_size
                        response_size = response_path.stat().st_size if response_path.exists() else 0
                        if max(combined_size, response_size) > limits.output_bytes:
                            termination = "output_cap"
                            break
                        sample = _footprint(library, process.pid)
                        if sample is None:
                            if process.poll() is not None:
                                break
                            monitoring_failures += 1
                            monitor_error_type = "ObservationUnavailable"
                            monitor_error = "process memory monitor returned no observation"
                            termination = "monitoring_failure"
                            break
                        samples += 1
                        max_resident = max(max_resident, sample.resident_bytes)
                        max_physical = max(max_physical, sample.physical_footprint_bytes or 0)
                        max_lifetime = max(
                            max_lifetime, sample.lifetime_max_physical_footprint_bytes or 0
                        )
                        memory_bytes = (sample.physical_footprint_bytes if library is not None
                                        else sample.resident_bytes)
                        if memory_bytes > limits.physical_footprint_kill_bytes:
                            breach = sample
                            termination = "memory_cap"
                            break
                        time.sleep(limits.physical_footprint_poll_seconds)
                except BaseException as caught:
                    monitoring_failures += 1
                    monitor_error_type = type(caught).__name__
                    monitor_error = str(caught)
                    termination = (
                        "interrupted" if isinstance(caught, (KeyboardInterrupt, SystemExit))
                        else "monitoring_failure"
                    )
                finally:
                    _kill_group(process.pid)
                    try:
                        exit_status = process.wait(timeout=2)
                    except BaseException as caught:
                        cleanup_error = f"{type(caught).__name__}: {caught}"
                        termination = "cleanup_failure"
                        try:
                            process.kill()
                            exit_status = process.wait(timeout=2)
                        except BaseException as final_error:
                            cleanup_error += f"; final cleanup: {type(final_error).__name__}: {final_error}"
            if not _group_absent(process.pid):
                _kill_group(process.pid)
                time.sleep(0.05)
            cleanup = _group_absent(process.pid)
            if not cleanup and cleanup_error is None:
                cleanup_error = "process group still present after bounded cleanup"
                termination = "cleanup_failure"
            try:
                with stdout_path.open("rb") as stream:
                    stdout = stream.read(limits.output_bytes + 1)
                with stderr_path.open("rb") as stream:
                    stderr = stream.read(limits.output_bytes + 1)
            except BaseException as caught:
                stdout = b""
                stderr = b""
                monitoring_failures += 1
                monitor_error_type = type(caught).__name__
                monitor_error = str(caught)
                termination = "monitoring_failure"
            combined = len(stdout) + len(stderr)
            if combined > limits.output_bytes:
                excess = combined - limits.output_bytes
                if excess <= len(stderr):
                    stderr = stderr[:-excess] if excess else stderr
                else:
                    stdout = stdout[: limits.output_bytes]
                    stderr = b""
                termination = "output_cap"
            if response_path.exists() and response_path.stat().st_size > limits.output_bytes:
                termination = "output_cap"
            if exit_status == -signal.SIGXCPU:
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
            max_sampled_physical_footprint_bytes=max_physical if samples and library is not None else None,
            max_reported_lifetime_physical_footprint_bytes=max_lifetime if samples and library is not None else None,
            breach_sample=breach,
            process_group_cleanup_verified=cleanup,
            monitoring_failures=monitoring_failures,
            monitor_error_type=monitor_error_type,
            monitor_error=monitor_error,
            cleanup_error=cleanup_error,
            max_sampled_resident_bytes=max_resident if samples else None,
            memory_limit_enforcement=("sampled_proc_pid_rusage" if library is not None else "sampled_linux_rss"),
        )

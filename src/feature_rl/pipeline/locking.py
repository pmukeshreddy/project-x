"""Ephemeral candidate exclusion; all durable state stays in the Registry/CAS."""
from contextlib import contextmanager
import fcntl
import os
import stat

from feature_rl.registry import Backpressure


@contextmanager
def candidate_lock(store, candidate):
    directory = store.root / '.m6-candidate-locks'
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError('unsafe candidate lock directory')
    descriptor = os.open(directory / (candidate.sha256+'.lock'), os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or info.st_mode & 0o022):
            raise ValueError('unsafe candidate lock file')
        try: fcntl.flock(descriptor, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Backpressure('candidate operation already active; no duplicate dispatch') from exc
        yield
    finally:
        os.close(descriptor)

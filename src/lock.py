import asyncio
import os
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path


@contextmanager
def file_lock(path: Path):
    """Exclusive lock. POSIX: fcntl.LOCK_EX. Windows: msvcrt.locking.
    Blocks until lock is granted. Creates the file if missing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name == "posix":
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        else:
            import msvcrt
            import time
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.01)
        yield
    finally:
        try:
            if os.name == "posix":
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
            else:
                import msvcrt
                try:
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        finally:
            os.close(fd)


@asynccontextmanager
async def async_file_lock(path: Path):
    """Async wrapper around file_lock.

    Acquires and releases the blocking OS lock in a worker thread via
    asyncio.to_thread, so holding it across `await` points (e.g. the create
    SSE stream) never blocks the event loop. Acquiring the blocking lock
    directly on the loop thread is what deadlocked the service: a second
    lifecycle request blocked the loop, so the first — suspended mid-stream
    while holding the lock — could never resume to release it. flock/msvcrt
    still provide cross-process AND in-process (per-fd) mutual exclusion."""
    cm = file_lock(path)
    await asyncio.to_thread(cm.__enter__)
    try:
        yield
    finally:
        await asyncio.to_thread(cm.__exit__, None, None, None)

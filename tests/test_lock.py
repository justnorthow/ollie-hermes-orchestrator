import asyncio
import threading
import time

from src.lock import file_lock


def test_lock_serializes_concurrent_writers(tmp_path):
    lock_path = tmp_path / ".lock"
    order = []

    def worker(label: str, hold_for: float) -> None:
        with file_lock(lock_path):
            order.append(f"enter-{label}")
            time.sleep(hold_for)
            order.append(f"exit-{label}")

    t1 = threading.Thread(target=worker, args=("A", 0.1))
    t2 = threading.Thread(target=worker, args=("B", 0.0))
    t1.start()
    time.sleep(0.05)
    t2.start()
    t1.join()
    t2.join()
    assert order.index("exit-A") < order.index("enter-B")


async def test_async_file_lock_serializes_without_blocking_loop(tmp_path):
    """async_file_lock must hold the OS lock across `await`s WITHOUT blocking
    the event loop. The lifecycle deadlock came from acquiring the blocking
    lock synchronously on the loop thread: a second request acquiring it froze
    the whole service so the first (suspended mid-stream) could never release."""
    from src.lock import async_file_lock

    lock_path = tmp_path / ".lock"
    order: list[str] = []

    async def worker(label: str):
        async with async_file_lock(lock_path):
            order.append(f"enter-{label}")
            await asyncio.sleep(0.05)  # hold the lock across an await point
            order.append(f"exit-{label}")

    async def canary():
        # If the loop were blocked while a worker held the lock, this could not
        # run until the worker finished. It must interleave during a hold.
        await asyncio.sleep(0.02)
        order.append("canary")

    await asyncio.gather(worker("A"), worker("B"), canary())

    # Mutual exclusion: one critical section fully precedes the other.
    assert (order.index("exit-A") < order.index("enter-B")
            or order.index("exit-B") < order.index("enter-A"))
    # Loop stayed responsive: canary ran before the last critical section ended.
    assert "canary" in order
    assert order.index("canary") < max(order.index("exit-A"), order.index("exit-B"))

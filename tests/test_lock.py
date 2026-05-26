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

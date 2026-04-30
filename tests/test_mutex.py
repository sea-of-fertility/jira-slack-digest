import threading
import time

from bot_lib.mutex import RepoMutex


def test_same_repo_returns_same_lock():
    m = RepoMutex()
    a1 = m.lock_for("repo")
    a2 = m.lock_for("repo")
    assert a1 is a2


def test_different_repos_get_different_locks():
    m = RepoMutex()
    assert m.lock_for("a") is not m.lock_for("b")


def test_same_repo_lock_actually_serializes():
    """A second acquirer must wait for the first to release."""
    m = RepoMutex()
    lock = m.lock_for("repo")

    order: list[str] = []
    started_first = threading.Event()
    let_first_finish = threading.Event()

    def first():
        with m.lock_for("repo"):
            started_first.set()
            order.append("first-acquired")
            let_first_finish.wait(timeout=2)
            order.append("first-releasing")

    def second():
        started_first.wait(timeout=2)
        # at this point, first holds the lock
        acquired = m.lock_for("repo").acquire(blocking=False)
        order.append(f"second-nb-acquired={acquired}")
        if acquired:
            m.lock_for("repo").release()
        with m.lock_for("repo"):
            order.append("second-blocking-acquired")

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    t2.start()
    started_first.wait(timeout=2)
    time.sleep(0.05)  # let second attempt non-blocking acquire
    let_first_finish.set()
    t1.join(timeout=2)
    t2.join(timeout=2)

    assert "second-nb-acquired=False" in order
    assert order.index("first-releasing") < order.index("second-blocking-acquired")

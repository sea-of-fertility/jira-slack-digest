import os
import signal
import subprocess
import threading
import time

import pytest

from bot_lib.cancellation import CancellationRegistry


def test_register_then_get_pid():
    r = CancellationRegistry()
    r.register("repo", 12345)
    assert r.get_pid("repo") == 12345


def test_unregister_clears_pid():
    r = CancellationRegistry()
    r.register("repo", 12345)
    r.unregister("repo")
    assert r.get_pid("repo") is None


def test_unregister_unknown_repo_is_safe():
    r = CancellationRegistry()
    r.unregister("never-registered")  # must not raise


def test_cancel_returns_none_when_unregistered():
    r = CancellationRegistry()
    assert r.cancel("repo") is None


def test_cancel_returns_none_when_process_dead():
    """A registered PID that no longer exists → cancel returns None cleanly."""
    r = CancellationRegistry()
    r.register("repo", 999_999_999)  # very unlikely to be a live PID
    assert r.cancel("repo") is None


def test_cancel_real_subprocess_terminates_it():
    """End-to-end: spawn a sleep, register its PID, cancel, verify it dies."""
    r = CancellationRegistry()
    proc = subprocess.Popen(
        ["python3", "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    r.register("repo", proc.pid)
    try:
        killed = r.cancel("repo")
        assert killed == proc.pid
        # Wait briefly for SIGTERM to land
        try:
            rc = proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            pytest.fail("subprocess did not exit after SIGTERM")
        assert rc != 0  # killed, not normal exit
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_concurrent_register_does_not_race():
    """Many threads registering different repos should all land cleanly."""
    r = CancellationRegistry()

    def writer(repo, pid):
        for _ in range(50):
            r.register(repo, pid)
            time.sleep(0)
            r.unregister(repo)

    threads = [
        threading.Thread(target=writer, args=(f"r{i}", 1000 + i)) for i in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # All unregistered at the end — every get_pid is None
    for i in range(8):
        assert r.get_pid(f"r{i}") is None

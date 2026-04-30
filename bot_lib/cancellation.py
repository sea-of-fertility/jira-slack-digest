"""Per-repo claude subprocess cancellation registry.

The job thread (running `claude -p` via Popen) registers its PID before
the call and unregisters when done. The Slack handler thread looks up
the PID and signals SIGTERM. Slack-bolt dispatches every event on its
own thread, so this registry is the cross-thread channel.

`claude -p` is launched with `start_new_session=True`, so its PID is
also its process-group leader — `os.killpg(pid, SIGTERM)` reaches the
whole group (insurance in case claude spawns children, even though
our tool deny-list blocks Bash).

Plan §12 Q7 originally banned cancel for "런타임 중단 위험"; the
existing dirty-check + cleanup + commit-only-on-success cover the
recovery path, so a narrow "kill claude only" cancel is safe.
"""
import os
import signal
import threading
from typing import Optional


class CancellationRegistry:
    def __init__(self):
        self._pids: dict[str, int] = {}
        self._lock = threading.Lock()

    def register(self, repo: str, pid: int) -> None:
        with self._lock:
            self._pids[repo] = pid

    def unregister(self, repo: str) -> None:
        with self._lock:
            self._pids.pop(repo, None)

    def get_pid(self, repo: str) -> Optional[int]:
        with self._lock:
            return self._pids.get(repo)

    def cancel(self, repo: str) -> Optional[int]:
        """SIGTERM the registered process group. Returns the PID killed,
        or None if no registered PID or the process already exited."""
        with self._lock:
            pid = self._pids.get(repo)
        if pid is None:
            return None
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return None
        return pid

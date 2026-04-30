import threading


class RepoMutex:
    """Per-repo lock so the same repo serializes (plan.md §12 Q8) while
    different repos run in parallel.

    Slack-bolt dispatches each event on its own thread, so a plain
    threading.Lock per repo is enough — no async state to manage.
    """

    def __init__(self):
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def lock_for(self, repo: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(repo)
            if lock is None:
                lock = threading.Lock()
                self._locks[repo] = lock
            return lock

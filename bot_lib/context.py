"""Persistent session context for plan-2.0 §B (init/<repo>/<remote>).

The bot stores at most one (repo, remote) pair on disk so that 3-token
Slack commands (`<type>/<issue>/<instruction>`) can resolve to a project
without thread dialogue (Plan §1.1 Invariant 4 stays intact — `init` is
a one-shot config-set, not a multi-turn flow).

Single user (Plan §2 비목표) → one slot, no per-user keying.
"""
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Context:
    repo: str
    remote: str
    branch: Optional[str] = None  # None → use projects.toml default_branch
    who: Optional[str] = None     # None → fall back to env BOT_USER, then no-namespace


class ContextStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self._lock = threading.Lock()

    def get(self) -> Optional[Context]:
        with self._lock:
            try:
                data = json.loads(self.path.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                return None
            repo = data.get("repo")
            remote = data.get("remote")
            if not repo or not remote:
                return None
            return Context(
                repo=repo,
                remote=remote,
                branch=data.get("branch"),  # may be None
                who=data.get("who"),
            )

    def set(self, ctx: Context) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            payload = {"repo": ctx.repo, "remote": ctx.remote}
            if ctx.branch:
                payload["branch"] = ctx.branch
            if ctx.who:
                payload["who"] = ctx.who
            tmp.write_text(json.dumps(payload))
            os.replace(tmp, self.path)

    def clear(self) -> None:
        with self._lock:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

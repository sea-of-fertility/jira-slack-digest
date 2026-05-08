import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

DEFAULT_DISALLOWED = ("Bash", "WebFetch", "WebSearch")
DEFAULT_MODEL = "sonnet"
DEFAULT_TIMEOUT = 600

CLAUDE_INSTALL_HINT = (
    "설치: https://docs.claude.com/en/docs/claude-code/quickstart"
)


def is_claude_available() -> bool:
    """Cheap PATH probe — does the `claude` binary exist on the runtime PATH?

    Doesn't actually exec it (auth / network errors only show up in the real
    `--print` call). Used at bot startup and as a runtime gate before the
    orchestrator spawns claude, so a missing CLI fails the user's `run`
    command with a clear Slack reply instead of a silent traceback.
    """
    return shutil.which("claude") is not None


class ClaudeError(Exception):
    """claude CLI not on PATH, timed out, or other invocation-level failure.

    A non-zero exit code from claude itself is NOT this — that surfaces in
    ClaudeRun.returncode so the orchestrator can decide whether to retry
    (plan.md §12 Q11).
    """


@dataclass(frozen=True)
class TokenUsage:
    """Token + cost figures pulled from `claude -p --output-format json`.

    Aggregates across retry attempts via __add__.
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    total_cost_usd: float = 0.0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens + other.cache_creation_input_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            total_cost_usd=self.total_cost_usd + other.total_cost_usd,
        )

    @property
    def total_input(self) -> int:
        return self.input_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens

    @property
    def is_empty(self) -> bool:
        return (
            self.input_tokens == 0
            and self.output_tokens == 0
            and self.cache_creation_input_tokens == 0
            and self.cache_read_input_tokens == 0
        )


@dataclass(frozen=True)
class ClaudeRun:
    stdout: str
    stderr: str
    returncode: int
    result: str
    session_id: Optional[str]
    raw_json: Optional[dict]
    usage: TokenUsage = field(default_factory=TokenUsage)


def _parse_usage(data: dict) -> TokenUsage:
    u = data.get("usage") or {}
    return TokenUsage(
        input_tokens=int(u.get("input_tokens", 0) or 0),
        output_tokens=int(u.get("output_tokens", 0) or 0),
        cache_creation_input_tokens=int(u.get("cache_creation_input_tokens", 0) or 0),
        cache_read_input_tokens=int(u.get("cache_read_input_tokens", 0) or 0),
        total_cost_usd=float(data.get("total_cost_usd", 0.0) or 0.0),
    )


def run_claude(
    cwd: str,
    prompt: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    resume: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    disallowed_tools: Sequence[str] = DEFAULT_DISALLOWED,
    on_start: Optional[Callable[[int], None]] = None,
    on_end: Optional[Callable[[], None]] = None,
) -> ClaudeRun:
    """Invoke `claude -p` with the bot's standard guardrails (plan.md §10-3).

    `on_start(pid)` fires after the subprocess is spawned; `on_end()` fires
    in `finally` after it terminates. The orchestrator uses these to plug
    the PID into CancellationRegistry so a Slack `cancel <repo>` can
    SIGTERM the running claude.

    `start_new_session=True` makes the spawned claude its own session
    leader, so a SIGTERM via `os.killpg(pid, ...)` reaches the whole
    process group — insurance against claude having children.
    """
    cmd = [
        "claude",
        "-p",
        "--output-format", "json",
        "--permission-mode", "acceptEdits",
        "--disallowedTools", " ".join(disallowed_tools),
        "--model", model,
    ]
    if resume:
        cmd += ["--resume", resume]
    cmd.append(prompt)

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError as e:
        raise ClaudeError("claude CLI not found on PATH") from e

    if on_start:
        on_start(proc.pid)

    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as e:
            proc.kill()
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            raise ClaudeError(f"claude timeout after {timeout}s") from e
    finally:
        if on_end:
            on_end()

    raw_json: Optional[dict] = None
    result = stdout or ""
    session_id: Optional[str] = None
    usage = TokenUsage()
    stripped = (stdout or "").strip()
    if stripped:
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            raw_json = data
            result = data.get("result", stdout)
            session_id = data.get("session_id")
            usage = _parse_usage(data)

    return ClaudeRun(
        stdout=stdout or "",
        stderr=stderr or "",
        returncode=proc.returncode,
        result=result,
        session_id=session_id,
        raw_json=raw_json,
        usage=usage,
    )

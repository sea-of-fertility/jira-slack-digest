import json
import subprocess
from dataclasses import dataclass
from typing import Optional, Sequence

DEFAULT_DISALLOWED = ("Bash", "WebFetch", "WebSearch")
DEFAULT_MODEL = "sonnet"
DEFAULT_TIMEOUT = 600


class ClaudeError(Exception):
    """claude CLI not on PATH, timed out, or other invocation-level failure.

    A non-zero exit code from claude itself is NOT this — that surfaces in
    ClaudeRun.returncode so the orchestrator can decide whether to retry
    (plan.md §12 Q11).
    """


@dataclass(frozen=True)
class ClaudeRun:
    stdout: str
    stderr: str
    returncode: int
    result: str
    session_id: Optional[str]
    raw_json: Optional[dict]


def run_claude(
    cwd: str,
    prompt: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    resume: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    disallowed_tools: Sequence[str] = DEFAULT_DISALLOWED,
) -> ClaudeRun:
    """Invoke `claude -p` with the bot's standard guardrails (plan.md §10-3).

    Returns a ClaudeRun. Use ClaudeRun.session_id to feed --resume on retry
    (§12 Q11c — caller falls back to stateless if resume fails).
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
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise ClaudeError(f"claude timeout after {timeout}s") from e
    except FileNotFoundError as e:
        raise ClaudeError("claude CLI not found on PATH") from e

    raw_json: Optional[dict] = None
    result = proc.stdout
    session_id: Optional[str] = None
    stripped = proc.stdout.strip()
    if stripped:
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            raw_json = data
            result = data.get("result", proc.stdout)
            session_id = data.get("session_id")

    return ClaudeRun(
        stdout=proc.stdout,
        stderr=proc.stderr,
        returncode=proc.returncode,
        result=result,
        session_id=session_id,
        raw_json=raw_json,
    )

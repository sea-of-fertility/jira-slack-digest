import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

PASS = "pass"
FAIL = "fail"
TIMEOUT = "timeout"
SKIP = "skip"


@dataclass(frozen=True)
class RunResult:
    status: str  # PASS / FAIL / TIMEOUT / SKIP
    stdout: str
    stderr: str
    returncode: Optional[int]
    duration_seconds: float


def run_tests(repo: str, cmd: Optional[str], timeout: int) -> RunResult:
    """Run a project's test command and classify the outcome.

    SKIP when cmd is None / empty (registry already maps '-' or blank to None).
    Never raises — every failure mode lands as a status; the orchestrator
    decides whether to retry (plan.md §12 Q11).
    """
    if not cmd or not cmd.strip():
        return RunResult(SKIP, "", "", None, 0.0)

    args = shlex.split(cmd)
    start = time.monotonic()
    try:
        proc = subprocess.run(
            args,
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        duration = time.monotonic() - start
        return RunResult(
            TIMEOUT,
            _decode(e.stdout),
            _decode(e.stderr),
            None,
            duration,
        )

    duration = time.monotonic() - start
    status = PASS if proc.returncode == 0 else FAIL
    return RunResult(status, proc.stdout, proc.stderr, proc.returncode, duration)


def _decode(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value

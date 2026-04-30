import pytest

from bot_lib.test_runner import FAIL, PASS, SKIP, TIMEOUT, RunResult, run_tests


# ---- skip ----


def test_skip_when_cmd_is_none(tmp_path):
    out = run_tests(str(tmp_path), None, timeout=10)
    assert isinstance(out, RunResult)
    assert out.status == SKIP
    assert out.returncode is None


def test_skip_when_cmd_is_empty(tmp_path):
    out = run_tests(str(tmp_path), "", timeout=10)
    assert out.status == SKIP


def test_skip_when_cmd_is_whitespace(tmp_path):
    out = run_tests(str(tmp_path), "   ", timeout=10)
    assert out.status == SKIP


# ---- pass ----


def test_passing_command(tmp_path):
    out = run_tests(str(tmp_path), "python3 -c pass", timeout=10)
    assert out.status == PASS
    assert out.returncode == 0


def test_pass_records_duration(tmp_path):
    out = run_tests(str(tmp_path), "python3 -c pass", timeout=10)
    assert out.duration_seconds > 0


# ---- fail ----


def test_failing_command_status(tmp_path):
    out = run_tests(str(tmp_path), "python3 -c 'raise SystemExit(2)'", timeout=10)
    assert out.status == FAIL
    assert out.returncode == 2


def test_failing_command_captures_stderr(tmp_path):
    out = run_tests(
        str(tmp_path),
        "python3 -c 'import sys; sys.exit(\"BOOM\")'",
        timeout=10,
    )
    assert out.status == FAIL
    assert "BOOM" in out.stderr


def test_failing_command_captures_stdout(tmp_path):
    out = run_tests(
        str(tmp_path),
        "python3 -c 'print(\"out-line\"); raise SystemExit(1)'",
        timeout=10,
    )
    assert out.status == FAIL
    assert "out-line" in out.stdout


# ---- timeout ----


def test_timeout_returns_timeout_status(tmp_path):
    out = run_tests(
        str(tmp_path),
        "python3 -c 'import time; time.sleep(5)'",
        timeout=1,
    )
    assert out.status == TIMEOUT


def test_timeout_records_duration(tmp_path):
    out = run_tests(
        str(tmp_path),
        "python3 -c 'import time; time.sleep(5)'",
        timeout=1,
    )
    assert out.duration_seconds >= 0.9  # roughly the timeout, allow jitter


# ---- cwd ----


def test_command_runs_in_repo_cwd(tmp_path):
    (tmp_path / "marker").write_text("data")
    out = run_tests(
        str(tmp_path),
        "python3 -c 'open(\"marker\").read()'",
        timeout=10,
    )
    assert out.status == PASS


# ---- shell-style cmd parsing ----


def test_complex_cmd_with_args(tmp_path):
    """Mirror real test_cmd from projects.md (./gradlew test --no-daemon)."""
    out = run_tests(
        str(tmp_path),
        "python3 -c 'import sys; print(len(sys.argv))' a b c",
        timeout=10,
    )
    assert out.status == PASS
    # 1 (the -c source) + 3 args = 4
    assert "4" in out.stdout

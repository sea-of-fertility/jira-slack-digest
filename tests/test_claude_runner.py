import json
import subprocess
from dataclasses import dataclass

import pytest

from bot_lib.claude_runner import ClaudeError, ClaudeRun, run_claude


@dataclass
class _FakeProc:
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


def _install_fake_run(monkeypatch, *, captured: dict, proc: _FakeProc, raise_exc=None):
    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        if raise_exc is not None:
            raise raise_exc
        return subprocess.CompletedProcess(
            args=cmd, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr
        )

    monkeypatch.setattr("bot_lib.claude_runner.subprocess.run", fake_run)


def _ok_json(result="done", session_id="s-123"):
    return json.dumps(
        {"type": "result", "subtype": "success", "result": result, "session_id": session_id}
    )


# ---- args / guardrails ----


def test_invokes_claude_p_with_acceptEdits(monkeypatch, tmp_path):
    captured = {}
    _install_fake_run(monkeypatch, captured=captured, proc=_FakeProc(stdout=_ok_json()))
    run_claude(str(tmp_path), "fix it")
    cmd = captured["cmd"]
    assert cmd[0] == "claude"
    assert "-p" in cmd
    i = cmd.index("--permission-mode")
    assert cmd[i + 1] == "acceptEdits"


def test_passes_disallowed_tools(monkeypatch, tmp_path):
    captured = {}
    _install_fake_run(monkeypatch, captured=captured, proc=_FakeProc(stdout=_ok_json()))
    run_claude(str(tmp_path), "x")
    cmd = captured["cmd"]
    i = cmd.index("--disallowedTools")
    # All three must appear in the value(s) following the flag
    blob = " ".join(cmd[i + 1 :])
    for tool in ("Bash", "WebFetch", "WebSearch"):
        assert tool in blob


def test_uses_sonnet_by_default(monkeypatch, tmp_path):
    captured = {}
    _install_fake_run(monkeypatch, captured=captured, proc=_FakeProc(stdout=_ok_json()))
    run_claude(str(tmp_path), "x")
    i = captured["cmd"].index("--model")
    assert captured["cmd"][i + 1] == "sonnet"


def test_runs_in_given_cwd(monkeypatch, tmp_path):
    captured = {}
    _install_fake_run(monkeypatch, captured=captured, proc=_FakeProc(stdout=_ok_json()))
    run_claude(str(tmp_path), "x")
    assert captured["kwargs"]["cwd"] == str(tmp_path)


def test_passes_timeout(monkeypatch, tmp_path):
    captured = {}
    _install_fake_run(monkeypatch, captured=captured, proc=_FakeProc(stdout=_ok_json()))
    run_claude(str(tmp_path), "x", timeout=42)
    assert captured["kwargs"]["timeout"] == 42


def test_default_timeout_is_600(monkeypatch, tmp_path):
    captured = {}
    _install_fake_run(monkeypatch, captured=captured, proc=_FakeProc(stdout=_ok_json()))
    run_claude(str(tmp_path), "x")
    assert captured["kwargs"]["timeout"] == 600


# ---- JSON output parsing ----


def test_parses_session_id_and_result(monkeypatch, tmp_path):
    captured = {}
    _install_fake_run(
        monkeypatch,
        captured=captured,
        proc=_FakeProc(stdout=_ok_json(result="ok!", session_id="ses-1")),
    )
    out = run_claude(str(tmp_path), "x")
    assert isinstance(out, ClaudeRun)
    assert out.session_id == "ses-1"
    assert out.result == "ok!"
    assert out.returncode == 0


def test_non_json_stdout_falls_back_to_raw(monkeypatch, tmp_path):
    captured = {}
    _install_fake_run(monkeypatch, captured=captured, proc=_FakeProc(stdout="plain text"))
    out = run_claude(str(tmp_path), "x")
    assert out.session_id is None
    assert out.result == "plain text"


# ---- resume ----


def test_resume_flag_passed_when_session_provided(monkeypatch, tmp_path):
    captured = {}
    _install_fake_run(monkeypatch, captured=captured, proc=_FakeProc(stdout=_ok_json()))
    run_claude(str(tmp_path), "x", resume="ses-99")
    cmd = captured["cmd"]
    assert "--resume" in cmd
    assert cmd[cmd.index("--resume") + 1] == "ses-99"


def test_no_resume_flag_when_session_omitted(monkeypatch, tmp_path):
    captured = {}
    _install_fake_run(monkeypatch, captured=captured, proc=_FakeProc(stdout=_ok_json()))
    run_claude(str(tmp_path), "x")
    assert "--resume" not in captured["cmd"]


# ---- error mapping ----


def test_timeout_raises_claude_error(monkeypatch, tmp_path):
    captured = {}
    _install_fake_run(
        monkeypatch,
        captured=captured,
        proc=_FakeProc(),
        raise_exc=subprocess.TimeoutExpired(cmd=["claude"], timeout=600),
    )
    with pytest.raises(ClaudeError, match="timeout"):
        run_claude(str(tmp_path), "x")


def test_missing_cli_raises_claude_error(monkeypatch, tmp_path):
    captured = {}
    _install_fake_run(
        monkeypatch,
        captured=captured,
        proc=_FakeProc(),
        raise_exc=FileNotFoundError("claude"),
    )
    with pytest.raises(ClaudeError, match="not found|PATH"):
        run_claude(str(tmp_path), "x")


def test_nonzero_exit_does_not_raise(monkeypatch, tmp_path):
    """returncode != 0 surfaces in result, not as exception — the orchestrator
    decides whether to retry (Q11)."""
    captured = {}
    _install_fake_run(
        monkeypatch, captured=captured, proc=_FakeProc(stdout="", stderr="boom", returncode=2)
    )
    out = run_claude(str(tmp_path), "x")
    assert out.returncode == 2


# ---- live (opt-in: pytest -m live) ----


@pytest.mark.live
def test_live_claude_smoke(tmp_path):
    """Real `claude -p` call. Verifies CLI args land, JSON parses, session_id
    is captured. Address §14 unknowns about --permission-mode + --disallowedTools.
    """
    out = run_claude(
        str(tmp_path),
        "Reply with exactly the word: ok",
        timeout=120,
    )
    assert out.returncode == 0, f"claude failed: {out.stderr}"
    assert out.session_id, "session_id must be present in JSON output"
    assert "ok" in out.result.lower()

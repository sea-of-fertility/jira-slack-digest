import subprocess
from pathlib import Path

import pytest

from bot_lib import claude_runner, jira_client
from bot_lib.commands import ParsedCmd
from bot_lib.orchestrator import SUCCESS, execute_job
from bot_lib.registry import Project


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )


@pytest.fixture
def project_with_remote(tmp_path):
    """A real local git repo + bare origin, returned as a registry.Project."""
    bare = tmp_path / "remote.git"
    bare.mkdir()
    _git(bare, "init", "--bare", "-b", "main")

    local = tmp_path / "local"
    local.mkdir()
    _git(local, "init", "-b", "main")
    _git(local, "config", "user.email", "t@x.com")
    _git(local, "config", "user.name", "t")
    (local / "README.md").write_text("hi\n")
    _git(local, "add", "-A")
    _git(local, "commit", "-m", "init")
    _git(local, "remote", "add", "origin", str(bare))
    _git(local, "push", "-u", "origin", "main")

    return Project(
        name="myrepo",
        path=str(local),
        default_branch="main",
        test_cmd="python3 -c pass",
        test_timeout=10,
    ), str(bare)


def _stub_fetch_issue(monkeypatch):
    issue = jira_client.JiraIssue(
        key="CDS-99",
        title="OAuth refresh fails",
        description="500 returned when refresh token expires.",
        comments_text="[alice] PR up at #341.",
    )
    monkeypatch.setattr(
        "bot_lib.orchestrator.jira_client.fetch_issue",
        lambda *a, **kw: issue,
    )
    return issue


def _stub_claude_edits_file(monkeypatch, edited_file: str = "EDITED.md"):
    """run_claude side effect: write a new file in cwd, return success JSON."""

    def fake_run(cwd, prompt, **kwargs):
        Path(cwd, edited_file).write_text("edited by claude\n")
        return claude_runner.ClaudeRun(
            stdout='{"result":"ok","session_id":"s-1"}',
            stderr="",
            returncode=0,
            result="ok",
            session_id="s-1",
            raw_json={"result": "ok", "session_id": "s-1"},
        )

    monkeypatch.setattr("bot_lib.orchestrator.claude_runner.run_claude", fake_run)


def _stub_gh_pr_create(monkeypatch, url: str = "https://github.com/x/y/pull/1"):
    """Replace subprocess.run only when invoked for gh pr create."""
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd and cmd[0] == "gh" and "pr" in cmd and "create" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=url + "\n", stderr="")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr("bot_lib.orchestrator.subprocess.run", fake_run)


def test_happy_path_complete(project_with_remote, monkeypatch):
    project, bare = project_with_remote
    _stub_fetch_issue(monkeypatch)
    _stub_claude_edits_file(monkeypatch)
    _stub_gh_pr_create(monkeypatch, url="https://github.com/x/y/pull/42")

    cmd = ParsedCmd(type="fix", repo="myrepo", issue="CDS-99", instruction="OAuth 수정")

    progress_log = []
    out = execute_job(
        cmd,
        project,
        jira_base_url="https://x.atlassian.net",
        jira_email="me@x.com",
        jira_token="tok",
        progress=progress_log.append,
    )

    # outcome
    assert out.status == SUCCESS
    assert out.branch == "fix/CDS-99"
    assert out.commit_sha and len(out.commit_sha) == 40
    assert "EDITED.md" in out.diff_stat
    assert out.test_status == "pass"
    assert out.pr_url == "https://github.com/x/y/pull/42"

    # repo state
    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%s%n%b"],
        cwd=project.path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "fix(CDS-99): OAuth refresh fails" in log
    assert "OAuth 수정" in log
    assert "Tests: PASS" in log

    # branch was pushed
    pushed = subprocess.run(
        ["git", "ls-remote", "--heads", bare, "fix/CDS-99"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "fix/CDS-99" in pushed

    # progress was relayed (Invariant 4 — interactive=A)
    assert any("claude" in m for m in progress_log)
    assert any("테스트" in m for m in progress_log)
    assert any("push" in m for m in progress_log)

import subprocess
from pathlib import Path

import pytest

from bot_lib import claude_runner, jira_client, test_runner
from bot_lib.commands import ParsedCmd
from bot_lib.orchestrator import FAILED, SUCCESS, execute_job
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
        default_branch="main", remote="origin",
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


# ---- self-repo guard ----


def test_self_repo_is_refused():
    """Job pointed at the bot's own dir → BLOCKED, no execution."""
    from bot_lib.orchestrator import BLOCKED, _BOT_DIR

    self_project = Project(
        name="self",
        path=str(_BOT_DIR),
        default_branch="main", remote="origin",
        test_cmd=None,
        test_timeout=10,
    )
    cmd = ParsedCmd(type="fix", repo="self", issue="CDS-1", instruction="x")
    out = execute_job(
        cmd, self_project, jira_base_url="u", jira_email="e", jira_token="t"
    )
    assert out.status == BLOCKED
    assert "self-modification" in out.message or "자기 자신" in out.message


def test_self_repo_check_resolves_symlinks(tmp_path):
    from bot_lib.orchestrator import BLOCKED, _BOT_DIR

    link = tmp_path / "linked"
    link.symlink_to(_BOT_DIR)
    project = Project(
        name="alias", path=str(link), default_branch="main", remote="origin",
        test_cmd=None, test_timeout=10,
    )
    cmd = ParsedCmd(type="fix", repo="alias", issue="CDS-1", instruction="x")
    out = execute_job(
        cmd, project, jira_base_url="u", jira_email="e", jira_token="t"
    )
    assert out.status == BLOCKED


# ---- cycle (b): retry loop ----


class _FakeClaude:
    """Replays a list of (file_writer, returncode, session_id) per call."""

    def __init__(self, plan):
        self.plan = list(plan)
        self.calls = []

    def __call__(self, cwd, prompt, **kwargs):
        self.calls.append({"cwd": cwd, "prompt": prompt, **kwargs})
        if not self.plan:
            raise AssertionError("FakeClaude called more times than planned")
        write_fn, rc, sid = self.plan.pop(0)
        if write_fn is not None:
            write_fn(Path(cwd))
        return claude_runner.ClaudeRun(
            stdout="",
            stderr="" if rc == 0 else f"claude failed rc={rc}",
            returncode=rc,
            result="ok" if rc == 0 else "",
            session_id=sid,
            raw_json=None,
        )


def _stub_test_results(monkeypatch, statuses):
    """Each statuses entry is one of pass/fail/timeout/skip; returned in order."""
    out = iter(statuses)

    def fake(repo, cmd, timeout):
        s = next(out)
        return test_runner.RunResult(
            status=s,
            stdout=f"out for {s}",
            stderr=f"err for {s}",
            returncode=0 if s == test_runner.PASS else 1,
            duration_seconds=0.01,
        )

    monkeypatch.setattr("bot_lib.orchestrator.test_runner.run_tests", fake)


def test_usage_aggregates_across_attempts(project_with_remote, monkeypatch):
    """orchestrator should sum claude_runner.usage from every attempt."""
    project, _ = project_with_remote
    _stub_fetch_issue(monkeypatch)
    _stub_gh_pr_create(monkeypatch)

    usages = [
        claude_runner.TokenUsage(input_tokens=1000, output_tokens=200, total_cost_usd=0.05),
        claude_runner.TokenUsage(input_tokens=500, output_tokens=100, total_cost_usd=0.02),
    ]
    plan = [
        (lambda d: (d / "a.py").write_text("v1"), 0, "s-1", usages[0]),
        (lambda d: (d / "a.py").write_text("v2"), 0, "s-1", usages[1]),
    ]

    def fake(cwd, prompt, **kwargs):
        write_fn, rc, sid, usage = plan.pop(0)
        write_fn(Path(cwd))
        return claude_runner.ClaudeRun(
            stdout="", stderr="", returncode=rc, result="ok",
            session_id=sid, raw_json=None, usage=usage,
        )

    monkeypatch.setattr("bot_lib.orchestrator.claude_runner.run_claude", fake)
    _stub_test_results(monkeypatch, [test_runner.FAIL, test_runner.PASS])

    cmd = ParsedCmd(type="fix", repo="myrepo", issue="CDS-99", instruction="x")
    out = execute_job(
        cmd, project, jira_base_url="u", jira_email="e", jira_token="t"
    )

    assert out.usage.input_tokens == 1500
    assert out.usage.output_tokens == 300
    assert out.usage.total_cost_usd == pytest.approx(0.07)


def test_retry_succeeds_on_attempt_2(project_with_remote, monkeypatch):
    project, _ = project_with_remote
    _stub_fetch_issue(monkeypatch)
    _stub_gh_pr_create(monkeypatch)

    fake = _FakeClaude(
        [
            (lambda d: (d / "a.py").write_text("v1"), 0, "s-1"),
            (lambda d: (d / "a.py").write_text("v2"), 0, "s-1"),
        ]
    )
    monkeypatch.setattr("bot_lib.orchestrator.claude_runner.run_claude", fake)
    _stub_test_results(monkeypatch, [test_runner.FAIL, test_runner.PASS])

    cmd = ParsedCmd(type="fix", repo="myrepo", issue="CDS-99", instruction="x")
    out = execute_job(
        cmd, project, jira_base_url="u", jira_email="e", jira_token="t"
    )

    assert out.status == SUCCESS
    assert out.attempts == 2
    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%b"],
        cwd=project.path, capture_output=True, text=True, check=True,
    ).stdout
    assert "Tests: PASS (attempt 2/3)" in log


def test_retry_exhausts_three_attempts(project_with_remote, monkeypatch):
    project, _ = project_with_remote
    _stub_fetch_issue(monkeypatch)

    fake = _FakeClaude(
        [
            (lambda d: (d / "a.py").write_text("v1"), 0, "s-1"),
            (lambda d: (d / "a.py").write_text("v2"), 0, "s-1"),
            (lambda d: (d / "a.py").write_text("v3"), 0, "s-1"),
        ]
    )
    monkeypatch.setattr("bot_lib.orchestrator.claude_runner.run_claude", fake)
    _stub_test_results(
        monkeypatch, [test_runner.FAIL, test_runner.FAIL, test_runner.FAIL]
    )

    cmd = ParsedCmd(type="fix", repo="myrepo", issue="CDS-99", instruction="x")
    out = execute_job(
        cmd, project, jira_base_url="u", jira_email="e", jira_token="t"
    )

    assert out.status == FAILED
    assert out.attempts == 3
    assert out.test_status == test_runner.FAIL
    assert "재시도 3회 소진" in out.message


def test_retry_stops_when_no_incremental_change(project_with_remote, monkeypatch):
    """Plan §7 step 11 / Q11: if an attempt makes no new edits, stop."""
    project, _ = project_with_remote
    _stub_fetch_issue(monkeypatch)

    fake = _FakeClaude(
        [
            (lambda d: (d / "a.py").write_text("v1"), 0, "s-1"),
            (lambda d: None, 0, "s-1"),  # second attempt edits nothing
        ]
    )
    monkeypatch.setattr("bot_lib.orchestrator.claude_runner.run_claude", fake)
    # only one test result needed before short-circuit; supply spare for safety
    _stub_test_results(monkeypatch, [test_runner.FAIL, test_runner.FAIL])

    cmd = ParsedCmd(type="fix", repo="myrepo", issue="CDS-99", instruction="x")
    out = execute_job(
        cmd, project, jira_base_url="u", jira_email="e", jira_token="t"
    )

    assert out.status == FAILED
    assert "추가 편집 없음" in out.message
    assert out.attempts == 2  # attempt 2 is what made no edit
    assert len(fake.plan) == 0  # both planned calls consumed
    # claude was not called a third time
    assert len(fake.calls) == 2


def test_cap_aborts_before_first_attempt(project_with_remote, monkeypatch):
    """cap=0 → the loop's pre-iteration check fires immediately, no attempts."""
    project, _ = project_with_remote
    _stub_fetch_issue(monkeypatch)

    fake = _FakeClaude([])  # nothing should be consumed
    monkeypatch.setattr("bot_lib.orchestrator.claude_runner.run_claude", fake)

    cmd = ParsedCmd(type="fix", repo="myrepo", issue="CDS-99", instruction="x")
    out = execute_job(
        cmd, project, jira_base_url="u", jira_email="e", jira_token="t",
        job_cap_seconds=0,
    )

    assert out.status == FAILED
    assert "cap" in out.message
    assert len(fake.calls) == 0


def test_cap_aborts_after_first_attempt(project_with_remote, monkeypatch):
    """First attempt runs; time-advance during test_runner makes cap fire on
    iteration 2's pre-check."""
    project, _ = project_with_remote
    _stub_fetch_issue(monkeypatch)

    fake = _FakeClaude(
        [(lambda d: (d / "a.py").write_text("v1"), 0, "s-1")]
    )
    monkeypatch.setattr("bot_lib.orchestrator.claude_runner.run_claude", fake)

    # Advance the clock by 999s once test_runner is invoked
    import bot_lib.orchestrator as orch
    base = orch.time.monotonic()
    advance = [0]

    def fake_now():
        return base + advance[0]

    def advancing_run_tests(repo, cmd_, timeout):
        advance[0] = 9999  # well past any plausible cap
        return test_runner.RunResult(
            status=test_runner.FAIL, stdout="", stderr="boom",
            returncode=1, duration_seconds=0.01,
        )

    monkeypatch.setattr("bot_lib.orchestrator.time.monotonic", fake_now)
    monkeypatch.setattr("bot_lib.orchestrator.test_runner.run_tests", advancing_run_tests)

    cmd = ParsedCmd(type="fix", repo="myrepo", issue="CDS-99", instruction="x")
    out = execute_job(
        cmd, project, jira_base_url="u", jira_email="e", jira_token="t",
        job_cap_seconds=500,
    )

    assert out.status == FAILED
    assert "cap" in out.message
    assert len(fake.calls) == 1


def test_resume_uses_session_id_from_first_attempt(project_with_remote, monkeypatch):
    project, _ = project_with_remote
    _stub_fetch_issue(monkeypatch)

    fake = _FakeClaude(
        [
            (lambda d: (d / "a.py").write_text("v1"), 0, "ses-XYZ"),
            (lambda d: (d / "a.py").write_text("v2"), 0, "ses-XYZ"),
        ]
    )
    monkeypatch.setattr("bot_lib.orchestrator.claude_runner.run_claude", fake)
    _stub_gh_pr_create(monkeypatch)
    _stub_test_results(monkeypatch, [test_runner.FAIL, test_runner.PASS])

    cmd = ParsedCmd(type="fix", repo="myrepo", issue="CDS-99", instruction="x")
    execute_job(cmd, project, jira_base_url="u", jira_email="e", jira_token="t")

    assert fake.calls[0].get("resume") is None
    assert fake.calls[1].get("resume") == "ses-XYZ"


def test_resume_failure_falls_back_to_stateless(project_with_remote, monkeypatch):
    project, _ = project_with_remote
    _stub_fetch_issue(monkeypatch)
    _stub_gh_pr_create(monkeypatch)

    # attempt 1: success.  attempt 2 with resume=ses-1 fails.  attempt 2 stateless succeeds.
    fake = _FakeClaude(
        [
            (lambda d: (d / "a.py").write_text("v1"), 0, "ses-1"),
            (lambda d: None, 1, None),                         # --resume call: rc=1
            (lambda d: (d / "a.py").write_text("v2"), 0, None),  # stateless retry
        ]
    )
    monkeypatch.setattr("bot_lib.orchestrator.claude_runner.run_claude", fake)
    _stub_test_results(monkeypatch, [test_runner.FAIL, test_runner.PASS])

    cmd = ParsedCmd(type="fix", repo="myrepo", issue="CDS-99", instruction="x")
    out = execute_job(
        cmd, project, jira_base_url="u", jira_email="e", jira_token="t"
    )

    assert out.status == SUCCESS
    assert fake.calls[1].get("resume") == "ses-1"
    assert fake.calls[2].get("resume") is None  # stateless fallback

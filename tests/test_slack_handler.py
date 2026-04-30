import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import pytest

from bot_lib import orchestrator
from bot_lib.commands import ParsedCmd
from bot_lib.mutex import RepoMutex
from bot_lib.registry import Project
from bot_lib.slack_handler import (
    HELP_TEXT,
    HandlerDeps,
    format_outcome,
    handle_message,
)


ALLOWED = "U0AUQ2VTVQE"


def _project(name="myrepo", path="/tmp/myrepo"):
    return Project(
        name=name, path=path, default_branch="main",
        test_cmd=None, test_timeout=60,
    )


def _record_say():
    sent = []
    return sent, sent.append


def _stub_execute(outcome=None):
    """Return (calls_list, fake_execute_fn)."""
    calls = []
    if outcome is None:
        outcome = orchestrator.JobOutcome(
            status=orchestrator.SUCCESS,
            branch="fix/CDS-99",
            commit_sha="a" * 40,
            diff_stat=" 1 file changed, 1 insertion(+)",
            test_status="pass",
            pr_url="https://github.com/x/y/pull/1",
            message="완료",
            attempts=1,
        )

    def fake(cmd, project, **kwargs):
        calls.append({"cmd": cmd, "project": project, **kwargs})
        return outcome

    return calls, fake


def _deps(execute=None, registry=None):
    return HandlerDeps(
        allowed_user_id=ALLOWED,
        registry=registry or {"myrepo": _project()},
        jira_base_url="https://x.atlassian.net",
        jira_email="me@x.com",
        jira_token="t",
        execute=execute or (lambda *a, **kw: None),
    )


# ---- allowlist ----


def test_unauthorized_user_silently_ignored():
    sent, say = _record_say()
    handle_message(
        text="help", user_id="UOTHER", say=say,
        deps=_deps(),
    )
    assert sent == []


# ---- help ----


def test_help_returns_help_text():
    sent, say = _record_say()
    handle_message(text="help", user_id=ALLOWED, say=say, deps=_deps())
    assert sent == [HELP_TEXT]


def test_dou_um_mal_returns_help_text():
    sent, say = _record_say()
    handle_message(text="도움말", user_id=ALLOWED, say=say, deps=_deps())
    assert sent == [HELP_TEXT]


# ---- parse errors ----


def test_bad_format_returns_helpful_error():
    sent, say = _record_say()
    handle_message(
        text="fix/ceph-api/CDS99/x", user_id=ALLOWED, say=say, deps=_deps()
    )
    assert len(sent) == 1
    assert "issue 형식" in sent[0]


def test_empty_instruction_returns_error():
    sent, say = _record_say()
    handle_message(
        text="fix/myrepo/CDS-1/", user_id=ALLOWED, say=say, deps=_deps()
    )
    assert any("비어" in m for m in sent)


# ---- repo lookup ----


def test_unknown_repo_with_close_match_suggests():
    sent, say = _record_say()
    deps = _deps(registry={"ceph-api": _project("ceph-api")})
    handle_message(
        text="fix/cef-api/CDS-1/x", user_id=ALLOWED, say=say, deps=deps
    )
    assert len(sent) == 1
    assert "`cef-api`" in sent[0]
    assert "ceph-api" in sent[0]


def test_unknown_repo_with_no_close_match_lists_all():
    sent, say = _record_say()
    deps = _deps(registry={"alpha": _project("alpha"), "beta": _project("beta")})
    handle_message(
        text="fix/zzzz/CDS-1/x", user_id=ALLOWED, say=say, deps=deps
    )
    assert len(sent) == 1
    assert "`alpha`" in sent[0]
    assert "`beta`" in sent[0]


# ---- happy path → execute is called ----


def test_valid_command_invokes_execute():
    calls, fake = _stub_execute()
    sent, say = _record_say()
    handle_message(
        text="fix/myrepo/CDS-99/null check 추가",
        user_id=ALLOWED, say=say, deps=_deps(execute=fake),
    )
    assert len(calls) == 1
    cmd = calls[0]["cmd"]
    assert isinstance(cmd, ParsedCmd)
    assert cmd.type == "fix"
    assert cmd.repo == "myrepo"
    assert cmd.issue == "CDS-99"
    assert cmd.instruction == "null check 추가"
    assert calls[0]["jira_email"] == "me@x.com"


def test_progress_callback_wired_to_say():
    """The orchestrator's progress messages should reach Slack via say()."""
    sent, say = _record_say()

    def fake_execute(cmd, project, *, progress, **kwargs):
        progress("midway")
        return orchestrator.JobOutcome(
            status=orchestrator.SUCCESS, branch="fix/CDS-99",
            commit_sha=None, diff_stat="", test_status="skip",
            pr_url=None, message="완료", attempts=1,
        )

    handle_message(
        text="fix/myrepo/CDS-99/x",
        user_id=ALLOWED, say=say, deps=_deps(execute=fake_execute),
    )
    assert "midway" in sent  # came through the progress channel


# ---- outcome formatting ----


def test_format_success_includes_pr_url():
    out = orchestrator.JobOutcome(
        status=orchestrator.SUCCESS,
        branch="fix/CDS-99",
        commit_sha="abcdef0123" * 4,
        diff_stat=" 2 files changed, 5 insertions(+)",
        test_status="pass",
        pr_url="https://github.com/x/y/pull/42",
        message="완료",
        attempts=1,
    )
    msg = format_outcome(out)
    assert "✅" in msg
    assert "fix/CDS-99" in msg
    assert "https://github.com/x/y/pull/42" in msg
    assert "PASS" in msg


def test_format_blocked_uses_block_emoji():
    out = orchestrator.JobOutcome(
        status=orchestrator.BLOCKED, branch="fix/CDS-99",
        commit_sha=None, diff_stat="", test_status="",
        pr_url=None, message="dirty",
    )
    msg = format_outcome(out)
    assert "⛔" in msg
    assert "dirty" in msg


def test_format_failed_includes_attempts():
    out = orchestrator.JobOutcome(
        status=orchestrator.FAILED, branch="fix/CDS-99",
        commit_sha=None, diff_stat="", test_status="fail",
        pr_url=None, message="3회 소진", attempts=3,
    )
    msg = format_outcome(out)
    assert "❌" in msg
    assert "FAIL" in msg
    assert "3" in msg


# ---- mutex (§12 Q8) ----


def test_same_repo_serializes_with_wait_message():
    """Two requests on the same repo: second waits, first runs to completion
    before second begins, second sees the 'wait' notice."""
    mutex = RepoMutex()
    started = threading.Event()
    let_finish = threading.Event()
    order: list[str] = []

    def execute(cmd, project, **kwargs):
        order.append(f"start {cmd.issue}")
        if cmd.issue == "CDS-1":
            started.set()
            let_finish.wait(timeout=2)
        order.append(f"end {cmd.issue}")
        return orchestrator.JobOutcome(
            status=orchestrator.SUCCESS, branch=f"fix/{cmd.issue}",
            commit_sha=None, diff_stat="", test_status="skip",
            pr_url=None, message="완료", attempts=1,
        )

    deps = _deps(execute=execute)
    deps.mutex = mutex

    sent_a, say_a = _record_say()
    sent_b, say_b = _record_say()

    t1 = threading.Thread(target=lambda: handle_message(
        text="fix/myrepo/CDS-1/x", user_id=ALLOWED, say=say_a, deps=deps,
    ))
    t2 = threading.Thread(target=lambda: handle_message(
        text="fix/myrepo/CDS-2/y", user_id=ALLOWED, say=say_b, deps=deps,
    ))
    t1.start()
    started.wait(timeout=2)
    t2.start()
    time.sleep(0.05)  # let t2 send its wait message and block
    let_finish.set()
    t1.join(timeout=3)
    t2.join(timeout=3)

    assert order == ["start CDS-1", "end CDS-1", "start CDS-2", "end CDS-2"]
    assert any("작업 중" in m for m in sent_b)


def test_different_repos_run_concurrently():
    mutex = RepoMutex()
    a_running = threading.Event()
    b_running = threading.Event()

    def execute(cmd, project, **kwargs):
        if project.name == "alpha":
            a_running.set()
            b_running.wait(timeout=2)
        else:
            b_running.set()
            a_running.wait(timeout=2)
        return orchestrator.JobOutcome(
            status=orchestrator.SUCCESS, branch="fix/X-1",
            commit_sha=None, diff_stat="", test_status="skip",
            pr_url=None, message="완료", attempts=1,
        )

    deps = HandlerDeps(
        allowed_user_id=ALLOWED,
        registry={"alpha": _project("alpha"), "beta": _project("beta")},
        jira_base_url="u", jira_email="e", jira_token="t",
        execute=execute, mutex=mutex,
    )

    s1, say1 = _record_say()
    s2, say2 = _record_say()
    t1 = threading.Thread(target=lambda: handle_message(
        text="fix/alpha/CDS-1/x", user_id=ALLOWED, say=say1, deps=deps,
    ))
    t2 = threading.Thread(target=lambda: handle_message(
        text="fix/beta/CDS-2/y", user_id=ALLOWED, say=say2, deps=deps,
    ))
    t1.start(); t2.start()
    t1.join(timeout=3); t2.join(timeout=3)

    # If they couldn't run concurrently the events would never both fire,
    # and the threads would time out (still alive after join).
    assert not t1.is_alive() and not t2.is_alive()


# ---- cleanup (§12 Q15) ----


@pytest.fixture
def repo_dirty(tmp_path):
    """A real git repo with uncommitted modifications + untracked file."""
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=r, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@x.com"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=r, check=True)
    (r / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=r, check=True, capture_output=True)
    # leave it dirty
    (r / "README.md").write_text("modified\n")
    (r / "untracked.py").write_text("garbage\n")
    return r


def test_cleanup_resets_working_tree(repo_dirty):
    project = Project(
        name="myrepo", path=str(repo_dirty), default_branch="main",
        test_cmd=None, test_timeout=10,
    )
    deps = HandlerDeps(
        allowed_user_id=ALLOWED, registry={"myrepo": project},
        jira_base_url="u", jira_email="e", jira_token="t",
        execute=lambda *a, **kw: None,
    )
    sent, say = _record_say()
    handle_message(text="cleanup/myrepo", user_id=ALLOWED, say=say, deps=deps)

    # working tree is clean
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo_dirty,
        capture_output=True, text=True, check=True,
    ).stdout
    assert status.strip() == ""
    # untracked file removed
    assert not (repo_dirty / "untracked.py").exists()
    # README restored
    assert (repo_dirty / "README.md").read_text() == "hi\n"
    assert any("초기화 완료" in m for m in sent)


def test_cleanup_unknown_repo_lists_known():
    deps = HandlerDeps(
        allowed_user_id=ALLOWED,
        registry={"alpha": _project("alpha"), "beta": _project("beta")},
        jira_base_url="u", jira_email="e", jira_token="t",
        execute=lambda *a, **kw: None,
    )
    sent, say = _record_say()
    handle_message(text="cleanup/zzz", user_id=ALLOWED, say=say, deps=deps)
    assert any("모르는 repo" in m for m in sent)


def test_cleanup_acquires_mutex(repo_dirty):
    """Cleanup waits for an in-flight job on the same repo."""
    mutex = RepoMutex()
    job_running = threading.Event()
    let_job_finish = threading.Event()

    def slow_execute(cmd, project, **kw):
        job_running.set()
        let_job_finish.wait(timeout=2)
        return orchestrator.JobOutcome(
            status=orchestrator.SUCCESS, branch="fix/CDS-1",
            commit_sha=None, diff_stat="", test_status="skip",
            pr_url=None, message="완료", attempts=1,
        )

    project = Project(
        name="myrepo", path=str(repo_dirty), default_branch="main",
        test_cmd=None, test_timeout=10,
    )
    deps = HandlerDeps(
        allowed_user_id=ALLOWED, registry={"myrepo": project},
        jira_base_url="u", jira_email="e", jira_token="t",
        execute=slow_execute, mutex=mutex,
    )

    # Even though the working tree is dirty here, the FIRST request bypasses
    # the dirty-check because we mocked execute. We only care that cleanup
    # waited for the lock.
    s1, say1 = _record_say()
    s2, say2 = _record_say()
    t1 = threading.Thread(target=lambda: handle_message(
        text="fix/myrepo/CDS-1/x", user_id=ALLOWED, say=say1, deps=deps,
    ))
    t2 = threading.Thread(target=lambda: handle_message(
        text="cleanup/myrepo", user_id=ALLOWED, say=say2, deps=deps,
    ))
    t1.start()
    job_running.wait(timeout=2)
    t2.start()
    time.sleep(0.05)
    # at this point cleanup is waiting; verify wait message was sent
    assert any("작업 중" in m for m in s2)
    let_job_finish.set()
    t1.join(timeout=3)
    t2.join(timeout=3)

    # cleanup eventually ran
    assert any("초기화 완료" in m for m in s2)

from typing import Optional

import pytest

from bot_lib import orchestrator
from bot_lib.commands import ParsedCmd
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

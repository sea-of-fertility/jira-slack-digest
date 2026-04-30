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
        name=name, path=path, default_branch="main", remote="origin",
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


def _deps(execute=None, registry=None, context=None):
    from bot_lib.context import ContextStore
    return HandlerDeps(
        allowed_user_id=ALLOWED,
        registry={"myrepo": _project()} if registry is None else registry,
        jira_base_url="https://x.atlassian.net",
        jira_email="me@x.com",
        jira_token="t",
        execute=execute or (lambda *a, **kw: None),
        context=context,
    )


def _ctx_store(tmp_path, repo="myrepo", remote="origin", branch=None):
    """Create a ContextStore pre-set to (repo, remote, branch)."""
    from bot_lib.context import Context, ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo=repo, remote=remote, branch=branch))
    return store


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
        text="run fix CDS99 -d x", user_id=ALLOWED, say=say, deps=_deps()
    )
    assert len(sent) == 1
    assert "issue 형식" in sent[0]


def test_non_run_prefix_returns_format_error():
    """Slash form is no longer accepted."""
    sent, say = _record_say()
    handle_message(text="fix/CDS-99/x", user_id=ALLOWED, say=say, deps=_deps())
    assert len(sent) == 1
    assert "run" in sent[0]


# ---- happy path → execute is called ----


def test_valid_command_invokes_execute(tmp_path):
    calls, fake = _stub_execute()
    sent, say = _record_say()
    handle_message(
        text="run fix CDS-99 -d null check 추가",
        user_id=ALLOWED, say=say,
        deps=_deps(execute=fake, context=_ctx_store(tmp_path)),
    )
    assert len(calls) == 1
    cmd = calls[0]["cmd"]
    assert isinstance(cmd, ParsedCmd)
    assert cmd.type == "fix"
    assert cmd.repo == "myrepo"   # filled from context
    assert cmd.issue == "CDS-99"
    assert cmd.instruction == "null check 추가"
    assert calls[0]["jira_email"] == "me@x.com"


def test_run_without_dash_d_passes_none_instruction(tmp_path):
    """Jira-trust mode: no -d → cmd.instruction is None."""
    calls, fake = _stub_execute()
    sent, say = _record_say()
    handle_message(
        text="run fix CDS-99",
        user_id=ALLOWED, say=say,
        deps=_deps(execute=fake, context=_ctx_store(tmp_path)),
    )
    assert calls[0]["cmd"].instruction is None


def test_progress_callback_wired_to_say(tmp_path):
    sent, say = _record_say()

    def fake_execute(cmd, project, *, progress, **kwargs):
        progress("midway")
        return orchestrator.JobOutcome(
            status=orchestrator.SUCCESS, branch="fix/CDS-99",
            commit_sha=None, diff_stat="", test_status="skip",
            pr_url=None, message="완료", attempts=1,
        )

    handle_message(
        text="run fix CDS-99 -d x",
        user_id=ALLOWED, say=say,
        deps=_deps(execute=fake_execute, context=_ctx_store(tmp_path)),
    )
    assert "midway" in sent


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


# ---- token usage in outcome ----


def test_format_includes_token_usage_on_success():
    from bot_lib.claude_runner import TokenUsage
    out = orchestrator.JobOutcome(
        status=orchestrator.SUCCESS, branch="fix/CDS-99",
        commit_sha="a" * 40, diff_stat=" 1 file changed",
        test_status="pass", pr_url="https://pr",
        message="완료", attempts=1,
        usage=TokenUsage(
            input_tokens=12345, output_tokens=678,
            cache_read_input_tokens=2000, cache_creation_input_tokens=500,
            total_cost_usd=0.0234,
        ),
    )
    msg = format_outcome(out)
    assert "in=12,345" in msg
    assert "out=678" in msg
    assert "cache(read=2,000, create=500)" in msg
    assert "$0.0234" in msg


def test_format_omits_usage_when_empty():
    from bot_lib.claude_runner import TokenUsage
    out = orchestrator.JobOutcome(
        status=orchestrator.SUCCESS, branch="fix/CDS-99",
        commit_sha=None, diff_stat="", test_status="skip",
        pr_url=None, message="완료", attempts=1,
        usage=TokenUsage(),
    )
    msg = format_outcome(out)
    assert "토큰" not in msg


def test_format_includes_usage_on_failed_outcome():
    from bot_lib.claude_runner import TokenUsage
    out = orchestrator.JobOutcome(
        status=orchestrator.FAILED, branch="fix/CDS-99",
        commit_sha=None, diff_stat="", test_status="fail",
        pr_url=None, message="3회 소진", attempts=3,
        usage=TokenUsage(input_tokens=5000, output_tokens=300, total_cost_usd=0.025),
    )
    msg = format_outcome(out)
    assert "in=5,000" in msg
    assert "$0.0250" in msg


# ---- init / status / clear (§B) ----


@pytest.fixture
def real_repo(tmp_path):
    """A real git repo with two named remotes (305, 306). Returned as a Project."""
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=r, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@x.com"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=r, check=True)
    (r / "README.md").write_text("hi")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=r, check=True, capture_output=True)
    # Bare remotes named like real GitLab: 305, 306
    for name in ("305", "306"):
        bare = tmp_path / f"{name}.git"
        bare.mkdir()
        subprocess.run(["git", "init", "--bare", "-b", "main"], cwd=bare, check=True, capture_output=True)
        subprocess.run(["git", "remote", "add", name, str(bare)], cwd=r, check=True)
    return Project(
        name="ceph-api", path=str(r), default_branch="main", remote="305",
        test_cmd=None, test_timeout=10,
    )


def test_init_sets_context_when_remote_valid(tmp_path, real_repo):
    from bot_lib.context import ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    deps = _deps(registry={"ceph-api": real_repo}, context=store)

    sent, say = _record_say()
    handle_message(text="init ceph-api -r 306", user_id=ALLOWED, say=say, deps=deps)

    assert any("컨텍스트 설정" in m for m in sent)
    ctx = store.get()
    assert ctx is not None
    assert ctx.repo == "ceph-api"
    assert ctx.remote == "306"


def test_init_rejects_unknown_remote(tmp_path, real_repo):
    from bot_lib.context import ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    deps = _deps(registry={"ceph-api": real_repo}, context=store)

    sent, say = _record_say()
    handle_message(text="init ceph-api -r 999", user_id=ALLOWED, say=say, deps=deps)

    assert any("remote `999` 없음" in m for m in sent)
    assert any("305" in m and "306" in m for m in sent)
    assert store.get() is None


def test_init_branch_override_persists(tmp_path, real_repo):
    """`init ceph-api -b develop` writes branch into context."""
    from bot_lib.context import ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    deps = _deps(registry={"ceph-api": real_repo}, context=store)

    sent, say = _record_say()
    handle_message(text="init ceph-api -b develop", user_id=ALLOWED, say=say, deps=deps)
    ctx = store.get()
    assert ctx is not None
    assert ctx.repo == "ceph-api"
    assert ctx.remote == real_repo.remote  # projects.md default since no -r
    assert ctx.branch == "develop"


def test_init_both_flags(tmp_path, real_repo):
    from bot_lib.context import ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    deps = _deps(registry={"ceph-api": real_repo}, context=store)

    sent, say = _record_say()
    handle_message(text="init ceph-api -r 306 -b develop", user_id=ALLOWED, say=say, deps=deps)
    ctx = store.get()
    assert ctx == __import__("bot_lib.context", fromlist=["Context"]).Context(
        repo="ceph-api", remote="306", branch="develop"
    )


def test_init_flag_order_does_not_matter(tmp_path, real_repo):
    from bot_lib.context import ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    deps = _deps(registry={"ceph-api": real_repo}, context=store)

    sent, say = _record_say()
    handle_message(text="init -b develop -r 306 ceph-api", user_id=ALLOWED, say=say, deps=deps)
    ctx = store.get()
    assert ctx is not None
    assert ctx.repo == "ceph-api"
    assert ctx.remote == "306"
    assert ctx.branch == "develop"


def test_init_unknown_flag_rejected(tmp_path, real_repo):
    from bot_lib.context import ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    deps = _deps(registry={"ceph-api": real_repo}, context=store)

    sent, say = _record_say()
    handle_message(text="init ceph-api --foo bar", user_id=ALLOWED, say=say, deps=deps)
    assert any("모르는 옵션" in m for m in sent)
    assert store.get() is None


def test_init_missing_flag_value(tmp_path, real_repo):
    from bot_lib.context import ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    deps = _deps(registry={"ceph-api": real_repo}, context=store)

    sent, say = _record_say()
    handle_message(text="init ceph-api -r", user_id=ALLOWED, say=say, deps=deps)
    assert any("remote 이름 필요" in m for m in sent)


def test_init_missing_repo(tmp_path, real_repo):
    from bot_lib.context import ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    deps = _deps(registry={"ceph-api": real_repo}, context=store)

    sent, say = _record_say()
    handle_message(text="init -r 305", user_id=ALLOWED, say=say, deps=deps)
    assert any("repo" in m and "빠졌" in m for m in sent)


def test_init_rejects_unknown_repo(tmp_path, real_repo):
    from bot_lib.context import ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    deps = _deps(registry={"ceph-api": real_repo}, context=store)

    sent, say = _record_say()
    handle_message(text="init typo-api -r 305", user_id=ALLOWED, say=say, deps=deps)

    assert any("모르는 repo" in m for m in sent)
    assert store.get() is None


def test_init_clear_removes_context(tmp_path):
    from bot_lib.context import Context, ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="myrepo", remote="origin"))

    deps = _deps(context=store)
    sent, say = _record_say()
    handle_message(text="clear", user_id=ALLOWED, say=say, deps=deps)

    assert any("삭제" in m for m in sent)
    assert store.get() is None


def test_status_no_context(tmp_path):
    from bot_lib.context import ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    deps = _deps(context=store)
    sent, say = _record_say()
    handle_message(text="status", user_id=ALLOWED, say=say, deps=deps)
    assert any("없음" in m for m in sent)
    assert any("myrepo" in m for m in sent)


def test_status_with_context(tmp_path):
    from bot_lib.context import Context, ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="myrepo", remote="305"))
    deps = _deps(context=store)
    sent, say = _record_say()
    handle_message(text="status", user_id=ALLOWED, say=say, deps=deps)
    assert any("`myrepo`" in m and "`305`" in m for m in sent)


# ---- 3-token routing ----


def test_run_uses_context_repo(tmp_path):
    from bot_lib.context import Context, ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="myrepo", remote="305"))

    calls, fake = _stub_execute()
    deps = _deps(execute=fake, context=store)

    sent, say = _record_say()
    handle_message(
        text="run fix CDS-99 -d null check",
        user_id=ALLOWED, say=say, deps=deps,
    )
    assert len(calls) == 1
    assert calls[0]["cmd"].repo == "myrepo"   # filled from context
    assert calls[0]["project"].remote == "305"  # context override applied


def test_run_without_context_returns_error(tmp_path):
    from bot_lib.context import ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))  # empty
    deps = _deps(context=store)
    sent, say = _record_say()
    handle_message(
        text="run fix CDS-99 -d x", user_id=ALLOWED, say=say, deps=deps
    )
    assert any("컨텍스트 미설정" in m for m in sent)


def test_run_uses_context_branch_override(tmp_path):
    from bot_lib.context import Context, ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="myrepo", remote="305", branch="develop"))

    calls, fake = _stub_execute()
    deps = _deps(execute=fake, context=store)
    sent, say = _record_say()
    handle_message(text="run fix CDS-99 -d x", user_id=ALLOWED, say=say, deps=deps)
    assert calls[0]["project"].default_branch == "develop"


def test_run_no_branch_override_keeps_projects_md_default(tmp_path):
    from bot_lib.context import Context, ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="myrepo", remote="305"))  # no branch

    calls, fake = _stub_execute()
    deps = _deps(execute=fake, context=store)
    sent, say = _record_say()
    handle_message(text="run fix CDS-99 -d x", user_id=ALLOWED, say=say, deps=deps)
    assert calls[0]["project"].default_branch == "main"


def test_run_passes_context_who_to_execute(tmp_path):
    from bot_lib.context import Context, ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="myrepo", remote="305", who="hjpark"))
    calls, fake = _stub_execute()
    deps = _deps(execute=fake, context=store)
    sent, say = _record_say()
    handle_message(text="run fix CDS-99 -d x", user_id=ALLOWED, say=say, deps=deps)
    assert calls[0].get("who") == "hjpark"


def test_run_falls_back_to_env_bot_user(tmp_path):
    from bot_lib.context import Context, ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="myrepo", remote="305"))  # no who
    calls, fake = _stub_execute()
    deps = _deps(execute=fake, context=store)
    deps.env_bot_user = "envname"
    sent, say = _record_say()
    handle_message(text="run fix CDS-99 -d x", user_id=ALLOWED, say=say, deps=deps)
    assert calls[0].get("who") == "envname"


def test_run_who_none_when_neither_set(tmp_path):
    from bot_lib.context import Context, ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="myrepo", remote="305"))  # no who
    calls, fake = _stub_execute()
    deps = _deps(execute=fake, context=store)  # no env_bot_user
    sent, say = _record_say()
    handle_message(text="run fix CDS-99 -d x", user_id=ALLOWED, say=say, deps=deps)
    assert calls[0].get("who") is None


# ---- cancel ----


def test_cancel_no_running_job_reports_clean(tmp_path):
    from bot_lib.cancellation import CancellationRegistry
    cancel_reg = CancellationRegistry()
    deps = _deps()
    deps.cancel_registry = cancel_reg
    sent, say = _record_say()
    handle_message(text="cancel myrepo", user_id=ALLOWED, say=say, deps=deps)
    assert any("진행 중인 claude 작업 없음" in m for m in sent)


def test_cancel_unknown_repo_suggests():
    from bot_lib.cancellation import CancellationRegistry
    deps = _deps(registry={"ceph-api": _project("ceph-api")})
    deps.cancel_registry = CancellationRegistry()
    sent, say = _record_say()
    handle_message(text="cancel cef-api", user_id=ALLOWED, say=say, deps=deps)
    assert any("모르는 repo" in m for m in sent)


def test_cancel_sigterms_registered_pid():
    """cancel <repo> looks up the registered PID and triggers killpg."""
    from bot_lib.cancellation import CancellationRegistry
    cancel_reg = CancellationRegistry()
    cancel_reg.register("myrepo", 99887)

    killed = []
    real_killpg = __import__("os").killpg

    def fake_killpg(pid, sig):
        killed.append((pid, sig))

    import bot_lib.cancellation as can_mod
    deps = _deps()
    deps.cancel_registry = cancel_reg

    import os as os_mod
    saved = os_mod.killpg
    os_mod.killpg = fake_killpg
    try:
        sent, say = _record_say()
        handle_message(text="cancel myrepo", user_id=ALLOWED, say=say, deps=deps)
    finally:
        os_mod.killpg = saved

    assert killed and killed[0][0] == 99887
    assert any("99887" in m and "SIGTERM" in m for m in sent)


def test_cancel_no_registry_reports_misconfig():
    deps = _deps()
    # cancel_registry left None
    sent, say = _record_say()
    handle_message(text="cancel myrepo", user_id=ALLOWED, say=say, deps=deps)
    assert any("cancel registry 미설정" in m for m in sent)


# ---- find ----


@pytest.fixture
def find_repo(tmp_path):
    """A real git repo with a few tracked Java-style files."""
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=r, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@x.com"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=r, check=True)
    for path in [
        "src/main/java/ApiOsdService.java",
        "src/main/java/ApiOsdServiceImpl.java",
        "src/test/java/ApiOsdServiceTest.java",
        "src/main/java/CephExporter.java",
    ]:
        full = r / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("data")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=r, check=True, capture_output=True)
    return Project(
        name="myrepo", path=str(r), default_branch="main", remote="origin",
        test_cmd=None, test_timeout=10,
    )


def test_find_uses_context_repo(tmp_path, find_repo):
    from bot_lib.context import Context, ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="myrepo", remote="origin"))
    deps = _deps(registry={"myrepo": find_repo}, context=store)
    sent, say = _record_say()
    handle_message(text="find ApiOsd", user_id=ALLOWED, say=say, deps=deps)
    msg = sent[0]
    assert "ApiOsdService.java" in msg
    assert "ApiOsdServiceImpl.java" in msg
    assert "ApiOsdServiceTest.java" in msg
    assert "CephExporter.java" not in msg


def test_find_case_insensitive(find_repo):
    deps = _deps(registry={"myrepo": find_repo})
    sent, say = _record_say()
    handle_message(text="find apiosd -r myrepo", user_id=ALLOWED, say=say, deps=deps)
    assert "ApiOsdService.java" in sent[0]


def test_find_explicit_repo_flag(find_repo):
    deps = _deps(registry={"ceph-api": find_repo})
    sent, say = _record_say()
    handle_message(text="find ApiOsd -r ceph-api", user_id=ALLOWED, say=say, deps=deps)
    assert "ApiOsdService.java" in sent[0]


def test_find_no_match_returns_message(find_repo):
    deps = _deps(registry={"myrepo": find_repo})
    sent, say = _record_say()
    handle_message(text="find nonexistent_xyz -r myrepo", user_id=ALLOWED, say=say, deps=deps)
    assert any("일치 파일 없음" in m for m in sent)


def test_find_unknown_repo_suggests(find_repo):
    deps = _deps(registry={"ceph-api": find_repo})
    sent, say = _record_say()
    handle_message(text="find ApiOsd -r cef-api", user_id=ALLOWED, say=say, deps=deps)
    assert any("모르는 repo" in m for m in sent)


def test_find_no_pattern_returns_usage(tmp_path):
    deps = _deps()
    sent, say = _record_say()
    handle_message(text="find", user_id=ALLOWED, say=say, deps=deps)
    assert any("패턴 입력 필요" in m or "사용법" in m for m in sent)


def test_find_no_context_no_explicit_repo_errors(tmp_path):
    from bot_lib.context import ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))  # empty
    deps = _deps(context=store)
    sent, say = _record_say()
    handle_message(text="find ApiOsd", user_id=ALLOWED, say=say, deps=deps)
    assert any("컨텍스트 미설정" in m for m in sent)


def test_find_unknown_flag_rejected(find_repo):
    deps = _deps(registry={"myrepo": find_repo})
    sent, say = _record_say()
    handle_message(text="find ApiOsd --foo bar -r myrepo", user_id=ALLOWED, say=say, deps=deps)
    assert any("모르는 옵션" in m for m in sent)


def test_find_truncation_hint_when_over_limit(tmp_path):
    """When matches exceed FIND_DEFAULT_LIMIT, footer suggests narrowing."""
    r = tmp_path / "big_repo"
    r.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=r, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@x.com"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=r, check=True)
    for i in range(25):
        (r / f"Service{i}.java").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=r, check=True, capture_output=True)
    project = Project(
        name="big", path=str(r), default_branch="main", remote="origin",
        test_cmd=None, test_timeout=10,
    )
    deps = _deps(registry={"big": project})
    sent, say = _record_say()
    handle_message(text="find Service -r big", user_id=ALLOWED, say=say, deps=deps)
    msg = sent[0]
    assert "25개 중 처음 20" in msg
    assert "외 5개" in msg
    assert "더 구체적인 패턴" in msg


# ---- who ----


def test_who_unset_returns_none(tmp_path):
    from bot_lib.context import ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    deps = _deps(context=store)  # no env_bot_user
    sent, say = _record_say()
    handle_message(text="who", user_id=ALLOWED, say=say, deps=deps)
    assert any("없음" in m for m in sent)


def test_who_returns_env_when_only_env_set(tmp_path):
    from bot_lib.context import ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    deps = _deps(context=store)
    deps.env_bot_user = "hjpark"
    sent, say = _record_say()
    handle_message(text="who", user_id=ALLOWED, say=say, deps=deps)
    assert any("hjpark" in m and "env" in m for m in sent)


def test_who_returns_context_value_overriding_env(tmp_path):
    from bot_lib.context import Context, ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="myrepo", remote="origin", who="overridden"))
    deps = _deps(context=store)
    deps.env_bot_user = "envname"
    sent, say = _record_say()
    handle_message(text="who", user_id=ALLOWED, say=say, deps=deps)
    assert any("overridden" in m and "context" in m for m in sent)
    assert all("envname" not in m for m in sent)


def test_who_set_persists_to_context(tmp_path):
    from bot_lib.context import Context, ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="myrepo", remote="origin"))
    deps = _deps(context=store)
    sent, say = _record_say()
    handle_message(text="who hjpark", user_id=ALLOWED, say=say, deps=deps)
    assert store.get().who == "hjpark"
    assert any("사용자 변경" in m for m in sent)


def test_who_set_rejects_invalid_chars(tmp_path):
    from bot_lib.context import Context, ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="myrepo", remote="origin"))
    deps = _deps(context=store)
    sent, say = _record_say()
    handle_message(text="who 박형준", user_id=ALLOWED, say=say, deps=deps)
    assert any("형식" in m for m in sent)
    assert store.get().who is None


def test_who_set_without_context_errors(tmp_path):
    from bot_lib.context import ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))  # empty
    deps = _deps(context=store)
    sent, say = _record_say()
    handle_message(text="who hjpark", user_id=ALLOWED, say=say, deps=deps)
    assert any("init" in m and "컨텍스트" in m for m in sent)


def test_who_clear_removes_context_who(tmp_path):
    from bot_lib.context import Context, ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="myrepo", remote="origin", who="hjpark"))
    deps = _deps(context=store)
    sent, say = _record_say()
    handle_message(text="who clear", user_id=ALLOWED, say=say, deps=deps)
    assert store.get().who is None
    assert any("삭제" in m for m in sent)


def test_who_set_accepts_dot_dash_underscore(tmp_path):
    from bot_lib.context import Context, ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="myrepo", remote="origin"))
    deps = _deps(context=store)
    sent, say = _record_say()
    for valid in ("hj.park", "hj-park", "hj_park", "HJPark99"):
        handle_message(text=f"who {valid}", user_id=ALLOWED, say=say, deps=deps)
        assert store.get().who == valid


# ---- repo / remote (read-only) ----


def test_repo_lists_all_registered():
    deps = _deps(registry={
        "alpha": _project("alpha"),
        "beta": _project("beta"),
    })
    sent, say = _record_say()
    handle_message(text="repo", user_id=ALLOWED, say=say, deps=deps)
    msg = sent[0]
    assert "alpha" in msg and "beta" in msg
    assert "2개" in msg


def test_repo_marks_current_context(tmp_path):
    from bot_lib.context import Context, ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="beta", remote="origin"))
    deps = _deps(
        registry={"alpha": _project("alpha"), "beta": _project("beta")},
        context=store,
    )
    sent, say = _record_say()
    handle_message(text="repo", user_id=ALLOWED, say=say, deps=deps)
    msg = sent[0]
    beta_line = next(line for line in msg.splitlines() if "beta" in line)
    alpha_line = next(line for line in msg.splitlines() if "alpha" in line)
    assert "⭐" in beta_line
    assert "⭐" not in alpha_line


def test_repo_empty_registry():
    deps = _deps(registry={})
    sent, say = _record_say()
    handle_message(text="repo", user_id=ALLOWED, say=say, deps=deps)
    assert any("등록된 repo 없음" in m for m in sent)


def test_remote_explicit_repo_lists_remotes(real_repo):
    deps = _deps(registry={"ceph-api": real_repo})
    sent, say = _record_say()
    handle_message(text="remote ceph-api", user_id=ALLOWED, say=say, deps=deps)
    msg = sent[0]
    assert "ceph-api" in msg
    assert "305" in msg and "306" in msg


def test_remote_uses_context_repo(tmp_path, real_repo):
    from bot_lib.context import Context, ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="ceph-api", remote="306"))
    deps = _deps(registry={"ceph-api": real_repo}, context=store)
    sent, say = _record_say()
    handle_message(text="remote", user_id=ALLOWED, say=say, deps=deps)
    msg = sent[0]
    assert "ceph-api" in msg
    assert "기본: `306`" in msg
    line_306 = next(line for line in msg.splitlines() if line.lstrip().startswith("306"))
    line_305 = next(line for line in msg.splitlines() if line.lstrip().startswith("305"))
    assert "⭐" in line_306
    assert "⭐" not in line_305


def test_remote_no_context_no_arg_returns_error(tmp_path):
    from bot_lib.context import ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    deps = _deps(context=store)
    sent, say = _record_say()
    handle_message(text="remote", user_id=ALLOWED, say=say, deps=deps)
    assert any("컨텍스트 미설정" in m for m in sent)


def test_remote_unknown_repo_suggests(real_repo):
    deps = _deps(registry={"ceph-api": real_repo})
    sent, say = _record_say()
    handle_message(text="remote cef-api", user_id=ALLOWED, say=say, deps=deps)
    assert any("모르는 repo" in m for m in sent)


def test_remote_repo_without_remotes(tmp_path):
    r = tmp_path / "lonely"
    r.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=r, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@x.com"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=r, check=True)
    (r / "x").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=r, check=True, capture_output=True)
    project = Project(
        name="lonely", path=str(r), default_branch="main", remote="origin",
        test_cmd=None, test_timeout=10,
    )
    deps = _deps(registry={"lonely": project})
    sent, say = _record_say()
    handle_message(text="remote lonely", user_id=ALLOWED, say=say, deps=deps)
    assert any("등록된 git remote 없음" in m for m in sent)


# ---- branch ----


@pytest.fixture
def branch_repo(tmp_path):
    """A real git repo with several branches at different timestamps."""
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=r, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@x.com"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=r, check=True)
    (r / "README.md").write_text("hi")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=r, check=True, capture_output=True)
    for n in ("feat/a", "feat/b", "fix/c"):
        subprocess.run(["git", "checkout", "-b", n], cwd=r, check=True, capture_output=True)
        (r / f"{n.replace('/', '_')}.txt").write_text("x")
        subprocess.run(["git", "add", "-A"], cwd=r, check=True)
        subprocess.run(["git", "commit", "-m", n], cwd=r, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=r, check=True, capture_output=True)
    return Project(
        name="myrepo", path=str(r), default_branch="main", remote="origin",
        test_cmd=None, test_timeout=10,
    )


def test_branch_explicit_repo_lists_all(branch_repo):
    deps = _deps(registry={"myrepo": branch_repo})
    sent, say = _record_say()
    handle_message(text="branch myrepo", user_id=ALLOWED, say=say, deps=deps)
    assert len(sent) == 1
    msg = sent[0]
    assert "myrepo" in msg
    assert "main" in msg and "feat/a" in msg and "fix/c" in msg
    assert "⭐" in msg  # current branch marked


def test_branch_uses_context_repo(tmp_path, branch_repo):
    from bot_lib.context import Context, ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="myrepo", remote="origin"))

    deps = _deps(registry={"myrepo": branch_repo}, context=store)
    sent, say = _record_say()
    handle_message(text="branch", user_id=ALLOWED, say=say, deps=deps)
    assert any("myrepo" in m for m in sent)
    assert any("feat/a" in m for m in sent)


def test_branch_no_context_no_arg_returns_error(tmp_path):
    from bot_lib.context import ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))  # empty
    deps = _deps(context=store)
    sent, say = _record_say()
    handle_message(text="branch", user_id=ALLOWED, say=say, deps=deps)
    assert any("컨텍스트 미설정" in m for m in sent)


def test_branch_unknown_repo_suggests(branch_repo):
    deps = _deps(registry={"ceph-api": branch_repo})
    sent, say = _record_say()
    handle_message(text="branch cef-api", user_id=ALLOWED, say=say, deps=deps)
    assert any("모르는 repo" in m for m in sent)


def test_branch_default_limit_is_ten(tmp_path):
    """Repo with 12 branches: default `/branch` shows 10 + footer."""
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=r, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@x.com"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=r, check=True)
    (r / "README.md").write_text("hi")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=r, check=True, capture_output=True)
    for i in range(11):  # main + 11 = 12 total
        subprocess.run(["git", "checkout", "main"], cwd=r, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "-b", f"feat/x{i}"], cwd=r, check=True, capture_output=True)
        (r / f"x{i}.txt").write_text(str(i))
        subprocess.run(["git", "add", "-A"], cwd=r, check=True)
        subprocess.run(["git", "commit", "-m", f"x{i}"], cwd=r, check=True, capture_output=True)

    project = Project(
        name="big", path=str(r), default_branch="main", remote="origin",
        test_cmd=None, test_timeout=10,
    )
    deps = _deps(registry={"big": project})
    sent, say = _record_say()
    handle_message(text="branch big", user_id=ALLOWED, say=say, deps=deps)
    msg = sent[0]
    assert "12개 중 최근 10" in msg
    assert "외 2개" in msg


def test_branch_all_shows_full_list(tmp_path):
    """`branch <repo> all` shows everything (up to hard cap)."""
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=r, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@x.com"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=r, check=True)
    (r / "README.md").write_text("hi")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=r, check=True, capture_output=True)
    for i in range(11):
        subprocess.run(["git", "checkout", "main"], cwd=r, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "-b", f"feat/x{i}"], cwd=r, check=True, capture_output=True)
        (r / f"x{i}.txt").write_text(str(i))
        subprocess.run(["git", "add", "-A"], cwd=r, check=True)
        subprocess.run(["git", "commit", "-m", f"x{i}"], cwd=r, check=True, capture_output=True)

    project = Project(
        name="big", path=str(r), default_branch="main", remote="origin",
        test_cmd=None, test_timeout=10,
    )
    deps = _deps(registry={"big": project})
    sent, say = _record_say()
    handle_message(text="branch big all", user_id=ALLOWED, say=say, deps=deps)
    msg = sent[0]
    assert "전체 12" in msg
    for i in range(11):
        assert f"feat/x{i}" in msg


# ---- mutex (§12 Q8) ----


def test_same_repo_serializes_with_wait_message(tmp_path):
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

    deps = _deps(execute=execute, context=_ctx_store(tmp_path))
    deps.mutex = mutex

    sent_a, say_a = _record_say()
    sent_b, say_b = _record_say()

    t1 = threading.Thread(target=lambda: handle_message(
        text="run fix CDS-1 -d x", user_id=ALLOWED, say=say_a, deps=deps,
    ))
    t2 = threading.Thread(target=lambda: handle_message(
        text="run fix CDS-2 -d y", user_id=ALLOWED, say=say_b, deps=deps,
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


# (test_different_repos_run_concurrently removed: in the run-form schema repo
# always comes from the single shared ContextStore, so two concurrent threads
# cannot target different repos in one process. Mutex behavior is still
# covered by the same-repo serialization test above and bot_lib/tests/test_mutex.py.)


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
        name="myrepo", path=str(repo_dirty), default_branch="main", remote="origin",
        test_cmd=None, test_timeout=10,
    )
    deps = HandlerDeps(
        allowed_user_id=ALLOWED, registry={"myrepo": project},
        jira_base_url="u", jira_email="e", jira_token="t",
        execute=lambda *a, **kw: None,
    )
    sent, say = _record_say()
    handle_message(text="cleanup myrepo", user_id=ALLOWED, say=say, deps=deps)

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
    handle_message(text="cleanup zzz", user_id=ALLOWED, say=say, deps=deps)
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
        name="myrepo", path=str(repo_dirty), default_branch="main", remote="origin",
        test_cmd=None, test_timeout=10,
    )
    from bot_lib.context import Context, ContextStore
    store = ContextStore(str(repo_dirty.parent / "ctx.json"))
    store.set(Context(repo="myrepo", remote="origin"))
    deps = HandlerDeps(
        allowed_user_id=ALLOWED, registry={"myrepo": project},
        jira_base_url="u", jira_email="e", jira_token="t",
        execute=slow_execute, mutex=mutex, context=store,
    )

    # Even though the working tree is dirty here, the FIRST request bypasses
    # the dirty-check because we mocked execute. We only care that cleanup
    # waited for the lock.
    s1, say1 = _record_say()
    s2, say2 = _record_say()
    t1 = threading.Thread(target=lambda: handle_message(
        text="run fix CDS-1 -d x", user_id=ALLOWED, say=say1, deps=deps,
    ))
    t2 = threading.Thread(target=lambda: handle_message(
        text="cleanup myrepo", user_id=ALLOWED, say=say2, deps=deps,
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

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
        registry=registry or {"myrepo": _project()},
        jira_base_url="https://x.atlassian.net",
        jira_email="me@x.com",
        jira_token="t",
        execute=execute or (lambda *a, **kw: None),
        context=context,
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
    handle_message(text="init/ceph-api/306", user_id=ALLOWED, say=say, deps=deps)

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
    handle_message(text="init/ceph-api/999", user_id=ALLOWED, say=say, deps=deps)

    assert any("remote `999` 없음" in m for m in sent)
    assert any("305" in m and "306" in m for m in sent)
    assert store.get() is None


def test_init_rejects_unknown_repo(tmp_path, real_repo):
    from bot_lib.context import ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    deps = _deps(registry={"ceph-api": real_repo}, context=store)

    sent, say = _record_say()
    handle_message(text="init/typo-api/305", user_id=ALLOWED, say=say, deps=deps)

    assert any("모르는 repo" in m for m in sent)
    assert store.get() is None


def test_init_clear_removes_context(tmp_path):
    from bot_lib.context import Context, ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="myrepo", remote="origin"))

    deps = _deps(context=store)
    sent, say = _record_say()
    handle_message(text="init/clear", user_id=ALLOWED, say=say, deps=deps)

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


def test_three_token_uses_context_repo(tmp_path):
    from bot_lib.context import Context, ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="myrepo", remote="305"))

    calls, fake = _stub_execute()
    deps = _deps(execute=fake, context=store)

    sent, say = _record_say()
    handle_message(
        text="fix/CDS-99/null check",
        user_id=ALLOWED, say=say, deps=deps,
    )
    assert len(calls) == 1
    assert calls[0]["cmd"].repo == "myrepo"   # filled from context
    assert calls[0]["project"].remote == "305"  # context override applied


def test_three_token_without_context_returns_error(tmp_path):
    from bot_lib.context import ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))  # empty
    deps = _deps(context=store)
    sent, say = _record_say()
    handle_message(
        text="fix/CDS-99/x", user_id=ALLOWED, say=say, deps=deps
    )
    assert any("컨텍스트 미설정" in m for m in sent)


def test_four_token_ignores_context(tmp_path):
    """Explicit 4-token bypasses context — uses projects.md remote."""
    from bot_lib.context import Context, ContextStore
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="alpha", remote="999"))  # context says alpha/999

    calls, fake = _stub_execute()
    deps = _deps(
        execute=fake,
        registry={"myrepo": _project()},  # myrepo.remote = origin (default)
        context=store,
    )
    sent, say = _record_say()
    handle_message(
        text="fix/myrepo/CDS-1/x", user_id=ALLOWED, say=say, deps=deps
    )
    assert calls[0]["cmd"].repo == "myrepo"
    assert calls[0]["project"].remote == "origin"  # not "999" from context


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
        name="myrepo", path=str(repo_dirty), default_branch="main", remote="origin",
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
        name="myrepo", path=str(repo_dirty), default_branch="main", remote="origin",
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

import difflib
import re
from dataclasses import dataclass, field
from typing import Callable, Mapping

from bot_lib import commands, git_ops, orchestrator
from bot_lib.mutex import RepoMutex
from bot_lib.registry import Project


HELP_TEXT = (
    "사용법:\n"
    "  <type>/<repo>/<issue>/<instruction>\n\n"
    "type: fix | feat | refactor | chore | docs | test | perf\n"
    "issue: PROJ-123 형식\n"
    "instruction: 자유 텍스트\n\n"
    "예: fix/ceph-api/CDS-99/null check 추가\n"
    "도움말: `help` 또는 `도움말`\n"
    "복구: `cleanup/<repo>` (워킹 트리 초기화)"
)


CLEANUP_RE = re.compile(r"^cleanup/(\S+)$")


Say = Callable[[str], None]


@dataclass
class HandlerDeps:
    allowed_user_id: str
    registry: Mapping[str, Project]
    jira_base_url: str
    jira_email: str
    jira_token: str
    execute: Callable[..., orchestrator.JobOutcome] = orchestrator.execute_job
    mutex: RepoMutex = field(default_factory=RepoMutex)


def handle_message(*, text: str, user_id: str, say: Say, deps: HandlerDeps) -> None:
    """Single Slack DM → action. Side effects via say(); never raises."""
    # §10-1
    if user_id != deps.allowed_user_id:
        return

    # §6 도움말
    if commands.is_help(text):
        say(HELP_TEXT)
        return

    # §12 Q15 — cleanup/<repo>
    cleanup_repo = _match_cleanup(text)
    if cleanup_repo is not None:
        _handle_cleanup(cleanup_repo, deps, say)
        return

    # §6 명령 파싱
    try:
        cmd = commands.parse(text)
    except commands.CommandError as e:
        say(f"❌ {e}")
        return

    project = deps.registry.get(cmd.repo)
    if project is None:
        say(_unknown_repo_message(cmd.repo, deps.registry))
        return

    _run_with_mutex(cmd, project, deps, say)


# ---- mutex-guarded execute (§12 Q8) ----


def _run_with_mutex(cmd, project: Project, deps: HandlerDeps, say: Say) -> None:
    lock = deps.mutex.lock_for(project.name)
    if not lock.acquire(blocking=False):
        say(f"⏳ `{project.name}` 작업 중. 순서대로 처리되니 대기해 주세요.")
        lock.acquire()  # block until our turn

    try:
        say(f"⏳ {cmd.type}/{cmd.issue} 시작합니다.")
        outcome = deps.execute(
            cmd,
            project,
            jira_base_url=deps.jira_base_url,
            jira_email=deps.jira_email,
            jira_token=deps.jira_token,
            progress=say,
        )
        say(format_outcome(outcome))
    finally:
        lock.release()


# ---- cleanup (§12 Q15) ----


def _match_cleanup(text: str) -> str | None:
    m = CLEANUP_RE.match(text.strip())
    return m.group(1) if m else None


def _handle_cleanup(repo_name: str, deps: HandlerDeps, say: Say) -> None:
    project = deps.registry.get(repo_name)
    if project is None:
        say(_unknown_repo_message(repo_name, deps.registry))
        return

    lock = deps.mutex.lock_for(project.name)
    if not lock.acquire(blocking=False):
        say(f"⏳ `{project.name}` 작업 중. cleanup은 끝난 뒤에 실행됩니다.")
        lock.acquire()

    try:
        say(f"🧹 `{project.name}` 워킹 트리 초기화 중...")
        try:
            git_ops.run_git(project.path, "reset", "--hard", "HEAD")
            git_ops.run_git(project.path, "clean", "-fd")
            sha = git_ops.head_sha(project.path)
            say(f"✅ 초기화 완료. HEAD: `{sha[:10]}`")
        except git_ops.GitError as e:
            say(f"❌ cleanup 실패: {e}")
    finally:
        lock.release()


# ---- formatting ----


def _unknown_repo_message(repo: str, registry: Mapping[str, Project]) -> str:
    candidates = difflib.get_close_matches(repo, list(registry.keys()), n=3, cutoff=0.5)
    if candidates:
        suggestion = ", ".join(f"`{c}`" for c in candidates)
        return f"❌ 모르는 repo: `{repo}`. 혹시: {suggestion}?"
    available = ", ".join(f"`{name}`" for name in sorted(registry))
    return f"❌ 모르는 repo: `{repo}`. 등록된 repo: {available or '(없음)'}"


def format_outcome(outcome: orchestrator.JobOutcome) -> str:
    """One-message Slack summary (§7 step 14)."""
    if outcome.status == orchestrator.SUCCESS:
        lines = [f"✅ 완료 — `{outcome.branch}`"]
        if outcome.commit_sha:
            lines.append(f"커밋: `{outcome.commit_sha[:10]}`")
        if outcome.diff_stat:
            stat_tail = outcome.diff_stat.strip().splitlines()[-1] if outcome.diff_stat.strip() else ""
            if stat_tail:
                lines.append(f"diff: {stat_tail}")
        if outcome.test_status:
            attempts_part = f" (attempt {outcome.attempts}/3)" if outcome.attempts else ""
            lines.append(f"테스트: {outcome.test_status.upper()}{attempts_part}")
        usage_line = _format_usage(outcome.usage)
        if usage_line:
            lines.append(usage_line)
        if outcome.pr_url:
            lines.append(f"PR: {outcome.pr_url}")
        if outcome.message and outcome.message != "완료":
            lines.append(outcome.message)
        return "\n".join(lines)

    if outcome.status == orchestrator.BLOCKED:
        return f"⛔ 차단됨 — `{outcome.branch}`\n{outcome.message}"

    lines = [f"❌ 실패 — `{outcome.branch}`", outcome.message]
    if outcome.test_status:
        lines.append(f"테스트: {outcome.test_status.upper()}")
    if outcome.attempts:
        lines.append(f"attempts: {outcome.attempts}")
    usage_line = _format_usage(outcome.usage)
    if usage_line:
        lines.append(usage_line)
    return "\n".join(lines)


def _format_usage(usage) -> str:
    """One-line token + cost summary, or empty string if nothing was used."""
    if usage is None or usage.is_empty:
        return ""
    parts = [
        f"in={usage.input_tokens:,}",
        f"out={usage.output_tokens:,}",
    ]
    if usage.cache_read_input_tokens or usage.cache_creation_input_tokens:
        parts.append(
            f"cache(read={usage.cache_read_input_tokens:,}, "
            f"create={usage.cache_creation_input_tokens:,})"
        )
    cost_str = f" — ${usage.total_cost_usd:.4f}" if usage.total_cost_usd > 0 else ""
    return f"토큰: {' '.join(parts)}{cost_str}"

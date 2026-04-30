import difflib
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

from bot_lib import commands, orchestrator
from bot_lib.registry import Project


HELP_TEXT = (
    "사용법:\n"
    "  <type>/<repo>/<issue>/<instruction>\n\n"
    "type: fix | feat | refactor | chore | docs | test | perf\n"
    "issue: PROJ-123 형식\n"
    "instruction: 자유 텍스트\n\n"
    "예: fix/ceph-api/CDS-99/null check 추가\n"
    "도움말: `help` 또는 `도움말`"
)


Say = Callable[[str], None]


@dataclass(frozen=True)
class HandlerDeps:
    allowed_user_id: str
    registry: Mapping[str, Project]
    jira_base_url: str
    jira_email: str
    jira_token: str
    execute: Callable[..., orchestrator.JobOutcome] = orchestrator.execute_job


def handle_message(*, text: str, user_id: str, say: Say, deps: HandlerDeps) -> None:
    """Single Slack DM → action. Side effects via say(); never raises."""
    # §10-1
    if user_id != deps.allowed_user_id:
        return

    # §6 도움말 트리거
    if commands.is_help(text):
        say(HELP_TEXT)
        return

    # §6 명령 파싱
    try:
        cmd = commands.parse(text)
    except commands.CommandError as e:
        say(f"❌ {e}")
        return

    # repo 등록 검증 (§6 검증 표 마지막 행)
    project = deps.registry.get(cmd.repo)
    if project is None:
        say(_unknown_repo_message(cmd.repo, deps.registry))
        return

    # Invariant 4 — progress relay only
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
        if outcome.pr_url:
            lines.append(f"PR: {outcome.pr_url}")
        if outcome.message and outcome.message != "완료":
            lines.append(outcome.message)
        return "\n".join(lines)

    if outcome.status == orchestrator.BLOCKED:
        return f"⛔ 차단됨 — `{outcome.branch}`\n{outcome.message}"

    # FAILED
    lines = [f"❌ 실패 — `{outcome.branch}`", outcome.message]
    if outcome.test_status:
        lines.append(f"테스트: {outcome.test_status.upper()}")
    if outcome.attempts:
        lines.append(f"attempts: {outcome.attempts}")
    return "\n".join(lines)

import difflib
import re
from dataclasses import dataclass, field, replace
from typing import Callable, Mapping, Optional

from bot_lib import commands, git_ops, orchestrator
from bot_lib.context import Context, ContextStore
from bot_lib.mutex import RepoMutex
from bot_lib.registry import Project


HELP_TEXT = (
    "사용법:\n"
    "  <type>/<repo>/<issue>/<instruction>      (4토큰)\n"
    "  <type>/<issue>/<instruction>             (3토큰, init 후)\n\n"
    "type: fix | feat | refactor | chore | docs | test | perf\n"
    "issue: PROJ-123 형식\n"
    "instruction: 자유 텍스트\n\n"
    "예: fix/ceph-api/CDS-99/null check 추가\n"
    "    fix/CDS-99/null check 추가  (init 후)\n\n"
    "컨텍스트:\n"
    "  init/<repo>/<remote>  - 세션 컨텍스트 설정 (이후 3토큰 가능)\n"
    "  init/clear            - 컨텍스트 삭제\n"
    "  status                - 현재 컨텍스트·등록 repo 조회\n\n"
    "조회:\n"
    "  branch                - 현재 컨텍스트 repo 의 최근 10개 브랜치\n"
    "  branch <repo>         - 명시한 repo 의 최근 10개\n"
    "  branch all            - 전체 (최대 50개)\n"
    "  branch <repo> all     - 명시 repo 전체\n\n"
    "기타:\n"
    "  help / 도움말         - 이 안내\n"
    "  cleanup/<repo>        - 워킹 트리 초기화"
)


CLEANUP_RE = re.compile(r"^cleanup/(\S+)$")
INIT_RE = re.compile(r"^init/(\S+?)/(\S+)$")
BRANCH_RE = re.compile(r"^branch(?:\s+(.+))?$")
BRANCH_DEFAULT_LIMIT = 10
BRANCH_HARD_CAP = 50


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
    context: Optional[ContextStore] = None


def handle_message(*, text: str, user_id: str, say: Say, deps: HandlerDeps) -> None:
    """Single Slack DM → action. Side effects via say(); never raises."""
    # §10-1
    if user_id != deps.allowed_user_id:
        return

    stripped = text.strip()

    # §6 도움말
    if commands.is_help(text):
        say(HELP_TEXT)
        return

    # §B init/clear
    if stripped == "init/clear":
        _handle_init_clear(deps, say)
        return

    # §B status
    if stripped == "status":
        _handle_status(deps, say)
        return

    # §B init/<repo>/<remote>
    init_match = INIT_RE.match(stripped)
    if init_match:
        _handle_init(init_match.group(1), init_match.group(2), deps, say)
        return

    # /branch [<repo>] [all]
    branch_match = BRANCH_RE.match(stripped)
    if branch_match:
        _handle_branch(branch_match.group(1), deps, say)
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

    # 3토큰: cmd.repo is None → 컨텍스트에서 채움
    if cmd.repo is None:
        ctx = deps.context.get() if deps.context else None
        if ctx is None:
            say(
                "❌ 컨텍스트 미설정. 4토큰 형식을 쓰거나 `init/<repo>/<remote>` 로 컨텍스트 설정하세요.\n"
                "예: fix/ceph-api/CDS-99/x"
            )
            return
        cmd = replace(cmd, repo=ctx.repo)
        repo_remote_override = ctx.remote
    else:
        repo_remote_override = None

    project = deps.registry.get(cmd.repo)
    if project is None:
        say(_unknown_repo_message(cmd.repo, deps.registry))
        return

    # 3토큰 경로에서는 컨텍스트의 remote 가 projects.md default 를 덮어씀
    if repo_remote_override and repo_remote_override != project.remote:
        project = replace(project, remote=repo_remote_override)

    _run_with_mutex(cmd, project, deps, say)


# ---- mutex-guarded execute (§12 Q8) ----


def _run_with_mutex(cmd, project: Project, deps: HandlerDeps, say: Say) -> None:
    lock = deps.mutex.lock_for(project.name)
    if not lock.acquire(blocking=False):
        say(f"⏳ `{project.name}` 작업 중. 순서대로 처리되니 대기해 주세요.")
        lock.acquire()

    try:
        say(f"⏳ {cmd.type}/{cmd.issue} 시작합니다 ({project.name}/{project.remote})")
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


# ---- init / status / clear (§B) ----


def _handle_init(repo_name: str, remote: str, deps: HandlerDeps, say: Say) -> None:
    project = deps.registry.get(repo_name)
    if project is None:
        say(_unknown_repo_message(repo_name, deps.registry))
        return

    if not _git_remote_exists(project.path, remote):
        known = _list_git_remotes(project.path)
        known_str = ", ".join(f"`{r}`" for r in known) if known else "(없음)"
        say(
            f"❌ `{repo_name}` 에 remote `{remote}` 없음.\n"
            f"사용 가능: {known_str}"
        )
        return

    if deps.context is None:
        say("⚠️ 컨텍스트 저장소 미설정 (deps.context). 봇 설정 확인 필요.")
        return

    deps.context.set(Context(repo=repo_name, remote=remote))
    say(f"✅ 컨텍스트 설정: `{repo_name}` / `{remote}`\n이후 3토큰 명령 가능: `<type>/<issue>/<instruction>`")


def _handle_init_clear(deps: HandlerDeps, say: Say) -> None:
    if deps.context is None:
        say("⚠️ 컨텍스트 저장소 미설정.")
        return
    deps.context.clear()
    say("✅ 컨텍스트 삭제됨. 4토큰 형식만 사용 가능.")


def _handle_status(deps: HandlerDeps, say: Say) -> None:
    ctx = deps.context.get() if deps.context else None
    repos = sorted(deps.registry)
    repo_lines = []
    for name in repos:
        p = deps.registry[name]
        repo_lines.append(f"  • `{name}` → {p.default_branch} / {p.remote}")
    repo_block = "\n".join(repo_lines) if repo_lines else "  (없음)"

    if ctx is None:
        say(f"현재 컨텍스트: *없음* (4토큰 형식 사용)\n등록된 repo:\n{repo_block}")
    else:
        say(
            f"현재 컨텍스트: `{ctx.repo}` / `{ctx.remote}`\n"
            f"등록된 repo:\n{repo_block}"
        )


def _git_remote_exists(repo_path: str, remote: str) -> bool:
    return remote in _list_git_remotes(repo_path)


def _list_git_remotes(repo_path: str) -> list[str]:
    try:
        out = git_ops.run_git(repo_path, "remote")
    except git_ops.GitError:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


# ---- /branch ----


def _handle_branch(arg: Optional[str], deps: HandlerDeps, say: Say) -> None:
    """`/branch [<repo>] [all]` — list recent branches of the chosen repo."""
    repo_name, show_all = _parse_branch_arg(arg, deps)

    if repo_name is None:
        say(
            "❌ 컨텍스트 미설정. `branch <repo>` 로 명시하거나 `init/<repo>/<remote>` 로 설정하세요.\n"
            f"등록된 repo: {', '.join(f'`{n}`' for n in sorted(deps.registry)) or '(없음)'}"
        )
        return

    project = deps.registry.get(repo_name)
    if project is None:
        say(_unknown_repo_message(repo_name, deps.registry))
        return

    limit = BRANCH_HARD_CAP if show_all else BRANCH_DEFAULT_LIMIT
    try:
        branches, total = git_ops.list_local_branches(project.path, limit=limit)
    except git_ops.GitError as e:
        say(f"❌ 브랜치 조회 실패: {e}")
        return

    say(_format_branch_list(project, branches, total, show_all))


def _parse_branch_arg(arg: Optional[str], deps: HandlerDeps) -> tuple[Optional[str], bool]:
    """Return (repo_name, show_all). repo_name is None when neither
    explicit nor context can resolve it."""
    show_all = False
    explicit_repo: Optional[str] = None

    if arg:
        tokens = arg.strip().split()
        for tok in tokens:
            if tok == "all":
                show_all = True
            elif explicit_repo is None:
                explicit_repo = tok
            # extra tokens silently ignored — keeps 'all <repo>' working both ways

    if explicit_repo is not None:
        return explicit_repo, show_all

    ctx = deps.context.get() if deps.context else None
    return (ctx.repo if ctx else None), show_all


def _format_branch_list(project, branches, total: int, show_all: bool) -> str:
    if not branches:
        return f"`{project.name}` 에 브랜치 없음."

    current = next((b for b in branches if b.is_current), None)
    header_cur = f"현재: `{current.name}`" if current else "(detached HEAD)"
    shown = len(branches)
    if show_all or shown == total:
        header = f"*{project.name}* ({header_cur}) — 전체 {total}개:"
    else:
        header = f"*{project.name}* ({header_cur}) — {total}개 중 최근 {shown}:"

    name_w = max(len(b.name) for b in branches)
    rows = []
    for b in branches:
        mark = " ⭐" if b.is_current else ""
        rows.append(f"  {b.name.ljust(name_w)}  · {b.age} · {b.author}{mark}")

    footer = ""
    if not show_all and total > shown:
        footer = f"\n  … 외 {total - shown}개. `branch {project.name} all` 로 전체 보기"

    return header + "\n" + "\n".join(rows) + footer


# ---- cleanup (§12 Q15) ----


def _match_cleanup(text: str) -> Optional[str]:
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

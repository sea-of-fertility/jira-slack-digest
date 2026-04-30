import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from bot_lib import claude_runner, git_ops, jira_client, test_runner
from bot_lib.claude_runner import TokenUsage
from bot_lib.commands import ParsedCmd
from bot_lib.registry import Project

SUCCESS = "success"
BLOCKED = "blocked"
FAILED = "failed"

MAX_ATTEMPTS = 3
JOB_CAP_SECONDS = 30 * 60  # §12 Q11d

# Where bot.py / bot_lib live. Used to refuse self-modification — branching
# off main would yank these files out of the working tree mid-run.
_BOT_DIR = Path(__file__).resolve().parent.parent


def _is_self_repo(project_path: str) -> bool:
    return Path(project_path).resolve() == _BOT_DIR


@dataclass(frozen=True)
class JobOutcome:
    status: str
    branch: str
    commit_sha: Optional[str]
    diff_stat: str
    test_status: str
    pr_url: Optional[str]
    message: str
    attempts: int = 0
    usage: TokenUsage = field(default_factory=TokenUsage)


Progress = Callable[[str], None]


def execute_job(
    cmd: ParsedCmd,
    project: Project,
    *,
    jira_base_url: str,
    jira_email: str,
    jira_token: str,
    progress: Optional[Progress] = None,
    job_cap_seconds: int = JOB_CAP_SECONDS,
) -> JobOutcome:
    """Run a single Slack-triggered job end-to-end (plan.md §7).

    Cycle (a): happy path + early aborts.
    Cycle (b): test-failure retry loop with --resume + stateless fallback,
               no-incremental-change short-circuit, and the 30-minute cap.
    """
    say = progress or (lambda _msg: None)
    start = time.monotonic()
    deadline = start + job_cap_seconds
    branch = f"{cmd.type}/{cmd.issue}"

    # Refuse self-modification. Branching off main here would swap our own
    # source out of the working tree, breaking launchd restarts and leaving
    # claude with an empty repo to edit.
    if _is_self_repo(project.path):
        return _blocked(
            branch,
            "봇이 자기 자신의 repo는 수정하지 않습니다 (self-modification disabled).",
        )

    # §7 step 6
    if not git_ops.is_clean(project.path):
        return _blocked(branch, f"워킹 트리 dirty. cleanup/{project.name} 로 초기화 가능.")

    # §7 step 5
    say(f"Jira {cmd.issue} 로드 중")
    issue = jira_client.fetch_issue(jira_base_url, jira_email, jira_token, cmd.issue)

    # §7 step 7
    _prepare_branch(project, branch, say)

    # §7 step 8 + 10 + 11 — claude + tests with retry
    initial_prompt = _build_prompt(cmd, issue)
    loop_result = _run_with_retry(project, initial_prompt, deadline, say)

    if loop_result.aborted_message is not None:
        # claude itself failed, or cap reached, or no incremental edit
        return _failed(
            branch,
            "",
            loop_result.test_status,
            None,
            loop_result.aborted_message,
            attempts=loop_result.attempts,
            usage=loop_result.usage,
        )

    # §7 step 9 — claude must have edited at least once
    if git_ops.is_clean(project.path):
        return _failed(branch, "", "", None, "claude가 파일을 편집하지 않음", usage=loop_result.usage)

    test_status = loop_result.test_status
    if test_status not in (test_runner.PASS, test_runner.SKIP):
        return _failed(
            branch,
            "",
            test_status,
            None,
            f"테스트 {test_status} (재시도 {loop_result.attempts}회 소진)",
            attempts=loop_result.attempts,
            usage=loop_result.usage,
        )

    # §7 step 12
    msg_tail = (
        f"Tests: PASS (attempt {loop_result.attempts}/{MAX_ATTEMPTS})"
        if test_status == test_runner.PASS
        else "Tests: SKIPPED"
    )
    commit_msg = _build_commit_msg(cmd, issue, msg_tail)
    sha = git_ops.commit_all(project.path, commit_msg)
    diff = git_ops.run_git(project.path, "show", "--stat", "--format=", "HEAD").strip()

    # §7 step 13
    say("push 중")
    try:
        git_ops.push(project.path, branch, remote=project.remote)
    except git_ops.GitError as e:
        return JobOutcome(
            status=SUCCESS,
            branch=branch,
            commit_sha=sha,
            diff_stat=diff,
            test_status=test_status,
            pr_url=None,
            message=f"commit 완료, push 실패: {e}",
            attempts=loop_result.attempts,
            usage=loop_result.usage,
        )

    pr_url = _create_pr(project.path, cmd, issue)
    say("완료")
    return JobOutcome(
        status=SUCCESS,
        branch=branch,
        commit_sha=sha,
        diff_stat=diff,
        test_status=test_status,
        pr_url=pr_url,
        message="완료",
        attempts=loop_result.attempts,
        usage=loop_result.usage,
    )


# ---- retry loop (§7 step 11, §12 Q11) ----


@dataclass(frozen=True)
class _LoopResult:
    test_status: str           # last seen status, may be ""
    attempts: int              # how many claude calls we made (1..MAX_ATTEMPTS)
    aborted_message: Optional[str]   # set iff loop ended without a usable test result
    usage: TokenUsage = field(default_factory=TokenUsage)


def _run_with_retry(
    project: Project,
    initial_prompt: str,
    deadline: float,
    say: Progress,
) -> _LoopResult:
    """Drive the §7 step 11 loop. SKIP and PASS exit cleanly; FAIL/TIMEOUT
    feeds the failure back to claude up to MAX_ATTEMPTS.

    Stops early when an attempt makes no incremental edit (Q11) or when the
    job-wide cap is reached (Q11d).
    """
    session_id: Optional[str] = None
    last_test: Optional[test_runner.RunResult] = None
    last_snapshot: Optional[str] = None
    attempts = 0
    usage = TokenUsage()

    for n in range(1, MAX_ATTEMPTS + 1):
        if time.monotonic() >= deadline:
            return _LoopResult(
                test_status=last_test.status if last_test else "",
                attempts=attempts,
                aborted_message=f"30분 cap 도달 (attempt {n})",
                usage=usage,
            )

        attempts = n
        prompt = initial_prompt if n == 1 else _retry_prompt(last_test)
        say(f"attempt {n}/{MAX_ATTEMPTS}: claude 호출")
        run = _call_claude_with_fallback(project.path, prompt, session_id, say)
        usage = usage + run.usage
        if run.returncode != 0:
            return _LoopResult(
                test_status=last_test.status if last_test else "",
                attempts=attempts,
                aborted_message=f"claude 실패 (rc={run.returncode}): {run.stderr.strip()[:200]}",
                usage=usage,
            )
        if run.session_id:
            session_id = run.session_id

        # Q11 — incremental diff check (skip on first attempt: nothing to compare)
        snapshot = _workspace_hash(project.path)
        if n > 1 and snapshot == last_snapshot:
            return _LoopResult(
                test_status=last_test.status if last_test else "",
                attempts=attempts,
                aborted_message=f"attempt {n}: 추가 편집 없음, 재시도 종료",
                usage=usage,
            )
        last_snapshot = snapshot

        # §7 step 10
        say(f"attempt {n}: 테스트 실행")
        last_test = test_runner.run_tests(
            project.path, project.test_cmd, project.test_timeout
        )
        if last_test.status in (test_runner.PASS, test_runner.SKIP):
            return _LoopResult(
                test_status=last_test.status, attempts=attempts,
                aborted_message=None, usage=usage,
            )
        say(f"attempt {n}: {last_test.status}")
        # else: FAIL or TIMEOUT — try again

    return _LoopResult(
        test_status=last_test.status if last_test else "",
        attempts=attempts,
        aborted_message=None,
        usage=usage,
    )


def _call_claude_with_fallback(
    repo: str, prompt: str, session_id: Optional[str], say: Progress
) -> claude_runner.ClaudeRun:
    """§12 Q11c — try --resume first when a session_id exists; on any failure
    (CLI raises or non-zero exit) fall back to a stateless call."""
    if session_id:
        try:
            run = claude_runner.run_claude(repo, prompt, resume=session_id)
            if run.returncode == 0:
                return run
            say(f"--resume 실패 (rc={run.returncode}), stateless 폴백")
        except claude_runner.ClaudeError as e:
            say(f"--resume 예외 ({e}), stateless 폴백")
    return claude_runner.run_claude(repo, prompt)


def _retry_prompt(last: Optional[test_runner.RunResult]) -> str:
    if last is None:
        return "이전 시도가 실패했습니다. 다시 검토 후 수정해 주세요."
    log = (last.stderr or last.stdout or "").strip()[-2000:]
    return (
        "이전 attempt의 테스트가 실패했습니다. 같은 브랜치 위에서 추가 교정해 주세요.\n\n"
        f"--- 테스트 결과 ({last.status}, returncode={last.returncode}) ---\n"
        f"{log}"
    )


def _workspace_hash(repo: str) -> str:
    """Stable hash of the working tree (modified + untracked).

    Side effect: stages everything via `git add -A`. Benign — commit_all does
    the same later, and re-staging across attempts is idempotent.
    """
    git_ops.run_git(repo, "add", "-A")
    return git_ops.run_git(repo, "write-tree").strip()


# ---- branch prep + helpers ----


def _prepare_branch(project: Project, branch: str, say: Progress) -> None:
    if git_ops.branch_exists_local(project.path, branch):
        say(f"기존 브랜치 {branch} 재사용")
        git_ops.checkout(project.path, branch)
        return
    if git_ops.branch_exists_remote(project.path, branch, remote=project.remote):
        say(f"원격 브랜치 {branch} fetch")
        git_ops.fetch_and_track(project.path, branch, remote=project.remote)
        return
    say(f"신규 브랜치 {branch} 생성 (base: {project.default_branch})")
    try:
        git_ops.run_git(project.path, "pull", project.remote, project.default_branch)
    except git_ops.GitError:
        pass
    git_ops.create_branch_from(project.path, branch, project.default_branch)


def _build_prompt(cmd: ParsedCmd, issue: jira_client.JiraIssue) -> str:
    parts = [
        f"Jira 이슈 {issue.key}: {issue.title}",
        f"본문:\n{issue.description or '(none)'}",
        f"최근 댓글:\n{issue.comments_text or '(none)'}",
    ]
    if cmd.instruction:
        parts.append(f"--- 사용자 지시 ---\n{cmd.instruction}")
    return "\n\n".join(parts)


def _build_commit_msg(cmd: ParsedCmd, issue: jira_client.JiraIssue, msg_tail: str) -> str:
    parts = [f"{cmd.type}({cmd.issue}): {issue.title}"]
    if cmd.instruction:
        parts.append(cmd.instruction)
    parts.append(msg_tail)
    return "\n\n".join(parts)


def _create_pr(repo: str, cmd: ParsedCmd, issue: jira_client.JiraIssue) -> Optional[str]:
    title = f"{cmd.type}({cmd.issue}): {issue.title}"
    body = (
        f"{cmd.instruction}\n\nCloses {cmd.issue}"
        if cmd.instruction
        else f"Closes {cmd.issue}"
    )
    proc = subprocess.run(
        ["gh", "pr", "create", "--title", title, "--body", body],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _blocked(branch: str, message: str) -> JobOutcome:
    return JobOutcome(
        status=BLOCKED,
        branch=branch,
        commit_sha=None,
        diff_stat="",
        test_status="",
        pr_url=None,
        message=message,
    )


def _failed(
    branch: str,
    diff: str,
    test_status: str,
    sha: Optional[str],
    message: str,
    attempts: int = 0,
    usage: TokenUsage = TokenUsage(),
) -> JobOutcome:
    return JobOutcome(
        status=FAILED,
        branch=branch,
        commit_sha=sha,
        diff_stat=diff,
        test_status=test_status,
        pr_url=None,
        message=message,
        attempts=attempts,
        usage=usage,
    )

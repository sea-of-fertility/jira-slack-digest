import subprocess
from dataclasses import dataclass
from typing import Callable, Optional

from bot_lib import claude_runner, git_ops, jira_client, test_runner
from bot_lib.commands import ParsedCmd
from bot_lib.registry import Project

SUCCESS = "success"
BLOCKED = "blocked"
FAILED = "failed"


@dataclass(frozen=True)
class JobOutcome:
    status: str
    branch: str
    commit_sha: Optional[str]
    diff_stat: str
    test_status: str
    pr_url: Optional[str]
    message: str


Progress = Callable[[str], None]


def execute_job(
    cmd: ParsedCmd,
    project: Project,
    *,
    jira_base_url: str,
    jira_email: str,
    jira_token: str,
    progress: Optional[Progress] = None,
) -> JobOutcome:
    """Run a single Slack-triggered job end-to-end (plan.md §7).

    Cycle (a) scope: happy path + early-abort on dirty/no-changes/test-fail.
    Retry loop, mutex, and cleanup live in later cycles.
    """
    say = progress or (lambda _msg: None)
    branch = f"{cmd.type}/{cmd.issue}"

    # §7 step 6
    if not git_ops.is_clean(project.path):
        return _blocked(branch, f"워킹 트리 dirty. cleanup/{project.name} 로 초기화 가능.")

    # §7 step 5
    say(f"Jira {cmd.issue} 로드 중")
    issue = jira_client.fetch_issue(jira_base_url, jira_email, jira_token, cmd.issue)

    # §7 step 7
    _prepare_branch(project, branch, say)

    # §7 step 8
    say("claude -p 호출 중")
    run = claude_runner.run_claude(project.path, _build_prompt(cmd, issue))
    if run.returncode != 0:
        return _failed(branch, "", "", None, f"claude 실패: {run.stderr.strip()[:200]}")

    # §7 step 9 — `is_clean` covers both modified and untracked files
    if git_ops.is_clean(project.path):
        return _failed(branch, "", "", None, "claude가 파일을 편집하지 않음")

    # §7 step 10 (retry loop deferred to cycle b)
    say("테스트 실행 중")
    tr = test_runner.run_tests(project.path, project.test_cmd, project.test_timeout)
    if tr.status in (test_runner.FAIL, test_runner.TIMEOUT):
        return _failed(branch, "", tr.status, None, f"테스트 {tr.status} (재시도는 cycle b에서)")

    # §7 step 12
    msg_tail = "Tests: PASS (attempt 1/3)" if tr.status == test_runner.PASS else "Tests: SKIPPED"
    commit_msg = f"{cmd.type}({cmd.issue}): {issue.title}\n\n{cmd.instruction}\n\n{msg_tail}"
    sha = git_ops.commit_all(project.path, commit_msg)
    # post-commit stat for the Slack report (covers tracked + previously-untracked)
    diff = git_ops.run_git(project.path, "show", "--stat", "--format=", "HEAD").strip()

    # §7 step 13
    say("push 중")
    try:
        git_ops.push(project.path, branch)
    except git_ops.GitError as e:
        return JobOutcome(
            status=SUCCESS,
            branch=branch,
            commit_sha=sha,
            diff_stat=diff,
            test_status=tr.status,
            pr_url=None,
            message=f"commit 완료, push 실패: {e}",
        )

    pr_url = _create_pr(project.path, cmd, issue)
    say("완료")
    return JobOutcome(
        status=SUCCESS,
        branch=branch,
        commit_sha=sha,
        diff_stat=diff,
        test_status=tr.status,
        pr_url=pr_url,
        message="완료",
    )


def _prepare_branch(project: Project, branch: str, say: Progress) -> None:
    if git_ops.branch_exists_local(project.path, branch):
        say(f"기존 브랜치 {branch} 재사용")
        git_ops.checkout(project.path, branch)
        return
    if git_ops.branch_exists_remote(project.path, branch):
        say(f"원격 브랜치 {branch} fetch")
        git_ops.fetch_and_track(project.path, branch)
        return
    say(f"신규 브랜치 {branch} 생성 (base: {project.default_branch})")
    try:
        git_ops.run_git(project.path, "pull", "origin", project.default_branch)
    except git_ops.GitError:
        pass  # offline / no remote configured — fall through to local branch
    git_ops.create_branch_from(project.path, branch, project.default_branch)


def _build_prompt(cmd: ParsedCmd, issue: jira_client.JiraIssue) -> str:
    return (
        f"Jira 이슈 {issue.key}: {issue.title}\n\n"
        f"본문:\n{issue.description or '(none)'}\n\n"
        f"최근 댓글:\n{issue.comments_text or '(none)'}\n\n"
        f"--- 사용자 지시 ---\n{cmd.instruction}"
    )


def _create_pr(repo: str, cmd: ParsedCmd, issue: jira_client.JiraIssue) -> Optional[str]:
    title = f"{cmd.type}({cmd.issue}): {issue.title}"
    body = f"{cmd.instruction}\n\nCloses {cmd.issue}"
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


def _failed(branch: str, diff: str, test_status: str, sha: Optional[str], message: str) -> JobOutcome:
    return JobOutcome(
        status=FAILED,
        branch=branch,
        commit_sha=sha,
        diff_stat=diff,
        test_status=test_status,
        pr_url=None,
        message=message,
    )

import subprocess


class GitError(Exception):
    """git command exited non-zero. Wraps stderr for the bot to relay."""


def run_git(repo: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def is_clean(repo: str) -> bool:
    return run_git(repo, "status", "--porcelain").strip() == ""


def current_branch(repo: str) -> str:
    return run_git(repo, "symbolic-ref", "--short", "HEAD").strip()


def head_sha(repo: str) -> str:
    return run_git(repo, "rev-parse", "HEAD").strip()


def branch_exists_local(repo: str, name: str) -> bool:
    try:
        run_git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{name}")
        return True
    except GitError:
        return False


def create_branch_from(repo: str, name: str, base: str) -> None:
    run_git(repo, "checkout", "-b", name, base)


def checkout(repo: str, branch: str) -> None:
    run_git(repo, "checkout", branch)


def commit_all(repo: str, message: str) -> str:
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-m", message)
    return head_sha(repo)


def diff_stat(repo: str, ref: str = "HEAD") -> str:
    return run_git(repo, "diff", "--stat", ref)


def commits_behind(repo: str, branch: str, base: str) -> int:
    out = run_git(repo, "rev-list", "--count", f"{branch}..{base}").strip()
    return int(out)


def branch_exists_remote(repo: str, name: str, remote: str = "origin") -> bool:
    try:
        out = run_git(repo, "ls-remote", "--heads", remote, name)
    except GitError:
        return False
    return bool(out.strip())


def fetch_and_track(repo: str, name: str, remote: str = "origin") -> None:
    """Fetch a remote-only branch and create a local tracking branch."""
    run_git(repo, "fetch", remote, name)
    run_git(repo, "checkout", "-b", name, f"{remote}/{name}")


def push(repo: str, branch: str, remote: str = "origin") -> None:
    run_git(repo, "push", "-u", remote, branch)

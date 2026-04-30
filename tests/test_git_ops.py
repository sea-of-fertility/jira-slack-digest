import subprocess

import pytest

from pathlib import Path

from bot_lib.git_ops import (
    GitError,
    branch_exists_local,
    branch_exists_remote,
    checkout,
    commit_all,
    commits_behind,
    create_branch_from,
    current_branch,
    diff_stat,
    fetch_and_track,
    head_sha,
    is_clean,
    push,
    run_git,
)


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )


@pytest.fixture
def repo(tmp_path):
    """Clean repo on `main` with one commit (README.md = 'hi')."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.email", "test@example.com")
    _git(r, "config", "user.name", "Test")
    (r / "README.md").write_text("hi\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-m", "init")
    return str(r)


@pytest.fixture
def repo_with_remote(tmp_path):
    """Local repo + bare 'origin' remote with main pushed."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare", "-b", "main")

    local = tmp_path / "local"
    local.mkdir()
    _git(local, "init", "-b", "main")
    _git(local, "config", "user.email", "t@x.com")
    _git(local, "config", "user.name", "t")
    (local / "README.md").write_text("hi\n")
    _git(local, "add", "-A")
    _git(local, "commit", "-m", "init")
    _git(local, "remote", "add", "origin", str(remote))
    _git(local, "push", "-u", "origin", "main")
    return str(local), str(remote)


# ---- run_git ----


def test_run_git_returns_stdout(repo):
    out = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    assert out.strip() == "main"


def test_run_git_raises_on_failure(repo):
    with pytest.raises(GitError):
        run_git(repo, "checkout", "nope-doesnt-exist")


# ---- is_clean ----


def test_is_clean_after_init(repo):
    assert is_clean(repo) is True


def test_is_clean_false_after_modification(repo, tmp_path):
    (tmp_path / "repo" / "README.md").write_text("modified\n")
    assert is_clean(repo) is False


def test_is_clean_false_with_untracked(repo, tmp_path):
    (tmp_path / "repo" / "newfile.txt").write_text("hi")
    assert is_clean(repo) is False


# ---- current_branch ----


def test_current_branch_main(repo):
    assert current_branch(repo) == "main"


def test_current_branch_after_checkout(repo):
    _git(repo, "checkout", "-b", "feature/x")
    assert current_branch(repo) == "feature/x"


# ---- head_sha ----


def test_head_sha_is_40_hex(repo):
    sha = head_sha(repo)
    assert len(sha) == 40
    assert all(c in "0123456789abcdef" for c in sha)


# ---- branch_exists_local ----


def test_branch_exists_local_true_for_main(repo):
    assert branch_exists_local(repo, "main") is True


def test_branch_exists_local_false_for_unknown(repo):
    assert branch_exists_local(repo, "nope") is False


def test_branch_exists_local_after_create(repo):
    _git(repo, "branch", "feat/x")
    assert branch_exists_local(repo, "feat/x") is True


# ---- create_branch_from ----


def test_create_branch_from_switches_head(repo):
    create_branch_from(repo, "fix/CDS-1", "main")
    assert current_branch(repo) == "fix/CDS-1"


def test_create_branch_from_inherits_base_sha(repo):
    base_sha = head_sha(repo)
    create_branch_from(repo, "fix/CDS-2", "main")
    assert head_sha(repo) == base_sha


def test_create_branch_from_unknown_base_raises(repo):
    with pytest.raises(GitError):
        create_branch_from(repo, "fix/CDS-3", "no-such-base")


# ---- checkout ----


def test_checkout_existing_branch(repo):
    _git(repo, "branch", "feat/y")
    checkout(repo, "feat/y")
    assert current_branch(repo) == "feat/y"


def test_checkout_unknown_branch_raises(repo):
    with pytest.raises(GitError):
        checkout(repo, "nope")


# ---- commit_all ----


def test_commit_all_creates_commit(repo):
    before = head_sha(repo)
    (Path(repo) / "f.txt").write_text("data")
    sha = commit_all(repo, "feat: f")
    assert sha != before
    assert head_sha(repo) == sha


def test_commit_all_returns_full_sha(repo):
    (Path(repo) / "f.txt").write_text("data")
    sha = commit_all(repo, "feat: f")
    assert len(sha) == 40


def test_commit_all_message_landed(repo):
    (Path(repo) / "f.txt").write_text("data")
    commit_all(repo, "fix(CDS-99): something")
    msg = run_git(repo, "log", "-1", "--pretty=%s")
    assert "fix(CDS-99): something" in msg


def test_commit_all_includes_untracked(repo):
    (Path(repo) / "newfile.py").write_text("print(1)\n")
    commit_all(repo, "add newfile")
    files = run_git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
    assert "newfile.py" in files


def test_commit_all_with_no_changes_raises(repo):
    with pytest.raises(GitError):
        commit_all(repo, "empty")


# ---- diff_stat ----


def test_diff_stat_shows_modified_file(repo):
    (Path(repo) / "README.md").write_text("changed\n")
    out = diff_stat(repo)
    assert "README.md" in out


def test_diff_stat_empty_when_clean(repo):
    assert diff_stat(repo).strip() == ""


# ---- commits_behind ----


def test_commits_behind_zero_on_same_commit(repo):
    _git(repo, "checkout", "-b", "feat")
    assert commits_behind(repo, "feat", "main") == 0


def test_commits_behind_counts_base_advance(repo):
    _git(repo, "checkout", "-b", "feat")
    _git(repo, "checkout", "main")
    (Path(repo) / "a").write_text("a")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "c1")
    (Path(repo) / "b").write_text("b")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "c2")
    _git(repo, "checkout", "feat")
    assert commits_behind(repo, "feat", "main") == 2


# ---- branch_exists_remote ----


def test_branch_exists_remote_true_for_main(repo_with_remote):
    local, _ = repo_with_remote
    assert branch_exists_remote(local, "main") is True


def test_branch_exists_remote_false_for_unknown(repo_with_remote):
    local, _ = repo_with_remote
    assert branch_exists_remote(local, "nope") is False


def test_branch_exists_remote_detects_branch_pushed_by_other(repo_with_remote):
    local, remote = repo_with_remote
    # Simulate another clone pushing a new branch
    other = Path(remote).parent / "other"
    other.mkdir()
    _git(other, "clone", remote, ".")
    _git(other, "config", "user.email", "o@x.com")
    _git(other, "config", "user.name", "o")
    _git(other, "checkout", "-b", "fix/CDS-99")
    (other / "x").write_text("x")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "c")
    _git(other, "push", "-u", "origin", "fix/CDS-99")

    assert branch_exists_remote(local, "fix/CDS-99") is True


# ---- fetch_and_track ----


def test_fetch_and_track_creates_local_tracking_branch(repo_with_remote):
    local, remote = repo_with_remote
    # Push a remote-only branch via a side clone
    other = Path(remote).parent / "other2"
    other.mkdir()
    _git(other, "clone", remote, ".")
    _git(other, "config", "user.email", "o@x.com")
    _git(other, "config", "user.name", "o")
    _git(other, "checkout", "-b", "feat/new")
    (other / "y").write_text("y")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "c")
    _git(other, "push", "-u", "origin", "feat/new")

    assert branch_exists_local(local, "feat/new") is False
    fetch_and_track(local, "feat/new")
    assert branch_exists_local(local, "feat/new") is True
    assert current_branch(local) == "feat/new"


# ---- push ----


def test_push_creates_remote_branch(repo_with_remote):
    local, remote = repo_with_remote
    _git(local, "checkout", "-b", "fix/CDS-1")
    (Path(local) / "f").write_text("f")
    _git(local, "add", "-A")
    _git(local, "commit", "-m", "c")
    push(local, "fix/CDS-1")
    assert branch_exists_remote(local, "fix/CDS-1") is True


def test_push_unknown_remote_raises(repo):
    _git(repo, "checkout", "-b", "feat")
    with pytest.raises(GitError):
        push(repo, "feat")

import io
import os
import stat
from pathlib import Path

import pytest

from bot_lib import setup_wizard
from bot_lib.setup_wizard import (
    REQUIRED_ENV_KEYS,
    _read_env_file,
    _write_env_file,
    missing_env_keys,
    run,
)


# ---- helpers ----


def _scripted_input(monkeypatch, plain: list[str], secret: list[str] | None = None):
    """Wire up `input()` and `getpass.getpass()` to consume scripted answers
    in order. Each call pops the next entry; raises if exhausted.
    """
    plain_iter = iter(plain)
    secret_iter = iter(secret or [])

    def fake_input(prompt=""):
        try:
            return next(plain_iter)
        except StopIteration:
            raise AssertionError(f"unexpected extra input(): {prompt!r}")

    def fake_getpass(prompt=""):
        try:
            return next(secret_iter)
        except StopIteration:
            raise AssertionError(f"unexpected extra getpass(): {prompt!r}")

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr("bot_lib.setup_wizard.getpass.getpass", fake_getpass)


# ---- missing_env_keys ----


def test_missing_env_keys_returns_all_when_dict_empty():
    assert missing_env_keys({}) == list(REQUIRED_ENV_KEYS)


def test_missing_env_keys_returns_empty_when_all_set():
    full = {k: "x" for k in REQUIRED_ENV_KEYS}
    assert missing_env_keys(full) == []


def test_missing_env_keys_treats_blank_string_as_missing():
    full = {k: "x" for k in REQUIRED_ENV_KEYS}
    full["JIRA_API_TOKEN"] = "   "
    assert missing_env_keys(full) == ["JIRA_API_TOKEN"]


# ---- _read_env_file / _write_env_file round-trip ----


def test_write_env_file_sets_permissions_600(tmp_path):
    env = tmp_path / ".env"
    _write_env_file(env, {k: "v" for k in REQUIRED_ENV_KEYS})
    mode = stat.S_IMODE(env.stat().st_mode)
    assert mode == 0o600


def test_write_env_file_quotes_values(tmp_path):
    env = tmp_path / ".env"
    _write_env_file(env, {"JIRA_BASE_URL": "https://x.atlassian.net",
                          "JIRA_EMAIL": "me@x.com",
                          "JIRA_API_TOKEN": "tok",
                          "SLACK_BOT_TOKEN": "xoxb",
                          "SLACK_APP_TOKEN": "xapp",
                          "SLACK_USER_ID": "U1"})
    body = env.read_text()
    assert 'JIRA_BASE_URL="https://x.atlassian.net"' in body
    assert 'JIRA_API_TOKEN="tok"' in body


def test_write_env_file_preserves_extra_keys(tmp_path):
    env = tmp_path / ".env"
    values = {k: "v" for k in REQUIRED_ENV_KEYS}
    values["BOT_USER"] = "hjpark"
    values["LLM_BACKEND"] = "cli"
    _write_env_file(env, values)
    parsed = _read_env_file(env)
    assert parsed["BOT_USER"] == "hjpark"
    assert parsed["LLM_BACKEND"] == "cli"


def test_read_env_file_handles_quoted_and_unquoted(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        '# comment\n'
        'JIRA_BASE_URL="https://x.com"\n'
        'JIRA_EMAIL=me@x.com\n'
        "BOT_USER='hjpark'\n"
        "\n"
    )
    parsed = _read_env_file(env)
    assert parsed["JIRA_BASE_URL"] == "https://x.com"
    assert parsed["JIRA_EMAIL"] == "me@x.com"
    assert parsed["BOT_USER"] == "hjpark"


def test_read_env_file_returns_empty_when_missing(tmp_path):
    assert _read_env_file(tmp_path / "nope.env") == {}


# ---- run() — end-to-end with mocked I/O ----


def _full_inputs():
    """Inputs in the order _FIELDS asks (3 plain, 3 secret, then 'n' for repo)."""
    return {
        "plain": [
            "https://x.atlassian.net",   # JIRA_BASE_URL
            "me@x.com",                   # JIRA_EMAIL
            "U01ABC23DEF",                # SLACK_USER_ID
            "n",                          # repo prompt: skip
        ],
        "secret": [
            "tok-jira-12345678901234",
            "xoxb-slack-bot-12345",
            "xapp-slack-app-12345",
        ],
    }


def test_run_writes_env_file_when_missing(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    projects_path = tmp_path / "projects.md"
    # Empty registry → repo prompt; we skip with 'n'
    projects_path.write_text("")
    inputs = _full_inputs()
    # Field order in _FIELDS interleaves plain/secret. Build streams in that order.
    plain = ["https://x.atlassian.net", "me@x.com", "U01ABC23DEF"]
    secret = ["tok-jira-12345678901234",
              "xoxb-slack-bot-12345",
              "xapp-slack-app-12345"]
    plain.append("n")  # repo prompt skip
    _scripted_input(monkeypatch, plain, secret)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_USER_ID", raising=False)
    stream = io.StringIO()
    run(env_path, projects_path, stream=stream)
    parsed = _read_env_file(env_path)
    assert parsed["JIRA_BASE_URL"] == "https://x.atlassian.net"
    assert parsed["JIRA_EMAIL"] == "me@x.com"
    assert parsed["JIRA_API_TOKEN"] == "tok-jira-12345678901234"
    assert parsed["SLACK_BOT_TOKEN"] == "xoxb-slack-bot-12345"
    assert parsed["SLACK_USER_ID"] == "U01ABC23DEF"


def test_run_does_not_echo_secret_in_full(tmp_path, monkeypatch, capsys):
    env_path = tmp_path / ".env"
    projects_path = tmp_path / "projects.md"
    projects_path.write_text("")
    plain = ["https://x.atlassian.net", "me@x.com", "U01ABC23DEF", "n"]
    secret = ["tok-jira-12345678901234",
              "xoxb-slack-bot-12345",
              "xapp-slack-app-12345"]
    _scripted_input(monkeypatch, plain, secret)
    for k in REQUIRED_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    stream = io.StringIO()
    run(env_path, projects_path, stream=stream)
    out = stream.getvalue()
    assert "tok-jira-12345678901234" not in out
    assert "xoxb-slack-bot-12345" not in out
    # masking shows prefix only
    assert "tok-ji" in out and "1234" in out


def test_run_validates_url_and_retries(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    projects_path = tmp_path / "projects.md"
    projects_path.write_text("")
    plain = [
        "ftp://bad",                  # URL: rejected
        "https://x.atlassian.net",    # URL: ok
        "me@x.com",
        "U01ABC23DEF",
        "n",
    ]
    secret = ["t1", "t2", "t3"]
    _scripted_input(monkeypatch, plain, secret)
    for k in REQUIRED_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    stream = io.StringIO()
    run(env_path, projects_path, stream=stream)
    parsed = _read_env_file(env_path)
    assert parsed["JIRA_BASE_URL"] == "https://x.atlassian.net"
    assert "URL 은 http(s):// 로 시작" in stream.getvalue()


def test_run_validates_email_and_retries(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    projects_path = tmp_path / "projects.md"
    projects_path.write_text("")
    plain = [
        "https://x.atlassian.net",
        "noatsign",                   # email: rejected
        "me@x.com",                   # email: ok
        "U01ABC23DEF",
        "n",
    ]
    secret = ["t1", "t2", "t3"]
    _scripted_input(monkeypatch, plain, secret)
    for k in REQUIRED_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    stream = io.StringIO()
    run(env_path, projects_path, stream=stream)
    parsed = _read_env_file(env_path)
    assert parsed["JIRA_EMAIL"] == "me@x.com"


def test_run_strips_trailing_slash_on_url(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    projects_path = tmp_path / "projects.md"
    projects_path.write_text("")
    plain = ["https://x.atlassian.net/", "me@x.com", "U01ABC23DEF", "n"]
    secret = ["t1", "t2", "t3"]
    _scripted_input(monkeypatch, plain, secret)
    for k in REQUIRED_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    run(env_path, projects_path, stream=io.StringIO())
    parsed = _read_env_file(env_path)
    assert parsed["JIRA_BASE_URL"] == "https://x.atlassian.net"


def test_run_preserves_existing_values_on_partial_missing(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    projects_path = tmp_path / "projects.md"
    projects_path.write_text("")
    # Existing .env has 5 of 6 keys; only SLACK_APP_TOKEN missing
    existing = {k: "preserved" for k in REQUIRED_ENV_KEYS if k != "SLACK_APP_TOKEN"}
    _write_env_file(env_path, existing)
    # When missing_env_keys is recomputed inside run(), os.environ is consulted
    # too. Clear it to simulate fresh process.
    for k in REQUIRED_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    plain = ["n"]   # only repo prompt
    secret = ["xapp-fresh-12345"]   # only the missing app token
    _scripted_input(monkeypatch, plain, secret)
    run(env_path, projects_path, stream=io.StringIO())
    parsed = _read_env_file(env_path)
    assert parsed["SLACK_APP_TOKEN"] == "xapp-fresh-12345"
    assert parsed["JIRA_BASE_URL"] == "preserved"
    assert parsed["JIRA_EMAIL"] == "preserved"


def test_run_keeps_existing_value_on_blank_input_in_force_mode(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    projects_path = tmp_path / "projects.md"
    projects_path.write_text("")
    full = {k: "kept" for k in REQUIRED_ENV_KEYS}
    full["JIRA_BASE_URL"] = "https://kept.atlassian.net"
    full["JIRA_EMAIL"] = "kept@x.com"
    _write_env_file(env_path, full)
    for k in REQUIRED_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    # All blank inputs → keep existing
    plain = ["", "", "", "n"]
    secret = ["", "", ""]
    _scripted_input(monkeypatch, plain, secret)
    run(env_path, projects_path, force=True, stream=io.StringIO())
    parsed = _read_env_file(env_path)
    assert parsed["JIRA_BASE_URL"] == "https://kept.atlassian.net"
    assert parsed["JIRA_EMAIL"] == "kept@x.com"
    assert parsed["JIRA_API_TOKEN"] == "kept"


# ---- repo registration ----


def _seed_full_env(tmp_path, monkeypatch):
    """Fill .env so wizard skips env phase. Clear os.environ to be safe."""
    env_path = tmp_path / ".env"
    full = {k: "x" for k in REQUIRED_ENV_KEYS}
    full["JIRA_BASE_URL"] = "https://x.atlassian.net"
    full["JIRA_EMAIL"] = "me@x.com"
    _write_env_file(env_path, full)
    for k in REQUIRED_ENV_KEYS:
        monkeypatch.setenv(k, full[k])
    return env_path


def test_repo_registration_appends_valid_row(tmp_path, monkeypatch):
    env_path = _seed_full_env(tmp_path, monkeypatch)
    projects_path = tmp_path / "projects.md"
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    plain = [
        "y",                    # 첫 prompt: 등록 yes
        "myrepo",               # 이름
        str(repo_dir),          # 경로
        "main",                 # 기본 브랜치
        "",                     # 원격 (default origin)
        "",                     # 테스트 명령 (skip)
        "",                     # 타임아웃 → 600
        "n",                    # 추가 등록 안 함
    ]
    _scripted_input(monkeypatch, plain, secret=[])
    run(env_path, projects_path, stream=io.StringIO())
    # Round-trip via load_registry
    from bot_lib.registry import load_registry
    registered = load_registry(str(projects_path))
    assert "myrepo" in registered
    p = registered["myrepo"]
    assert p.path == str(repo_dir)
    assert p.default_branch == "main"
    assert p.remote == "origin"
    assert p.test_cmd is None
    assert p.test_timeout == 600


def test_repo_registration_rejects_nonexistent_path(tmp_path, monkeypatch):
    env_path = _seed_full_env(tmp_path, monkeypatch)
    projects_path = tmp_path / "projects.md"
    repo_dir = tmp_path / "real"
    repo_dir.mkdir()
    plain = [
        "y",
        "myrepo",
        "/no/such/dir",        # rejected
        str(repo_dir),         # accepted
        "main",
        "", "", "",
        "n",
    ]
    _scripted_input(monkeypatch, plain, secret=[])
    stream = io.StringIO()
    run(env_path, projects_path, stream=stream)
    assert "경로가 존재하지 않음" in stream.getvalue()
    from bot_lib.registry import load_registry
    assert "myrepo" in load_registry(str(projects_path))


def test_repo_registration_rejects_duplicate_name(tmp_path, monkeypatch):
    env_path = _seed_full_env(tmp_path, monkeypatch)
    projects_path = tmp_path / "projects.md"
    repo_dir = tmp_path / "dir"
    repo_dir.mkdir()
    # Pre-register one repo
    projects_path.write_text(
        "| 이름 | 경로 | 기본 브랜치 | 원격 | 테스트 명령 | 테스트 타임아웃(초) |\n"
        "|---|---|---|---|---|---|\n"
        f"| existing | {repo_dir} | main | origin | - | 600 |\n"
    )
    # force=True → .env 도 모두 다시 묻지만 빈 입력으로 기존 값 보존
    plain = [
        "", "", "",            # BASE_URL / EMAIL / USER_ID — keep
        "y",                    # 추가 등록
        "existing",             # 중복 → reject
        "newone",               # ok
        str(repo_dir),
        "main",
        "", "", "",
        "n",
    ]
    secret = ["", "", ""]      # 3 secrets — keep
    _scripted_input(monkeypatch, plain, secret)
    stream = io.StringIO()
    # force=True so the wizard enters repo prompt despite existing entries
    run(env_path, projects_path, force=True, stream=stream)
    assert "이미 등록된 이름" in stream.getvalue()
    from bot_lib.registry import load_registry
    reg = load_registry(str(projects_path))
    assert "existing" in reg
    assert "newone" in reg


def test_repo_registration_rejects_non_integer_timeout(tmp_path, monkeypatch):
    env_path = _seed_full_env(tmp_path, monkeypatch)
    projects_path = tmp_path / "projects.md"
    repo_dir = tmp_path / "dir"
    repo_dir.mkdir()
    plain = [
        "y",
        "myrepo",
        str(repo_dir),
        "main",
        "",
        "",
        "abc",                 # bad timeout → reject
        "300",                 # ok
        "n",
    ]
    _scripted_input(monkeypatch, plain, secret=[])
    stream = io.StringIO()
    run(env_path, projects_path, stream=stream)
    assert "정수만 허용" in stream.getvalue()


def test_repo_registration_handles_empty_projects_md(tmp_path, monkeypatch):
    env_path = _seed_full_env(tmp_path, monkeypatch)
    projects_path = tmp_path / "projects.md"  # does not exist
    repo_dir = tmp_path / "dir"
    repo_dir.mkdir()
    plain = [
        "y", "myrepo", str(repo_dir), "main", "", "", "", "n",
    ]
    _scripted_input(monkeypatch, plain, secret=[])
    run(env_path, projects_path, stream=io.StringIO())
    assert projects_path.exists()
    from bot_lib.registry import load_registry
    assert "myrepo" in load_registry(str(projects_path))


def test_repo_registration_skip_when_existing_and_not_force(tmp_path, monkeypatch):
    env_path = _seed_full_env(tmp_path, monkeypatch)
    projects_path = tmp_path / "projects.md"
    repo_dir = tmp_path / "dir"
    repo_dir.mkdir()
    projects_path.write_text(
        "| 이름 | 경로 | 기본 브랜치 | 원격 | 테스트 명령 | 테스트 타임아웃(초) |\n"
        "|---|---|---|---|---|---|\n"
        f"| existing | {repo_dir} | main | origin | - | 600 |\n"
    )
    # No prompts expected — wizard should print "추가 등록 생략" and return
    _scripted_input(monkeypatch, plain=[], secret=[])
    stream = io.StringIO()
    run(env_path, projects_path, stream=stream)
    assert "생략" in stream.getvalue()


# ---- validate_tokens ----


class _FakeResp:
    def __init__(self, status_code=200, payload=None, raise_on_json=False):
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self._payload = payload or {}
        self._raise_on_json = raise_on_json

    def json(self):
        if self._raise_on_json:
            raise ValueError("not json")
        return self._payload


def _patch_validate_http(monkeypatch, jira_resp=None, slack_resp=None,
                        jira_exc=None, slack_exc=None):
    """Stub the GET (Jira /myself) and POST (Slack auth.test) calls
    that `validate_tokens` makes."""
    def fake_get(url, auth=None, headers=None, timeout=None):
        if jira_exc is not None:
            raise jira_exc
        return jira_resp

    def fake_post(url, headers=None, timeout=None):
        if slack_exc is not None:
            raise slack_exc
        return slack_resp

    monkeypatch.setattr("bot_lib.setup_wizard.requests.get", fake_get)
    monkeypatch.setattr("bot_lib.setup_wizard.requests.post", fake_post)


_VALID_ENV = {
    "JIRA_BASE_URL": "https://x.atlassian.net",
    "JIRA_EMAIL": "me@x.com",
    "JIRA_API_TOKEN": "tok",
    "SLACK_BOT_TOKEN": "xoxb-bot",
    "SLACK_APP_TOKEN": "xapp-app",
    "SLACK_USER_ID": "U01ABC23DEF",
}


def test_validate_tokens_all_ok(monkeypatch):
    _patch_validate_http(
        monkeypatch,
        jira_resp=_FakeResp(200, {"accountId": "abc"}),
        slack_resp=_FakeResp(200, {"ok": True}),
    )
    assert setup_wizard.validate_tokens(_VALID_ENV) == []


def test_validate_tokens_jira_401_blames_token(monkeypatch):
    _patch_validate_http(
        monkeypatch,
        jira_resp=_FakeResp(401),
        slack_resp=_FakeResp(200, {"ok": True}),
    )
    failures = setup_wizard.validate_tokens(_VALID_ENV)
    keys = [k for k, _ in failures]
    assert "JIRA_API_TOKEN" in keys


def test_validate_tokens_jira_404_blames_base_url(monkeypatch):
    _patch_validate_http(
        monkeypatch,
        jira_resp=_FakeResp(404),
        slack_resp=_FakeResp(200, {"ok": True}),
    )
    failures = setup_wizard.validate_tokens(_VALID_ENV)
    assert ("JIRA_BASE_URL", "404 — base URL 확인") in failures


def test_validate_tokens_slack_bot_invalid(monkeypatch):
    _patch_validate_http(
        monkeypatch,
        jira_resp=_FakeResp(200, {"accountId": "abc"}),
        slack_resp=_FakeResp(200, {"ok": False, "error": "invalid_auth"}),
    )
    failures = setup_wizard.validate_tokens(_VALID_ENV)
    keys = [k for k, msg in failures]
    msgs = [msg for _, msg in failures]
    assert "SLACK_BOT_TOKEN" in keys
    assert any("invalid_auth" in m for m in msgs)


def test_validate_tokens_jira_network_error_blames_url(monkeypatch):
    import requests as _r
    _patch_validate_http(
        monkeypatch,
        jira_exc=_r.ConnectionError("boom"),
        slack_resp=_FakeResp(200, {"ok": True}),
    )
    failures = setup_wizard.validate_tokens(_VALID_ENV)
    keys = [k for k, _ in failures]
    assert "JIRA_BASE_URL" in keys


def test_validate_tokens_app_token_format(monkeypatch):
    _patch_validate_http(
        monkeypatch,
        jira_resp=_FakeResp(200, {"accountId": "abc"}),
        slack_resp=_FakeResp(200, {"ok": True}),
    )
    bad = {**_VALID_ENV, "SLACK_APP_TOKEN": "xoxb-not-app"}
    failures = setup_wizard.validate_tokens(bad)
    keys = [k for k, _ in failures]
    assert "SLACK_APP_TOKEN" in keys


def test_validate_tokens_user_id_format(monkeypatch):
    _patch_validate_http(
        monkeypatch,
        jira_resp=_FakeResp(200, {"accountId": "abc"}),
        slack_resp=_FakeResp(200, {"ok": True}),
    )
    bad = {**_VALID_ENV, "SLACK_USER_ID": "notauid"}
    failures = setup_wizard.validate_tokens(bad)
    keys = [k for k, _ in failures]
    assert "SLACK_USER_ID" in keys


def test_validate_tokens_skips_jira_probe_when_keys_missing(monkeypatch):
    """Empty/missing Jira keys → no probe, no failure for JIRA_*."""
    called = {"jira": False}

    def fake_get(*args, **kwargs):
        called["jira"] = True
        raise AssertionError("should not probe")

    monkeypatch.setattr("bot_lib.setup_wizard.requests.get", fake_get)
    monkeypatch.setattr(
        "bot_lib.setup_wizard.requests.post",
        lambda *a, **kw: _FakeResp(200, {"ok": True}),
    )
    partial = {**_VALID_ENV, "JIRA_API_TOKEN": ""}
    failures = setup_wizard.validate_tokens(partial)
    assert called["jira"] is False
    assert not any(k.startswith("JIRA_") for k, _ in failures)


# ---- run() with invalid_keys ----


def test_run_with_invalid_keys_reprompts_those_only(tmp_path, monkeypatch):
    env_path = _seed_full_env(tmp_path, monkeypatch)
    projects_path = tmp_path / "projects.md"
    # existing repo so wizard skips repo prompt
    repo_dir = tmp_path / "dir"
    repo_dir.mkdir()
    projects_path.write_text(
        "| 이름 | 경로 | 기본 브랜치 | 원격 | 테스트 명령 | 테스트 타임아웃(초) |\n"
        "|---|---|---|---|---|---|\n"
        f"| ex | {repo_dir} | main | origin | - | 600 |\n"
    )
    # Only JIRA_API_TOKEN should be re-prompted
    _scripted_input(monkeypatch, plain=[], secret=["new-jira-token-90909090"])
    stream = io.StringIO()
    run(env_path, projects_path,
        invalid_keys=["JIRA_API_TOKEN"], stream=stream)
    parsed = _read_env_file(env_path)
    assert parsed["JIRA_API_TOKEN"] == "new-jira-token-90909090"
    # Other values preserved
    assert parsed["JIRA_EMAIL"]
    assert parsed["SLACK_BOT_TOKEN"]
    assert "유효하지 않은 토큰" in stream.getvalue()


def test_run_with_invalid_keys_does_not_show_old_value_as_current(
    tmp_path, monkeypatch,
):
    env_path = _seed_full_env(tmp_path, monkeypatch)
    projects_path = tmp_path / "projects.md"
    repo_dir = tmp_path / "dir"
    repo_dir.mkdir()
    projects_path.write_text(
        "| 이름 | 경로 | 기본 브랜치 | 원격 | 테스트 명령 | 테스트 타임아웃(초) |\n"
        "|---|---|---|---|---|---|\n"
        f"| ex | {repo_dir} | main | origin | - | 600 |\n"
    )
    captured_prompts: list[str] = []

    def fake_getpass(prompt=""):
        captured_prompts.append(prompt)
        return "fresh-token-12345678901234"

    monkeypatch.setattr("builtins.input", lambda *_a, **_kw: "")
    monkeypatch.setattr("bot_lib.setup_wizard.getpass.getpass", fake_getpass)
    stream = io.StringIO()
    run(env_path, projects_path,
        invalid_keys=["JIRA_API_TOKEN"], stream=stream)
    # The "[현재: …]" suffix must NOT appear for the invalidated key
    assert all("[현재" not in p for p in captured_prompts)

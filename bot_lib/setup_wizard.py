"""Interactive setup wizard for `.env` and `projects.toml`.

Triggered by `bot.py` when required env keys are missing or no projects are
registered, or when the user passes `--setup` explicitly. Reads input from
stdin (plain) or `getpass.getpass` (secrets) and writes atomically.

Design notes:
  - Secrets never echo to stdout; masked preview is `xxxxxx…yyyy`.
  - `.env` write reuses the tempfile + `os.replace` pattern from
    `bot_lib/context.py:48-58` — no half-written files.
  - `projects.toml` section append goes through `registry.load_registry`
    for a final round-trip validation; bad section is rolled back.
"""
from __future__ import annotations

import getpass
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import requests
from requests.auth import HTTPBasicAuth

from bot_lib.registry import DEFAULT_REMOTE, RegistryError, load_registry

VALIDATE_HTTP_TIMEOUT = 10


REQUIRED_ENV_KEYS = (
    "JIRA_BASE_URL",
    "JIRA_EMAIL",
    "JIRA_API_TOKEN",
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "SLACK_USER_ID",
)

SECRET_KEYS = frozenset({"JIRA_API_TOKEN", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"})


@dataclass(frozen=True)
class _EnvField:
    key: str
    prompt: str
    is_secret: bool
    validate: Callable[[str], Optional[str]]   # returns error message or None
    transform: Callable[[str], str] = lambda s: s


def _nonempty(name: str) -> Callable[[str], Optional[str]]:
    def check(v: str) -> Optional[str]:
        return None if v.strip() else f"{name} 비어 있음"
    return check


def _is_https_url(v: str) -> Optional[str]:
    if not v.strip():
        return "URL 비어 있음"
    if not v.startswith(("http://", "https://")):
        return "URL 은 http(s):// 로 시작해야 합니다"
    return None


def _is_email(v: str) -> Optional[str]:
    if "@" not in v or not v.strip():
        return "이메일 형식이 아닙니다 (@ 누락)"
    return None


_FIELDS: tuple[_EnvField, ...] = (
    _EnvField("JIRA_BASE_URL", "Jira 도메인 (예: https://co.atlassian.net)",
              False, _is_https_url, lambda s: s.rstrip("/")),
    _EnvField("JIRA_EMAIL", "Jira 로그인 이메일",
              False, _is_email),
    _EnvField("JIRA_API_TOKEN", "Jira API token",
              True, _nonempty("JIRA_API_TOKEN")),
    _EnvField("SLACK_BOT_TOKEN", "Slack Bot token (xoxb-…)",
              True, _nonempty("SLACK_BOT_TOKEN")),
    _EnvField("SLACK_APP_TOKEN", "Slack App-Level token (xapp-…)",
              True, _nonempty("SLACK_APP_TOKEN")),
    _EnvField("SLACK_USER_ID", "Slack member ID (예: U01ABC23DEF)",
              False, _nonempty("SLACK_USER_ID")),
)


# ---------- public entry points ----------


def missing_env_keys(env: Optional[dict] = None) -> list[str]:
    """Return required keys not present (or empty) in `env` (default: os.environ)."""
    src = env if env is not None else os.environ
    return [k for k in REQUIRED_ENV_KEYS if not (src.get(k) or "").strip()]


def validate_tokens(env: Optional[dict] = None) -> list[tuple[str, str]]:
    """Probe each token via its provider. Returns [(env_key, error_msg), ...]
    for failures; empty list = all OK.

    The Jira and Slack-bot tokens are checked with cheap GET/POST calls
    (`/rest/api/3/myself`, `auth.test`). `SLACK_APP_TOKEN` cannot be
    validated without opening a Socket-Mode connection, so we only verify
    its `xapp-` prefix; the same goes for `SLACK_USER_ID` (`U…` prefix).
    """
    src = env if env is not None else os.environ
    failures: list[tuple[str, str]] = []

    base_url = (src.get("JIRA_BASE_URL") or "").strip().rstrip("/")
    email = (src.get("JIRA_EMAIL") or "").strip()
    api_token = (src.get("JIRA_API_TOKEN") or "").strip()
    if base_url and email and api_token:
        err = _probe_jira(base_url, email, api_token)
        if err:
            failures.append(err)

    bot_token = (src.get("SLACK_BOT_TOKEN") or "").strip()
    if bot_token:
        err = _probe_slack_bot(bot_token)
        if err:
            failures.append(err)

    app_token = (src.get("SLACK_APP_TOKEN") or "").strip()
    if app_token and not app_token.startswith("xapp-"):
        failures.append(("SLACK_APP_TOKEN", "`xapp-` 로 시작해야 합니다"))

    user_id = (src.get("SLACK_USER_ID") or "").strip()
    if user_id and not re.match(r"^U[A-Z0-9]{6,}$", user_id):
        failures.append(("SLACK_USER_ID", "`U` + 영숫자 형식이 아닙니다 (예: U01ABC23DEF)"))

    return failures


def _probe_jira(base_url: str, email: str, token: str) -> Optional[tuple[str, str]]:
    """Returns (key, msg) on failure, None on success.
    The blamed key is JIRA_API_TOKEN since 401 is overwhelmingly a bad token,
    not a bad URL/email."""
    try:
        r = requests.get(
            base_url + "/rest/api/3/myself",
            auth=HTTPBasicAuth(email, token),
            headers={"Accept": "application/json"},
            timeout=VALIDATE_HTTP_TIMEOUT,
        )
    except requests.RequestException as e:
        return ("JIRA_BASE_URL", f"네트워크 실패 ({type(e).__name__})")
    if r.status_code == 401:
        return ("JIRA_API_TOKEN", "401 Unauthorized — 토큰 만료/잘못된 이메일")
    if r.status_code == 404:
        return ("JIRA_BASE_URL", "404 — base URL 확인")
    if not r.ok:
        return ("JIRA_API_TOKEN", f"HTTP {r.status_code}")
    return None


def _probe_slack_bot(token: str) -> Optional[tuple[str, str]]:
    """Returns (key, msg) on failure, None on success."""
    try:
        r = requests.post(
            "https://slack.com/api/auth.test",
            headers={"Authorization": f"Bearer {token}"},
            timeout=VALIDATE_HTTP_TIMEOUT,
        )
    except requests.RequestException as e:
        return ("SLACK_BOT_TOKEN", f"네트워크 실패 ({type(e).__name__})")
    try:
        data = r.json()
    except ValueError:
        return ("SLACK_BOT_TOKEN", f"비정상 응답 (HTTP {r.status_code})")
    if not data.get("ok"):
        return ("SLACK_BOT_TOKEN", f"slack: {data.get('error') or 'unknown'}")
    return None


def run(
    env_path: Path,
    projects_path: Path,
    *,
    force: bool = False,
    invalid_keys: Optional[list[str]] = None,
    stream=sys.stdout,
) -> None:
    """Top-level wizard. Prompts for any missing env keys then optionally
    appends new repo sections to projects.toml. Idempotent: existing values
    are preserved unless `force=True` (and even then, an empty input keeps
    the current value).

    `invalid_keys` adds those keys to the prompt list and clears their
    "current" suggestion so the user can't accidentally keep a known-bad
    value by hitting Enter.
    """
    print("\n=== jira-bot 설정 wizard ===\n", file=stream)

    existing = _read_env_file(env_path)
    if invalid_keys:
        print(
            "❌ 유효하지 않은 토큰 — 새 값을 입력해 주세요: "
            + ", ".join(invalid_keys) + "\n",
            file=stream,
        )
        for k in invalid_keys:
            existing.pop(k, None)

    if force:
        needed = list(REQUIRED_ENV_KEYS)
    else:
        needed = missing_env_keys({**existing, **os.environ})
        if invalid_keys:
            for k in invalid_keys:
                if k not in needed:
                    needed.append(k)

    if needed:
        print(f"입력 필요 항목: {len(needed)}개\n", file=stream)
        new_values = _prompt_env_fields(needed, existing, stream=stream)
        # When re-prompting invalid keys, write the file by merging on top of
        # the *full* existing file (incl. the popped keys we cleared above)
        # so we don't lose unrelated entries on disk.
        full_existing = _read_env_file(env_path)
        merged = {**full_existing, **new_values}
        _write_env_file(env_path, merged)
        print(f"\n✅ {env_path} 저장 완료 (권한 600)\n", file=stream)
    else:
        print("✓ .env 필수 키 모두 채워져 있음\n", file=stream)

    _maybe_add_repos(projects_path, stream=stream, force=force)
    print("\n=== wizard 완료 ===\n", file=stream)


# ---------- env prompting ----------


def _prompt_env_fields(
    keys: list[str], existing: dict, *, stream,
) -> dict[str, str]:
    fields_by_key = {f.key: f for f in _FIELDS}
    out: dict[str, str] = {}
    for key in keys:
        field = fields_by_key.get(key)
        if field is None:
            # Unknown key — fall back to plain non-empty prompt
            field = _EnvField(key, key, False, _nonempty(key))
        current = existing.get(key, "")
        out[key] = _prompt_one(field, current, stream=stream)
    return out


def _prompt_one(field: _EnvField, current: str, *, stream) -> str:
    suffix = ""
    if current:
        suffix = f" [현재: {_mask(current) if field.is_secret else current}]"
    while True:
        prompt = f"{field.prompt}{suffix}: "
        raw = getpass.getpass(prompt) if field.is_secret else input(prompt)
        if not raw.strip() and current:
            return current   # keep existing
        err = field.validate(raw)
        if err:
            print(f"  ❌ {err}", file=stream)
            continue
        value = field.transform(raw.strip())
        if field.is_secret:
            print(f"  ✓ 받음 ({_mask(value)})", file=stream)
        else:
            print(f"  ✓ {value}", file=stream)
        return value


def _mask(secret: str) -> str:
    if len(secret) <= 10:
        return "*" * len(secret)
    return f"{secret[:6]}…{secret[-4:]}"


# ---------- env file I/O ----------


_ENV_LINE_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)=(.*)$")


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse an existing .env into a dict. Comments and blank lines ignored."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _ENV_LINE_RE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        # strip surrounding quotes (single or double)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        out[key] = value
    return out


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    """Write all values atomically. Existing comments are not preserved
    (we control the canonical layout). Permissions set to 600.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    written: set[str] = set()

    # Required keys first, in canonical order
    lines.append("# Jira")
    for key in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
        lines.append(_env_line(key, values.get(key, "")))
        written.add(key)

    lines.append("")
    lines.append("# Slack")
    for key in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_USER_ID"):
        lines.append(_env_line(key, values.get(key, "")))
        written.add(key)

    # Any extra keys (BOT_USER, LLM_*, JIRA_EXTRA_JQL, ...) carried over
    extras = {k: v for k, v in values.items() if k not in written}
    if extras:
        lines.append("")
        lines.append("# Other")
        for key, value in sorted(extras.items()):
            lines.append(_env_line(key, value))

    body = "\n".join(lines) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _env_line(key: str, value: str) -> str:
    """Render a single KEY="value" line. Always quoted for safety
    (consistent with AGENTS.md §4 which mandates quotes for spaces).
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{key}="{escaped}"'


# ---------- projects.toml ----------


_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class _RepoEntry:
    name: str
    path: str
    default_branch: str
    remote: Optional[str]   # None → omit from TOML (caller falls back to default)
    test_cmd: Optional[str]
    test_timeout: int


def _maybe_add_repos(projects_path: Path, *, stream, force: bool) -> None:
    """Loop: ask whether to register a repo, validate, append, re-validate.

    Triggered automatically when registry has 0 entries; with `force=True`
    the user is also prompted (they can skip with N).
    """
    try:
        existing = load_registry(str(projects_path)) if projects_path.exists() else {}
    except RegistryError as e:
        print(f"⚠️  projects.toml 파싱 실패: {e}", file=stream)
        existing = {}

    if existing and not force:
        print(f"✓ 등록된 repo {len(existing)}건 — 추가 등록 생략", file=stream)
        return

    if existing:
        print(f"등록된 repo: {', '.join(sorted(existing))}", file=stream)

    while True:
        if not existing:
            prompt, default = "repo 등록 [Y/n]: ", "y"
        else:
            prompt, default = "추가 등록 [y/N]: ", "n"
        ans = (input(prompt).strip().lower() or default)
        if ans in ("n", "no"):
            return

        entry = _prompt_repo_entry(set(existing), stream=stream)
        if entry is None:
            continue   # user gave up this entry
        snapshot = projects_path.read_text() if projects_path.exists() else None
        _append_repo_section(projects_path, entry)

        # Round-trip validation
        try:
            existing = load_registry(str(projects_path))
        except RegistryError as e:
            print(f"❌ projects.toml 검증 실패 — 마지막 섹션 롤백: {e}", file=stream)
            if snapshot is None:
                projects_path.unlink()
            else:
                projects_path.write_text(snapshot)
        else:
            print(f"✅ {entry.name} 등록 완료", file=stream)


def _prompt_repo_entry(taken_names: set[str], *, stream) -> Optional[_RepoEntry]:
    """Prompt every field. Returns `None` if user aborts via empty name."""
    while True:
        name = input("  이름 (빈 입력 → 취소): ").strip()
        if not name:
            return None
        if name in taken_names:
            print(f"  ❌ 이미 등록된 이름: {name}", file=stream)
            continue
        if not _NAME_RE.match(name):
            print("  ❌ 이름은 영숫자 + `_` `-` 만 허용", file=stream)
            continue
        break

    while True:
        path_raw = input("  경로 (절대경로): ").strip()
        if not path_raw:
            print("  ❌ 경로 필수", file=stream)
            continue
        if not Path(path_raw).expanduser().exists():
            print(f"  ❌ 경로가 존재하지 않음: {path_raw}", file=stream)
            continue
        path = str(Path(path_raw).expanduser())
        break

    branch = input("  기본 브랜치 (예: main): ").strip()
    if not branch:
        print("  ❌ 기본 브랜치 필수", file=stream)
        return None

    remote_raw = input(f"  원격 (빈 입력 → {DEFAULT_REMOTE}): ").strip()
    remote = remote_raw or None  # None → field omitted, registry default kicks in

    test_cmd_raw = input("  테스트 명령 (빈 입력 → 스킵): ").strip()
    test_cmd = test_cmd_raw or None

    while True:
        timeout_raw = input("  테스트 타임아웃(초) [600]: ").strip() or "600"
        try:
            timeout = int(timeout_raw)
        except ValueError:
            print("  ❌ 정수만 허용", file=stream)
            continue
        if timeout <= 0:
            print("  ❌ 양수만 허용", file=stream)
            continue
        break

    return _RepoEntry(
        name=name, path=path, default_branch=branch,
        remote=remote, test_cmd=test_cmd, test_timeout=timeout,
    )


def _append_repo_section(path: Path, entry: _RepoEntry) -> None:
    """Append a `[name]` TOML section to `projects.toml`. Atomic write."""
    section_lines = [f"[{entry.name}]",
                     f'path = {_toml_string(entry.path)}',
                     f'default_branch = {_toml_string(entry.default_branch)}']
    if entry.remote is not None:
        section_lines.append(f'remote = {_toml_string(entry.remote)}')
    if entry.test_cmd is not None:
        section_lines.append(f'test_cmd = {_toml_string(entry.test_cmd)}')
    section_lines.append(f"test_timeout = {entry.test_timeout}")
    block = "\n".join(section_lines) + "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_text()
        # Make sure we land on a fresh line and have a blank gap before the
        # new section so the file stays readable.
        prefix = existing
        if not prefix.endswith("\n"):
            prefix += "\n"
        if not prefix.endswith("\n\n"):
            prefix += "\n"
        body = prefix + block
    else:
        body = block

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body)
    os.replace(tmp, path)


def _toml_string(value: str) -> str:
    """Render a TOML basic string. Backslash-escape `\\` and `"`."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

DEFAULT_REMOTE = "origin"
DEFAULT_TIMEOUT = 600
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ALLOWED_FIELDS = frozenset({
    "path", "default_branch", "remote",
    "test_cmd", "test_timeout", "disabled",
})


class RegistryError(Exception):
    """Bot startup is rejected when projects.toml has problems."""


@dataclass(frozen=True)
class Project:
    name: str
    path: str
    default_branch: str
    remote: str
    test_cmd: Optional[str]
    test_timeout: int


def load_registry(file_path: str) -> dict[str, Project]:
    raw_bytes = Path(file_path).read_bytes()
    try:
        data = tomllib.loads(raw_bytes.decode("utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise RegistryError(f"TOML 파싱 실패: {e}") from None
    except UnicodeDecodeError as e:
        raise RegistryError(f"UTF-8 디코딩 실패: {e}") from None

    projects: dict[str, Project] = {}
    for name, section in data.items():
        if not isinstance(section, dict):
            raise RegistryError(
                f"`{name}`: TOML 섹션이 아님 (테이블 [name] 형식이어야 함)"
            )
        if not _NAME_RE.match(name):
            raise RegistryError(
                f"`{name}`: 이름은 영숫자 + `_` `-` 만 허용"
            )
        if section.get("disabled") is True:
            continue
        projects[name] = _parse_section(name, section)

    return projects


def _parse_section(name: str, section: dict[str, Any]) -> Project:
    unknown = set(section) - _ALLOWED_FIELDS
    if unknown:
        raise RegistryError(
            f"`{name}`: 알 수 없는 필드 {sorted(unknown)}"
        )

    path_str = section.get("path")
    if not isinstance(path_str, str) or not path_str:
        raise RegistryError(f"`{name}`: `path` 필수 (문자열)")
    if not Path(path_str).exists():
        raise RegistryError(f"`{name}`: 경로가 존재하지 않음: {path_str}")

    branch = section.get("default_branch")
    if not isinstance(branch, str) or not branch:
        raise RegistryError(f"`{name}`: `default_branch` 필수 (문자열)")

    remote_raw = section.get("remote", "")
    if not isinstance(remote_raw, str):
        raise RegistryError(f"`{name}`: `remote` 는 문자열이어야 함")
    remote = remote_raw or DEFAULT_REMOTE

    test_cmd_raw = section.get("test_cmd", "")
    if not isinstance(test_cmd_raw, str):
        raise RegistryError(f"`{name}`: `test_cmd` 는 문자열이어야 함")
    test_cmd: Optional[str] = test_cmd_raw or None

    timeout = section.get("test_timeout", DEFAULT_TIMEOUT)
    if not isinstance(timeout, int) or isinstance(timeout, bool):
        raise RegistryError(
            f"`{name}`: `test_timeout` 은 정수여야 함 (받음: {timeout!r})"
        )
    if timeout <= 0:
        raise RegistryError(f"`{name}`: `test_timeout` 양수만 허용")

    return Project(
        name=name,
        path=path_str,
        default_branch=branch,
        remote=remote,
        test_cmd=test_cmd,
        test_timeout=timeout,
    )

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

COLUMNS = 6
SEPARATOR_RE = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+\s*$")
DEFAULT_REMOTE = "origin"


class RegistryError(Exception):
    """Bot startup is rejected when projects.md has problems."""


@dataclass(frozen=True)
class Project:
    name: str
    path: str
    default_branch: str
    remote: str
    test_cmd: Optional[str]
    test_timeout: int


def load_registry(file_path: str) -> dict[str, Project]:
    text = Path(file_path).read_text()
    projects: dict[str, Project] = {}
    seen_header = False

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        if SEPARATOR_RE.match(line):
            continue

        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != COLUMNS:
            raise RegistryError(
                f"line {lineno}: 컬럼 개수 {len(cells)} (예상 {COLUMNS})"
            )

        if not seen_header:
            seen_header = True
            continue

        name, path_str, branch, remote_raw, test_cmd_raw, timeout_raw = cells
        if name.startswith("#"):
            continue

        if not Path(path_str).exists():
            raise RegistryError(f"line {lineno}: 경로가 존재하지 않음: {path_str}")

        try:
            timeout = int(timeout_raw)
        except ValueError:
            raise RegistryError(
                f"line {lineno}: 타임아웃이 정수가 아님: {timeout_raw!r}"
            ) from None

        if name in projects:
            raise RegistryError(f"line {lineno}: 중복된 이름: {name}")

        test_cmd = None if test_cmd_raw in ("", "-") else test_cmd_raw
        remote = DEFAULT_REMOTE if remote_raw in ("", "-") else remote_raw

        projects[name] = Project(
            name=name,
            path=path_str,
            default_branch=branch,
            remote=remote,
            test_cmd=test_cmd,
            test_timeout=timeout,
        )

    return projects

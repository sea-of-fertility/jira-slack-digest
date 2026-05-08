"""Slack DM command parser for the bot's job command.

Form: `run <type> <issue> [-d <description>]`

  - `<type>`         one of fix | feat | refactor | chore | docs | test | perf
  - `<issue>`        Jira key, e.g. CDS-99 (^[A-Z]+-\\d+$)
  - `-d <text>`      optional. Greedy: everything after `-d ` is consumed as
                     a single string. Must come last among any flags.
                     Omit for "trust Jira" mode — claude reads only Jira
                     description + comments.

Repo is NOT taken at command time. It comes from the session context
(`init <repo>` / `init <repo> -r <remote>` / etc.) — write commands
remain on the existing path.
"""
import re
from dataclasses import dataclass
from typing import Optional

ALLOWED_TYPES = frozenset({"fix", "feat", "refactor", "chore", "docs", "test", "perf"})
ISSUE_RE = re.compile(r"^[A-Z]+-\d+$")
_DASH_D_RE = re.compile(r"\s-d(?:\s+|$)")  # `-d` boundary: leading whitespace, then space or EOL

USAGE = "형식: run <type> <issue> [-d <지시문>]"

# `jira create` — issue creation via API
CREATE_USAGE = "형식: jira create -k <에픽|작업|버그|스토리> -t <제목> [-d <본문>]"
KIND_KO_TO_EN = {
    "에픽": "Epic",
    "작업": "Task",
    "버그": "Bug",
    "스토리": "Story",
}
KIND_EN_CANONICAL = {"epic": "Epic", "task": "Task", "bug": "Bug", "story": "Story"}

# `jira get` — list assigned issues by status
GET_USAGE = (
    "형식: jira get [-s <todo|inprogress|review|resolved|done|all>] "
    "[-p <키|all>]"
)
# Maps user-facing alias → exact Jira status name (None = no status filter).
# NOTE: `review → "In Review"` is an assumption — adjust if your Jira workflow
# uses "Code Review" / "Review" / etc.
STATUS_ALIASES: dict[str, Optional[str]] = {
    "todo": "To Do",
    "inprogress": "In Progress",
    "review": "In Review",
    "resolved": "Resolved",
    "done": "Done",
    "all": None,
}


class CommandError(ValueError):
    """User-facing parse failure. The message is shown back in Slack."""


@dataclass(frozen=True)
class ParsedCmd:
    type: str
    repo: Optional[str]          # always None — repo is supplied by context
    issue: str
    instruction: Optional[str]   # None when -d not provided (Jira-trust mode)


def is_help(text: str) -> bool:
    stripped = text.strip()
    return stripped.lower() == "help" or stripped == "도움말"


def parse(text: str) -> ParsedCmd:
    """Parse `run <type> <issue> [-d <greedy text>]`. Raises CommandError."""
    stripped = text.strip()
    if not (stripped == "run" or stripped.startswith("run ")):
        raise CommandError(USAGE)
    rest = stripped[len("run"):].strip()
    if not rest:
        raise CommandError(USAGE)

    # Greedy split on `-d` boundary. Whatever follows the first `-d ` token
    # becomes instruction verbatim; whatever precedes is positional.
    m = _DASH_D_RE.search(rest)
    if m:
        head = rest[:m.start()].strip()
        tail = rest[m.end():].strip()
        instruction: Optional[str] = tail if tail else None
    else:
        head = rest
        instruction = None

    tokens = head.split()
    if len(tokens) == 0:
        raise CommandError(f"type / issue 토큰이 비어 있습니다. {USAGE}")
    if len(tokens) == 1:
        raise CommandError(f"issue 토큰이 빠졌습니다. {USAGE}")
    if len(tokens) > 2:
        extras = " ".join(tokens[2:])
        raise CommandError(f"인식 못한 토큰: `{extras}`. {USAGE}")

    type_, issue = tokens[0], tokens[1]
    _validate_type(type_)
    if not ISSUE_RE.match(issue):
        raise CommandError("issue 형식: PROJ-123 같은 Jira 키")

    return ParsedCmd(type=type_, repo=None, issue=issue, instruction=instruction)


def _validate_type(type_: str) -> None:
    if type_ not in ALLOWED_TYPES:
        raise CommandError("지원 type: " + ", ".join(sorted(ALLOWED_TYPES)))


# ---- jira create ----


@dataclass(frozen=True)
class ParsedCreate:
    kind: str                    # canonical Jira issuetype: Epic / Task / Bug / Story
    title: str
    description: Optional[str]   # None when -d not provided


def is_create(text: str) -> bool:
    stripped = text.strip()
    return stripped == "jira create" or stripped.startswith("jira create ")


def parse_create(text: str) -> ParsedCreate:
    """Parse `jira create -k <kind> -t <title> [-d <body>]`.

    Both `-t` and `-d` are greedy:
      - `-d` consumes everything to EOL (must come last among flags).
      - `-t` consumes everything up to the next `-k` flag (or `-d`).
    `-k` takes a single token argument.
    """
    stripped = text.strip()
    if not is_create(stripped):
        raise CommandError(CREATE_USAGE)
    rest = stripped[len("jira create"):].strip()
    if not rest:
        raise CommandError(CREATE_USAGE)

    # 1) Greedy split on `-d` boundary.
    m = _DASH_D_RE.search(rest)
    if m:
        head = rest[:m.start()].strip()
        tail = rest[m.end():].strip()
        description: Optional[str] = tail if tail else None
    else:
        head = rest
        description = None

    # 2) Walk head tokens. -k takes 1 token; -t is greedy until next -k.
    tokens = head.split()
    kind: Optional[str] = None
    title_parts: list[str] = []
    capturing_title = False
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-k":
            capturing_title = False
            if i + 1 >= len(tokens):
                raise CommandError("`-k` 다음에 값이 필요합니다.")
            kind = tokens[i + 1]
            i += 2
        elif tok == "-t":
            capturing_title = True
            i += 1
        elif capturing_title:
            title_parts.append(tok)
            i += 1
        else:
            raise CommandError(f"인식 못한 토큰: `{tok}`. {CREATE_USAGE}")

    title = " ".join(title_parts).strip()

    if not kind:
        raise CommandError(f"`-k` 필수. {CREATE_USAGE}")
    if not title:
        raise CommandError(f"`-t` 필수. {CREATE_USAGE}")

    canonical = KIND_KO_TO_EN.get(kind) or KIND_EN_CANONICAL.get(kind.lower())
    if canonical is None:
        raise CommandError(
            "지원 분류: 에픽, 작업, 버그, 스토리 (또는 Epic/Task/Bug/Story)"
        )

    return ParsedCreate(kind=canonical, title=title, description=description)


# ---- jira get ----


@dataclass(frozen=True)
class ParsedGet:
    status: Optional[str]    # canonical Jira status name; None = no filter
    alias: str               # user-facing status alias for display ("todo", "all", ...)
    project: Optional[str]   # None = -p omitted (caller picks default);
                             # "all" = cross-project (no project filter);
                             # uppercase key (e.g. "CDS") = specific project


def is_get(text: str) -> bool:
    stripped = text.strip()
    return stripped == "jira get" or stripped.startswith("jira get ")


def parse_get(text: str) -> ParsedGet:
    """Parse `jira get [-s <alias>] [-p <key|all>]`."""
    stripped = text.strip()
    if not is_get(stripped):
        raise CommandError(GET_USAGE)
    rest = stripped[len("jira get"):].strip()

    if not rest:
        return ParsedGet(status=None, alias="all", project=None)

    tokens = rest.split()
    alias_raw: Optional[str] = None
    project_raw: Optional[str] = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-s":
            if i + 1 >= len(tokens):
                raise CommandError("`-s` 다음에 값이 필요합니다.")
            alias_raw = tokens[i + 1]
            i += 2
        elif tok == "-p":
            if i + 1 >= len(tokens):
                raise CommandError("`-p` 다음에 값이 필요합니다.")
            project_raw = tokens[i + 1]
            i += 2
        else:
            raise CommandError(f"인식 못한 토큰: `{tok}`. {GET_USAGE}")

    if alias_raw is None:
        status: Optional[str] = None
        alias = "all"
    else:
        alias = alias_raw.lower()
        if alias not in STATUS_ALIASES:
            valid = ", ".join(STATUS_ALIASES)
            raise CommandError(f"지원 status: {valid}")
        status = STATUS_ALIASES[alias]

    if project_raw is None:
        project: Optional[str] = None
    elif project_raw.lower() == "all":
        project = "all"
    else:
        # Jira keys are conventionally uppercase A-Z + digits/underscore;
        # uppercase the input so `-p cds` and `-p CDS` are equivalent.
        project = project_raw.upper()
        if not project or not project.replace("_", "").isalnum():
            raise CommandError(
                f"`-p` 값은 Jira 프로젝트 키 또는 `all`. 받음: `{project_raw}`"
            )

    return ParsedGet(status=status, alias=alias, project=project)

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

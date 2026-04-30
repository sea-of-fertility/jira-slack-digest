
import re
from dataclasses import dataclass
from typing import Optional

ALLOWED_TYPES = frozenset({"fix", "feat", "refactor", "chore", "docs", "test", "perf"})
ISSUE_RE = re.compile(r"^[A-Z]+-\d+$")


class CommandError(ValueError):
    """User-facing parse failure. The message is shown back in Slack."""


@dataclass(frozen=True)
class ParsedCmd:
    type: str
    repo: Optional[str]   # None means "use the session context's repo" (3-token form)
    issue: str
    instruction: str


def is_help(text: str) -> bool:
    stripped = text.strip()
    return stripped.lower() == "help" or stripped == "도움말"


def parse(text: str) -> ParsedCmd:
    """Accepts both 4-token (`<type>/<repo>/<issue>/<instruction>`) and
    3-token (`<type>/<issue>/<instruction>`) forms. The 3-token form is
    chosen when the second token matches the Jira issue regex — repo
    aliases (kebab-case) and issue keys (UPPERCASE-NUMBER) don't collide.
    """
    stripped = text.strip()

    # Try 3-token first by splitting at most 2 slashes
    head_split = stripped.split("/", maxsplit=2)
    if len(head_split) == 3 and ISSUE_RE.match(head_split[1].strip()):
        type_ = head_split[0].strip()
        issue = head_split[1].strip()
        instruction = head_split[2].strip()
        _validate_type(type_)
        for name, value in (("type", type_), ("issue", issue), ("instruction", instruction)):
            if not value:
                raise CommandError(f"{name} 토큰이 비어 있습니다")
        return ParsedCmd(type=type_, repo=None, issue=issue, instruction=instruction)

    # Otherwise expect 4-token form
    parts = stripped.split("/", maxsplit=3)
    if len(parts) != 4:
        raise CommandError("형식: <type>/<repo>/<issue>/<instruction> 또는 <type>/<issue>/<instruction>")

    type_, repo, issue = (p.strip() for p in parts[:3])
    instruction = parts[3].strip()

    for name, value in (("type", type_), ("repo", repo), ("issue", issue), ("instruction", instruction)):
        if not value:
            raise CommandError(f"{name} 토큰이 비어 있습니다")

    _validate_type(type_)

    if not ISSUE_RE.match(issue):
        raise CommandError("issue 형식: PROJ-123 같은 Jira 키")

    return ParsedCmd(type=type_, repo=repo, issue=issue, instruction=instruction)


def _validate_type(type_: str) -> None:
    if type_ not in ALLOWED_TYPES:
        raise CommandError("지원 type: " + ", ".join(sorted(ALLOWED_TYPES)))

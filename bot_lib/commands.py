import re
from dataclasses import dataclass

ALLOWED_TYPES = frozenset({"fix", "feat", "refactor", "chore", "docs", "test", "perf"})
ISSUE_RE = re.compile(r"^[A-Z]+-\d+$")


class CommandError(ValueError):
    """User-facing parse failure. The message is shown back in Slack."""


@dataclass(frozen=True)
class ParsedCmd:
    type: str
    repo: str
    issue: str
    instruction: str


def is_help(text: str) -> bool:
    stripped = text.strip()
    return stripped.lower() == "help" or stripped == "도움말"


def parse(text: str) -> ParsedCmd:
    parts = text.strip().split("/", maxsplit=3)
    if len(parts) != 4:
        raise CommandError("형식: <type>/<repo>/<issue>/<instruction>")

    type_, repo, issue = (p.strip() for p in parts[:3])
    instruction = parts[3].strip()

    for name, value in (("type", type_), ("repo", repo), ("issue", issue), ("instruction", instruction)):
        if not value:
            raise CommandError(f"{name} 토큰이 비어 있습니다")

    if type_ not in ALLOWED_TYPES:
        raise CommandError("지원 type: " + ", ".join(sorted(ALLOWED_TYPES)))

    if not ISSUE_RE.match(issue):
        raise CommandError("issue 형식: PROJ-123 같은 Jira 키")

    return ParsedCmd(type=type_, repo=repo, issue=issue, instruction=instruction)

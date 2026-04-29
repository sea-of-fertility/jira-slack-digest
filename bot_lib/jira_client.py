from dataclasses import dataclass
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

JIRA_ISSUE_PATH = "/rest/api/3/issue/"
RECENT_COMMENT_LIMIT = 5
HTTP_TIMEOUT = 30


def adf_to_text(node: Any) -> str:
    """Flatten Atlassian Document Format (ADF) JSON into plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(adf_to_text(n) for n in node if n).strip()
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return node.get("text", "")
    children = node.get("content", [])
    block_types = {"doc", "paragraph", "heading", "bulletList", "orderedList", "listItem"}
    joiner = "\n" if node.get("type") in block_types else ""
    return joiner.join(adf_to_text(c) for c in children)


@dataclass(frozen=True)
class JiraIssue:
    key: str
    title: str
    description: str
    comments_text: str


def fetch_issue(base_url: str, email: str, token: str, key: str) -> JiraIssue:
    """GET /rest/api/3/issue/{key} and parse into JiraIssue."""
    url = base_url.rstrip("/") + JIRA_ISSUE_PATH + key
    auth = HTTPBasicAuth(email, token)
    headers = {"Accept": "application/json"}
    r = requests.get(url, auth=auth, headers=headers, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return _parse_issue(r.json())


def _parse_issue(data: dict) -> JiraIssue:
    fields = data.get("fields") or {}
    title = fields.get("summary") or "(no title)"
    description = adf_to_text(fields.get("description"))

    raw_comments = (fields.get("comment") or {}).get("comments") or []
    parts = []
    for c in raw_comments[-RECENT_COMMENT_LIMIT:]:
        author = (c.get("author") or {}).get("displayName", "?")
        body = adf_to_text(c.get("body"))
        if body:
            parts.append(f"[{author}] {body}")
    comments_text = "\n---\n".join(parts)

    return JiraIssue(
        key=data.get("key", ""),
        title=title,
        description=description,
        comments_text=comments_text,
    )

from dataclasses import dataclass
from typing import Any, Optional

import requests
from requests.auth import HTTPBasicAuth

JIRA_ISSUE_PATH = "/rest/api/3/issue/"
JIRA_CREATE_PATH = "/rest/api/3/issue"
JIRA_MYSELF_PATH = "/rest/api/3/myself"
JIRA_SEARCH_PATH = "/rest/api/3/search/jql"
DEFAULT_PROJECT = "CDS"   # Single-tenant bot: hardcoded.
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


@dataclass(frozen=True)
class JiraCreatedIssue:
    key: str
    url: str


@dataclass(frozen=True)
class JiraIssueSummary:
    key: str
    title: str
    status: str


def fetch_issue(base_url: str, email: str, token: str, key: str) -> JiraIssue:
    """GET /rest/api/3/issue/{key} and parse into JiraIssue."""
    url = base_url.rstrip("/") + JIRA_ISSUE_PATH + key
    auth = HTTPBasicAuth(email, token)
    headers = {"Accept": "application/json"}
    r = requests.get(url, auth=auth, headers=headers, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return _parse_issue(r.json())


def search_my_issues(
    base_url: str,
    email: str,
    token: str,
    *,
    status: Optional[str] = None,
    project_key: str = DEFAULT_PROJECT,
    limit: int = 20,
) -> tuple[list[JiraIssueSummary], bool]:
    """JQL search restricted to the API token owner's assigned issues.

    `status` is the exact Jira status name (e.g. "In Progress"). None → no
    status filter. Returns up to `limit` summaries plus a `has_more` flag
    (we fetch limit+1 to detect overflow without a separate count call).
    """
    url = base_url.rstrip("/") + JIRA_SEARCH_PATH
    auth = HTTPBasicAuth(email, token)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    jql_parts = [f"project = {project_key}", "assignee = currentUser()"]
    if status:
        safe = status.replace('"', '\\"')
        jql_parts.append(f'status = "{safe}"')
    jql = " AND ".join(jql_parts) + " ORDER BY updated DESC"

    body = {
        "jql": jql,
        "fields": ["summary", "status"],
        "maxResults": limit + 1,
    }
    r = requests.post(url, json=body, auth=auth, headers=headers, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    raw = (r.json() or {}).get("issues") or []

    summaries = [
        JiraIssueSummary(
            key=item.get("key", ""),
            title=(item.get("fields") or {}).get("summary") or "(no title)",
            status=(((item.get("fields") or {}).get("status") or {}).get("name")) or "?",
        )
        for item in raw[:limit]
    ]
    has_more = len(raw) > limit
    return summaries, has_more


def get_my_account_id(base_url: str, email: str, token: str) -> str:
    """GET /rest/api/3/myself → accountId of the API token owner."""
    url = base_url.rstrip("/") + JIRA_MYSELF_PATH
    auth = HTTPBasicAuth(email, token)
    headers = {"Accept": "application/json"}
    r = requests.get(url, auth=auth, headers=headers, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()["accountId"]


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


def _adf_doc_from_text(text: str) -> dict:
    """Plain text → minimal ADF doc. Each line becomes a paragraph."""
    paragraphs: list[dict] = []
    for line in text.split("\n"):
        if line:
            paragraphs.append({
                "type": "paragraph",
                "content": [{"type": "text", "text": line}],
            })
        else:
            paragraphs.append({"type": "paragraph"})
    return {"type": "doc", "version": 1, "content": paragraphs}


def create_issue(
    base_url: str,
    email: str,
    token: str,
    issuetype: str,
    summary: str,
    description: str = "",
    project_key: str = DEFAULT_PROJECT,
    assignee_account_id: str | None = None,
) -> JiraCreatedIssue:
    """POST /rest/api/3/issue. Returns the created key + browse URL."""
    url = base_url.rstrip("/") + JIRA_CREATE_PATH
    auth = HTTPBasicAuth(email, token)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    fields: dict = {
        "project": {"key": project_key},
        "issuetype": {"name": issuetype},
        "summary": summary,
    }
    if description:
        fields["description"] = _adf_doc_from_text(description)
    if assignee_account_id:
        fields["assignee"] = {"accountId": assignee_account_id}
    r = requests.post(
        url, json={"fields": fields}, auth=auth, headers=headers, timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    key = data.get("key") or ""
    return JiraCreatedIssue(
        key=key,
        url=f"{base_url.rstrip('/')}/browse/{key}",
    )

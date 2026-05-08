#!/usr/bin/env python3
"""
Jira Daily Digest → Slack DM (with optional LLM summaries)

Fetches all open issues assigned to the current Jira user, summarizes each
one in 1 Korean sentence (using either a local CLI agent like Claude Code,
or the Anthropic API), and posts a formatted digest to your Slack DM.

Usage:
    python jira_daily_digest.py                       # fetch, summarize, send
    python jira_daily_digest.py --dry-run             # print Slack payload, do not send
    python jira_daily_digest.py --mock                # use built-in sample data
    python jira_daily_digest.py --backend cli         # use local CLI agent (default if installed)
    python jira_daily_digest.py --backend api         # use Anthropic API
    python jira_daily_digest.py --backend none        # skip summaries entirely

Env vars (see .env.example):
    JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN
    SLACK_BOT_TOKEN, SLACK_USER_ID
    JIRA_EXTRA_JQL              # optional, appended with AND

    # Summarization backend (default: auto-detect → cli > api > none)
    LLM_BACKEND                 # cli | api | none
    LLM_CLI                     # CLI command (default: "claude -p"); other examples:
                                #   "codex exec", "gemini -p", "opencode run"
    LLM_CLI_TIMEOUT             # seconds (default: 90)
    ANTHROPIC_API_KEY           # required when LLM_BACKEND=api
    LLM_MODEL                   # default: claude-haiku-4-5-20251001 (api backend only)
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Any

import requests
from requests.auth import HTTPBasicAuth


# ---------- Jira ----------

JIRA_SEARCH_PATH = "/rest/api/3/search/jql"
DEFAULT_JQL = "assignee = currentUser() AND resolution = Unresolved"
FIELDS = [
    "summary",
    "status",
    "priority",
    "duedate",
    "issuetype",
    "updated",
    "description",
    "comment",
]

PRIORITY_RANK = {"Highest": 0, "High": 1, "Medium": 2, "Low": 3, "Lowest": 4}


def fetch_jira_issues(base_url: str, email: str, token: str, jql: str) -> list[dict]:
    auth = HTTPBasicAuth(email, token)
    headers = {"Accept": "application/json"}
    url = base_url.rstrip("/") + JIRA_SEARCH_PATH
    issues: list[dict] = []
    next_page_token: str | None = None
    page_size = 50

    while True:
        params: dict[str, Any] = {
            "jql": jql,
            "fields": ",".join(FIELDS),
            "maxResults": page_size,
        }
        if next_page_token:
            params["nextPageToken"] = next_page_token

        r = requests.get(url, params=params, auth=auth, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        issues.extend(data.get("issues", []))

        if data.get("isLast", True):
            break
        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break
    return issues


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
    joiner = "\n" if node.get("type") in {"paragraph", "heading", "bulletList", "orderedList", "listItem"} else ""
    return joiner.join(adf_to_text(c) for c in children)


def normalize(issue: dict, base_url: str) -> dict:
    f = issue.get("fields", {})
    priority = (f.get("priority") or {}).get("name") or "Medium"
    status = (f.get("status") or {}).get("name") or "Unknown"
    issuetype = (f.get("issuetype") or {}).get("name") or "Task"
    due = f.get("duedate")

    description = adf_to_text(f.get("description"))[:3000]

    comments = []
    for c in (f.get("comment") or {}).get("comments", [])[-5:]:
        author = (c.get("author") or {}).get("displayName", "?")
        body = adf_to_text(c.get("body"))[:600]
        if body:
            comments.append(f"[{author}] {body}")
    comments_text = "\n---\n".join(comments)

    return {
        "key": issue.get("key"),
        "summary": f.get("summary") or "(no title)",
        "status": status,
        "priority": priority,
        "issuetype": issuetype,
        "duedate": due,
        "description": description,
        "comments_text": comments_text,
        "url": f"{base_url.rstrip('/')}/browse/{issue.get('key')}",
        "llm_summary": "",  # filled in later if LLM is enabled
    }


def sort_issues(issues: list[dict]) -> list[dict]:
    today = date.today()
    def key(i: dict):
        pr = PRIORITY_RANK.get(i["priority"], 2)
        if i["duedate"]:
            try:
                d = datetime.strptime(i["duedate"], "%Y-%m-%d").date()
                days = (d - today).days
            except ValueError:
                days = 9999
        else:
            days = 9999
        return (pr, days, i["key"] or "")
    return sorted(issues, key=key)


def classify(issue: dict) -> str:
    due = issue["duedate"]
    if not due:
        return "nodate"
    try:
        d = datetime.strptime(due, "%Y-%m-%d").date()
    except ValueError:
        return "nodate"
    today = date.today()
    return "overdue" if d < today else "today" if d == today else "upcoming"


# ---------- LLM summarization (CLI or API backend) ----------

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_CLI = "claude -p"

SUMMARY_INSTRUCTION = (
    "You summarize Jira issues for a busy engineer's morning Slack digest. "
    "Given an issue's title, description, and recent comments below, output a "
    "single Korean sentence (max ~80 Korean characters) describing what the "
    "issue is about and the current state of play. "
    "No preamble, no markdown, no quotes, no explanation — just the sentence. "
    "If there isn't enough context, output exactly: 본문 정보 부족."
)


def build_user_prompt(issue: dict) -> str:
    return (
        f"Title: {issue['summary']}\n"
        f"Status: {issue['status']} / Priority: {issue['priority']} / Type: {issue['issuetype']}\n"
        f"Due: {issue['duedate'] or '(none)'}\n\n"
        f"Description:\n{issue['description'] or '(empty)'}\n\n"
        f"Recent comments:\n{issue['comments_text'] or '(none)'}"
    )


def _clean_cli_output(raw: str) -> str:
    """CLI agents sometimes prefix/suffix with shell prompts or empty lines."""
    lines = [ln.rstrip() for ln in raw.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else ""


# --- API backend ---

def summarize_via_api(issue: dict, api_key: str, model: str) -> str:
    if not issue["description"] and not issue["comments_text"]:
        return ""
    payload = {
        "model": model,
        "max_tokens": 200,
        "system": SUMMARY_INSTRUCTION,
        "messages": [{"role": "user", "content": build_user_prompt(issue)}],
    }
    try:
        r = requests.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            data=json.dumps(payload),
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        parts = data.get("content", [])
        return "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()
    except Exception as e:
        return f"(요약 실패: {type(e).__name__})"


# --- CLI backend (cokacdir-style) ---

def summarize_via_cli(issue: dict, cli_cmd: list[str], timeout: int) -> str:
    """
    Call a local coding-agent CLI (claude / codex / gemini / opencode) in
    headless mode. The full prompt is fed via stdin; any extra args the user
    provided in LLM_CLI are passed through unchanged.

    No API key needed — uses the CLI's existing auth (e.g. Claude Code login).
    """
    if not issue["description"] and not issue["comments_text"]:
        return ""

    full_prompt = SUMMARY_INSTRUCTION + "\n\n---\n\n" + build_user_prompt(issue)
    try:
        proc = subprocess.run(
            cli_cmd,
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or "").strip().splitlines()[-1] if proc.stderr else f"exit {proc.returncode}"
            return f"(요약 실패: cli {err[:60]})"
        return _clean_cli_output(proc.stdout) or "(요약 실패: empty cli output)"
    except subprocess.TimeoutExpired:
        return f"(요약 실패: cli timeout {timeout}s)"
    except FileNotFoundError:
        return f"(요약 실패: '{cli_cmd[0]}' 명령을 찾을 수 없음)"
    except Exception as e:
        return f"(요약 실패: {type(e).__name__})"


# --- dispatch ---

def resolve_backend(explicit: str | None) -> str:
    """Decide which summarization backend to use."""
    if explicit:
        return explicit
    env = (os.environ.get("LLM_BACKEND") or "").strip().lower()
    if env in {"cli", "api", "none"}:
        return env
    # Auto-detect: prefer CLI if its first token is on PATH, then API.
    cli_cmd = shlex.split(os.environ.get("LLM_CLI") or DEFAULT_CLI)
    if cli_cmd and shutil.which(cli_cmd[0]):
        return "cli"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    return "none"


def summarize_all(issues: list[dict], backend: str, max_workers: int = 3) -> None:
    """Fill issue['llm_summary'] in place, concurrently."""
    if not issues or backend == "none":
        return

    if backend == "cli":
        cli_cmd = shlex.split(os.environ.get("LLM_CLI") or DEFAULT_CLI)
        timeout = int(os.environ.get("LLM_CLI_TIMEOUT") or 90)
        if not cli_cmd:
            print("[warn] LLM_CLI is empty; skipping summaries", file=sys.stderr)
            return
        if not shutil.which(cli_cmd[0]):
            print(f"[warn] '{cli_cmd[0]}' not on PATH; skipping summaries", file=sys.stderr)
            return
        worker = lambda i: summarize_via_cli(i, cli_cmd, timeout)
    elif backend == "api":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("[warn] ANTHROPIC_API_KEY not set; skipping summaries", file=sys.stderr)
            return
        model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
        worker = lambda i: summarize_via_api(i, api_key, model)
    else:
        return

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(worker, i): i for i in issues}
        for fut in as_completed(futs):
            issue = futs[fut]
            try:
                issue["llm_summary"] = fut.result()
            except Exception as e:
                issue["llm_summary"] = f"(요약 실패: {type(e).__name__})"


# ---------- Slack formatting ----------

PRIORITY_EMOJI = {
    "Highest": ":red_circle:", "High": ":large_orange_circle:",
    "Medium": ":large_yellow_circle:", "Low": ":large_blue_circle:",
    "Lowest": ":white_circle:",
}

BUCKET_TITLES = {
    "overdue": ":rotating_light: 지난 마감",
    "today": ":fire: 오늘 마감",
    "upcoming": ":calendar: 다가오는 마감",
    "nodate": ":page_facing_up: 마감일 없음",
}


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_issue_line(i: dict) -> str:
    emoji = PRIORITY_EMOJI.get(i["priority"], ":white_circle:")
    due = f" · 📅 {i['duedate']}" if i["duedate"] else ""
    header = (
        f"{emoji} <{i['url']}|{i['key']}> *{_escape(i['summary'])}*\n"
        f"    _{i['issuetype']} · {i['status']} · {i['priority']}{due}_"
    )
    if i.get("llm_summary"):
        header += f"\n    📝 {_escape(i['llm_summary'])}"
    return header


def build_slack_blocks(issues: list[dict]) -> list[dict]:
    today_str = date.today().strftime("%Y-%m-%d (%a)")

    if not issues:
        return [
            {"type": "header", "text": {"type": "plain_text", "text": f"📋 Jira 할당 이슈 · {today_str}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "🎉 열린 할당 이슈가 없습니다. 좋은 하루 되세요!"}},
        ]

    buckets: dict[str, list[dict]] = {"overdue": [], "today": [], "upcoming": [], "nodate": []}
    for i in issues:
        buckets[classify(i)].append(i)

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📋 Jira 할당 이슈 {len(issues)}건 · {today_str}"}},
        {
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": (
                    f"지난 마감 *{len(buckets['overdue'])}* · "
                    f"오늘 *{len(buckets['today'])}* · "
                    f"다가오는 *{len(buckets['upcoming'])}* · "
                    f"마감일 없음 *{len(buckets['nodate'])}*"
                ),
            }],
        },
        {"type": "divider"},
    ]

    for bucket in ("overdue", "today", "upcoming", "nodate"):
        items = buckets[bucket]
        if not items:
            continue
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{BUCKET_TITLES[bucket]} ({len(items)})*"},
        })
        chunk: list[str] = []
        size = 0
        for line in (format_issue_line(i) for i in items):
            if size + len(line) > 2800 and chunk:
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n\n".join(chunk)}})
                chunk, size = [], 0
            chunk.append(line)
            size += len(line) + 2
        if chunk:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n\n".join(chunk)}})

    return blocks


# ---------- Slack send ----------

def post_to_slack(token: str, user_id: str, text: str, blocks: list[dict]) -> dict:
    r = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
        data=json.dumps({"channel": user_id, "text": text, "blocks": blocks}),
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack API error: {data}")
    return data


# ---------- main ----------

def require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(f"[error] missing env var: {name}", file=sys.stderr)
        sys.exit(2)
    return v


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print payload, do not send to Slack.")
    parser.add_argument("--mock", action="store_true", help="Use built-in mock data (no Jira call).")
    parser.add_argument(
        "--backend",
        choices=["cli", "api", "none", "auto"],
        default="auto",
        help="Summarization backend. 'auto' picks cli > api > none based on what's available.",
    )
    parser.add_argument("--no-llm", action="store_true", help="Alias for --backend none.")
    args = parser.parse_args(argv)

    if args.mock:
        base_url = os.environ.get("JIRA_BASE_URL", "https://example.atlassian.net")
        raw: list[dict[str, Any]] = _mock_issues()
    else:
        email = require_env("JIRA_EMAIL")
        token = require_env("JIRA_API_TOKEN")
        base_url = require_env("JIRA_BASE_URL")
        extra = os.environ.get("JIRA_EXTRA_JQL", "").strip()
        jql = DEFAULT_JQL + (f" AND {extra}" if extra else "") + " ORDER BY priority DESC, duedate ASC"
        raw = fetch_jira_issues(base_url, email, token, jql)

    issues = sort_issues([normalize(i, base_url) for i in raw])

    if args.no_llm:
        backend = "none"
    else:
        backend = resolve_backend(None if args.backend == "auto" else args.backend)

    if backend == "cli":
        cmd = os.environ.get("LLM_CLI") or DEFAULT_CLI
        print(f"[info] summarizing {len(issues)} issue(s) via CLI: `{cmd}`", file=sys.stderr)
    elif backend == "api":
        model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
        print(f"[info] summarizing {len(issues)} issue(s) via Anthropic API ({model})", file=sys.stderr)
    else:
        print("[info] LLM summaries disabled", file=sys.stderr)

    summarize_all(issues, backend)

    blocks = build_slack_blocks(issues)
    text = f"📋 Jira 할당 이슈 {len(issues)}건"

    if args.dry_run:
        print(text)
        print(json.dumps({"blocks": blocks}, ensure_ascii=False, indent=2))
        return

    slack_token = require_env("SLACK_BOT_TOKEN")
    slack_user = require_env("SLACK_USER_ID")
    resp = post_to_slack(slack_token, slack_user, text, blocks)
    print(f"[ok] posted to Slack (ts={resp.get('ts')}, channel={resp.get('channel')})")


def _mock_issues() -> list[dict]:
    """Sample data with ADF description + comments for offline testing."""
    def adf(text: str) -> dict:
        return {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}

    return [
        {
            "key": "PROJ-101",
            "fields": {
                "summary": "Fix flaky login test",
                "status": {"name": "In Progress"},
                "priority": {"name": "High"},
                "issuetype": {"name": "Bug"},
                "duedate": "2026-04-20",
                "description": adf("CI에서 login_test.py가 랜덤하게 실패함. Chrome 120 이상에서 재현됨."),
                "comment": {"comments": [
                    {"author": {"displayName": "Alice"}, "body": adf("로그 보니까 auth callback 타임아웃인 듯. 재시도 로직 넣는 거 고려 중.")},
                    {"author": {"displayName": "Bob"}, "body": adf("PR #341 올렸습니다. 리뷰 부탁드려요.")},
                ]},
            },
        },
        {
            "key": "PROJ-102",
            "fields": {
                "summary": "오늘 배포 체크리스트 작성",
                "status": {"name": "To Do"},
                "priority": {"name": "Highest"},
                "issuetype": {"name": "Task"},
                "duedate": "2026-04-23",
                "description": adf("v2.3 릴리스용 배포 체크리스트. DB 마이그레이션 단계와 롤백 절차 포함 필요."),
                "comment": {"comments": []},
            },
        },
        {
            "key": "PROJ-110",
            "fields": {
                "summary": "리팩토링: user service",
                "status": {"name": "To Do"},
                "priority": {"name": "Medium"},
                "issuetype": {"name": "Task"},
                "duedate": None,
                "description": adf("UserService가 너무 비대함. auth / profile / preferences 3개로 쪼개는 안 검토."),
                "comment": {"comments": []},
            },
        },
        {
            "key": "PROJ-121",
            "fields": {
                "summary": "설계 문서 리뷰 (payments v2)",
                "status": {"name": "In Review"},
                "priority": {"name": "Medium"},
                "issuetype": {"name": "Story"},
                "duedate": "2026-04-28",
                "description": adf("payments v2 API 설계 문서. 이번 주 목요일까지 피드백 달라고 요청받음."),
                "comment": {"comments": [
                    {"author": {"displayName": "Carol"}, "body": adf("idempotency key 부분 한 번 더 봐주세요.")},
                ]},
            },
        },
    ]


if __name__ == "__main__":
    main()

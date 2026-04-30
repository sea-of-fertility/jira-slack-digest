#!/usr/bin/env python3
"""Slack DM bot — plan.md §3 entry point.

Loads env + projects.md, wires slack_bolt Socket Mode, delegates each DM
to bot_lib.slack_handler.handle_message. Run via `python bot.py` for the
local PoC; in production the launchd plist (Step 13) supervises it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from bot_lib.context import ContextStore
from bot_lib.registry import RegistryError, load_registry
from bot_lib.slack_handler import HandlerDeps, handle_message


CONTEXT_PATH = Path.home() / "Library" / "Application Support" / "jira-bot" / "context.json"


def _require(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.stderr.write(f"[error] missing env var: {name}\n")
        sys.exit(2)
    return v


def main() -> None:
    load_dotenv()

    registry_path = Path(__file__).resolve().parent / "projects.md"
    try:
        registry = load_registry(str(registry_path))
    except (FileNotFoundError, RegistryError) as e:
        sys.stderr.write(f"[error] {registry_path}: {e}\n")
        sys.exit(2)

    context = ContextStore(str(CONTEXT_PATH))

    deps = HandlerDeps(
        allowed_user_id=_require("SLACK_USER_ID"),
        registry=registry,
        jira_base_url=_require("JIRA_BASE_URL"),
        jira_email=_require("JIRA_EMAIL"),
        jira_token=_require("JIRA_API_TOKEN"),
        context=context,
    )

    app = App(token=_require("SLACK_BOT_TOKEN"))

    @app.message("")
    def on_message(message, say):
        handle_message(
            text=message.get("text") or "",
            user_id=message.get("user") or "",
            say=say,
            deps=deps,
        )

    ctx_now = context.get()
    ctx_str = f"{ctx_now.repo}/{ctx_now.remote}" if ctx_now else "(none)"
    sys.stderr.write(
        f"[info] bot online — {len(registry)} project(s) registered: "
        f"{', '.join(sorted(registry))} | context: {ctx_str}\n"
    )
    SocketModeHandler(app, _require("SLACK_APP_TOKEN")).start()


if __name__ == "__main__":
    main()

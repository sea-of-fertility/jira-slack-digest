#!/usr/bin/env python3
"""Slack DM bot — plan.md §3 entry point.

Loads env + projects.toml, wires slack_bolt Socket Mode, delegates each
DM to bot_lib.slack_handler.handle_message. Run via `jira-bot` for the
local PoC; in production the launchd plist (Step 13) supervises it.

Startup: when required env keys are missing, projects.toml has no
entries, or any token fails a live `/myself` / `auth.test` probe, the
interactive setup wizard launches automatically (TTY only) and the bot
then keeps running in the same process — no manual restart needed.
Non-TTY (launchd) exits with status 2 so the user can run
`jira-bot --setup`. Pass `--setup` to force the wizard and exit
(no bot startup).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from bot_lib import jira_client, setup_wizard
from bot_lib.cancellation import CancellationRegistry
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


def _load_registry_or_empty(path: Path) -> dict:
    try:
        return load_registry(str(path))
    except (FileNotFoundError, RegistryError):
        return {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--setup", action="store_true",
        help="설정 wizard 강제 실행 (.env / projects.toml 편집)",
    )
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    env_path = here / ".env"
    projects_path = here / "projects.toml"

    load_dotenv(env_path)
    missing = setup_wizard.missing_env_keys()
    registry = _load_registry_or_empty(projects_path)

    if args.setup or missing or not registry:
        if not sys.stdin.isatty():
            sys.stderr.write(
                "[error] 누락 설정이 있는데 비대화 환경입니다 "
                f"(env 누락: {missing or '없음'}, repo 등록: {len(registry)}건).\n"
                "터미널에서 `jira-bot --setup` 을 실행해 주세요.\n"
            )
            sys.exit(2)
        setup_wizard.run(env_path, projects_path, force=args.setup)
        if args.setup:
            sys.stderr.write(
                "[info] setup 완료 — 봇 재시작은 launchd 가 처리합니다 "
                "(`launchctl kickstart -k gui/$UID/local.jira-bot`).\n"
            )
            sys.exit(0)
        load_dotenv(env_path, override=True)
        try:
            registry = load_registry(str(projects_path))
        except (FileNotFoundError, RegistryError) as e:
            sys.stderr.write(f"[error] {projects_path}: {e}\n")
            sys.exit(2)

    # Live token check on every startup — catches expired/revoked tokens
    # that `missing_env_keys` cannot detect (key is present but invalid).
    # Loop so a TTY user who fat-fingers a token gets re-prompted instead of
    # the bot crashing on first API call.
    for _attempt in range(3):
        invalid = setup_wizard.validate_tokens()
        if not invalid:
            break
        sys.stderr.write("[error] 유효하지 않은 토큰:\n")
        for key, msg in invalid:
            sys.stderr.write(f"  - {key}: {msg}\n")
        if not sys.stdin.isatty():
            sys.stderr.write(
                "터미널에서 `jira-bot --setup` 을 실행해 주세요.\n"
            )
            sys.exit(2)
        setup_wizard.run(
            env_path, projects_path,
            invalid_keys=[k for k, _ in invalid],
        )
        load_dotenv(env_path, override=True)
    else:
        sys.stderr.write("[error] 토큰 검증 3회 실패 — 종료합니다.\n")
        sys.exit(2)

    context = ContextStore(str(CONTEXT_PATH))

    env_bot_user = os.environ.get("BOT_USER") or None  # optional
    cancel_registry = CancellationRegistry()

    jira_base_url = _require("JIRA_BASE_URL")
    jira_email = _require("JIRA_EMAIL")
    jira_token = _require("JIRA_API_TOKEN")

    try:
        bot_account_id = jira_client.get_my_account_id(
            jira_base_url, jira_email, jira_token,
        )
    except Exception as e:
        sys.stderr.write(
            f"[warn] /myself 조회 실패 — assignee 미설정으로 진행: "
            f"{type(e).__name__}: {e}\n"
        )
        bot_account_id = None

    deps = HandlerDeps(
        allowed_user_id=_require("SLACK_USER_ID"),
        registry=registry,
        jira_base_url=jira_base_url,
        jira_email=jira_email,
        jira_token=jira_token,
        context=context,
        env_bot_user=env_bot_user,
        cancel_registry=cancel_registry,
        bot_account_id=bot_account_id,
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
    who_str = (
        (ctx_now.who if ctx_now and ctx_now.who else env_bot_user) or "(unset)"
    )
    assignee_str = bot_account_id or "(unset)"
    sys.stderr.write(
        f"[info] bot online — {len(registry)} project(s) registered: "
        f"{', '.join(sorted(registry))} | context: {ctx_str} | who: {who_str} "
        f"| assignee: {assignee_str}\n"
    )
    SocketModeHandler(app, _require("SLACK_APP_TOKEN")).start()


if __name__ == "__main__":
    main()

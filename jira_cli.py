"""Unified `jira` CLI — single entry point that dispatches to subcommands.

Replaces the older `jira-bot` / `jira-digest` console scripts. Every
operation the user can invoke from the terminal is a verb:

    jira bot [--setup]       # Slack DM 봇 (= 옛 jira-bot)
    jira digest [--dry-run...] # Daily digest 1회 (= 옛 jira-digest)
    jira setup               # wizard 만 (= jira bot --setup)
    jira validate            # 토큰 라이브 검증 후 종료
    jira status              # launchd 상태 + .env 키 마스킹 요약
    jira install             # launchd plist 복사 + bootstrap
    jira uninstall           # launchd 등록 해제
    jira logs [-f]           # ~/Library/Logs/jira-bot.log

Calling `jira` with no subcommand defaults to `jira bot`, so the
"한 명령으로 시작" UX is preserved.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


def _here() -> Path:
    return Path(__file__).resolve().parent


# ---- subcommand handlers ----


def cmd_bot(args: argparse.Namespace) -> None:
    import bot
    bot.main(["--setup"] if args.setup else [])


def cmd_digest(args: argparse.Namespace) -> None:
    import jira_daily_digest
    digest_argv: list[str] = []
    if args.dry_run:
        digest_argv.append("--dry-run")
    if args.mock:
        digest_argv.append("--mock")
    if args.no_llm:
        digest_argv.append("--no-llm")
    if args.backend:
        digest_argv.extend(["--backend", args.backend])
    jira_daily_digest.main(digest_argv)


def cmd_setup(args: argparse.Namespace) -> None:
    import bot
    bot.main(["--setup"])


def cmd_validate(args: argparse.Namespace) -> None:
    from dotenv import load_dotenv

    from bot_lib import setup_wizard

    load_dotenv(_here() / ".env")
    invalid = setup_wizard.validate_tokens()
    if invalid:
        print("[error] 유효하지 않은 토큰:", file=sys.stderr)
        for key, msg in invalid:
            print(f"  - {key}: {msg}", file=sys.stderr)
        sys.exit(2)
    print("✓ 모든 토큰 유효 (Jira /myself, Slack auth.test, 형식 검사 통과)")


def cmd_status(args: argparse.Namespace) -> None:
    from dotenv import load_dotenv

    from bot_lib.setup_wizard import REQUIRED_ENV_KEYS, SECRET_KEYS, _mask

    print("=== launchd ===")
    label = f"gui/{os.getuid()}/local.jira-bot"
    r = subprocess.run(
        ["launchctl", "print", label],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print("(미설치 — `jira install` 으로 등록)")
    else:
        for line in r.stdout.splitlines()[:10]:
            print(line)

    print("\n=== .env (필수 키) ===")
    load_dotenv(_here() / ".env")
    for key in REQUIRED_ENV_KEYS:
        value = os.environ.get(key, "")
        if not value:
            mark = "✗ 비어 있음"
        elif key in SECRET_KEYS:
            mark = _mask(value)
        else:
            mark = value
        print(f"  {key}: {mark}")

    print("\n=== projects.toml ===")
    try:
        from bot_lib.registry import load_registry
        reg = load_registry(str(_here() / "projects.toml"))
    except Exception as e:
        print(f"  ❌ {type(e).__name__}: {e}")
    else:
        if not reg:
            print("  (등록된 repo 없음)")
        else:
            for name in sorted(reg):
                p = reg[name]
                print(f"  {name} → {p.path} ({p.default_branch}/{p.remote})")


def cmd_install(args: argparse.Namespace) -> None:
    src = _here() / "launchd" / "local.jira-bot.plist"
    if not src.exists():
        print(f"[error] plist 원본 없음: {src}", file=sys.stderr)
        sys.exit(2)
    dst_dir = Path.home() / "Library" / "LaunchAgents"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / "local.jira-bot.plist"
    shutil.copy(src, dst)
    print(f"✓ {dst}")
    label = f"gui/{os.getuid()}"
    r = subprocess.run(["launchctl", "bootstrap", label, str(dst)])
    if r.returncode == 0:
        print("✓ launchd bootstrap (RunAtLoad → 봇 즉시 시작)")
    else:
        print(
            "⚠️  bootstrap 실패 — 이미 등록돼 있거나 권한 문제일 수 있습니다.\n"
            "   기존 등록 해제: `jira uninstall`",
            file=sys.stderr,
        )


def cmd_uninstall(args: argparse.Namespace) -> None:
    label = f"gui/{os.getuid()}/local.jira-bot"
    subprocess.run(["launchctl", "bootout", label])
    dst = Path.home() / "Library" / "LaunchAgents" / "local.jira-bot.plist"
    if dst.exists():
        dst.unlink()
        print(f"✓ {dst} 제거")
    else:
        print("(설치된 plist 없음)")


def cmd_logs(args: argparse.Namespace) -> None:
    log = Path.home() / "Library" / "Logs" / ("jira-bot.err.log" if args.err else "jira-bot.log")
    if not log.exists():
        print(f"[info] {log} 아직 없음 (봇 미가동?)")
        return
    cmd = ["tail"]
    if args.follow:
        cmd.append("-f")
    cmd.extend(["-n", str(args.lines), str(log)])
    subprocess.run(cmd)


# ---- parser ----


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jira",
        description="Jira ↔ Slack ↔ Claude 봇 + 다이제스트 통합 CLI",
    )
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("bot", help="Slack DM 봇 시작 (기본 동작)")
    p.add_argument("--setup", action="store_true",
                   help="설정 wizard 강제 (= `jira setup`)")
    p.set_defaults(func=cmd_bot)

    p = sub.add_parser("digest", help="Daily digest 1회 실행 + Slack DM")
    p.add_argument("--dry-run", action="store_true",
                   help="페이로드 출력만, DM 발송 안 함")
    p.add_argument("--mock", action="store_true",
                   help="내장 mock 데이터 사용 (Jira 미호출)")
    p.add_argument("--no-llm", action="store_true",
                   help="요약 스킵")
    p.add_argument("--backend", choices=["cli", "api", "none", "auto"],
                   help="요약 백엔드 (기본 auto)")
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("setup", help="설정 wizard 단독 실행")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("validate",
                       help="토큰 라이브 검증 (Jira /myself, Slack auth.test)")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("status",
                       help="launchd 상태 + .env + projects.toml 요약")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("install", help="launchd plist 복사 + bootstrap")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("uninstall", help="launchd 등록 해제 + plist 제거")
    p.set_defaults(func=cmd_uninstall)

    p = sub.add_parser("logs", help="~/Library/Logs/jira-bot{,.err}.log tail")
    p.add_argument("-f", "--follow", action="store_true")
    p.add_argument("-n", "--lines", type=int, default=50)
    p.add_argument("--err", action="store_true",
                   help="stderr 로그 (jira-bot.err.log)")
    p.set_defaults(func=cmd_logs)

    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        # `jira` 단독 호출 → bot 시작 (기본 UX)
        cmd_bot(argparse.Namespace(setup=False))
        return
    args.func(args)


if __name__ == "__main__":
    main()

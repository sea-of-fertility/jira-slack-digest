# launchd setup (Step 13)

`local.jira-bot.plist` is a macOS LaunchAgent that supervises the
`jira-bot` console script (defined in `pyproject.toml`, calls `bot:main`)
per plan.md §11:

- starts at login (`RunAtLoad`)
- auto-restarts on any exit, throttled to 30s (`KeepAlive` + `ThrottleInterval`)
- stdout → `~/Library/Logs/jira-bot.log`
- stderr → `~/Library/Logs/jira-bot.err.log`
- runs at background priority (`ProcessType: Background`)
- explicit `PATH` so `git`, `gh`, and `claude` are reachable from launchd's
  empty default environment

## Install

```sh
cp launchd/local.jira-bot.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.jira-bot.plist
```

`bootstrap` both registers and starts the agent. After this, the bot is
online — verify in Slack with `help`, or:

```sh
tail -f ~/Library/Logs/jira-bot.log
```

## Common operations

```sh
# Status
launchctl print gui/$(id -u)/local.jira-bot | head -20

# Restart (after .env or projects.md changes)
launchctl kickstart -k gui/$(id -u)/local.jira-bot

# Stop temporarily
launchctl bootout gui/$(id -u)/local.jira-bot

# Permanently remove
launchctl bootout gui/$(id -u)/local.jira-bot
rm ~/Library/LaunchAgents/local.jira-bot.plist
```

## Notes

- The plist hardcodes absolute paths under `/Users/hyungjunpark/...`. This is
  a single-user bot (plan.md §2), so a template wasn't worth the indirection.
  If you ever fork it, edit the paths in the plist (the `jira-bot` entry, the
  `WorkingDirectory`, and the two log paths).
- `cron` digest (`jira_daily_digest.py`) keeps its own schedule and is
  unaffected by this LaunchAgent — see plan.md §8.
- If `gh auth refresh` invalidates the token cache, restart the agent so
  bot.py reloads it.

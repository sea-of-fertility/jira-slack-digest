"""Smoke tests for the unified `jira` CLI dispatcher.

The dispatcher is a thin pass-through; we verify each subcommand routes to
the right handler and translates flags correctly. Underlying behavior of
`bot.main`, `jira_daily_digest.main`, and `setup_wizard.*` is covered by
their own test modules.
"""
import argparse

import pytest

import jira_cli


def test_default_command_is_bot(monkeypatch):
    captured = []
    monkeypatch.setattr(jira_cli, "cmd_bot", lambda ns: captured.append(ns))
    jira_cli.main([])
    assert len(captured) == 1
    assert captured[0].setup is False


def test_bot_subcommand_passes_setup_flag(monkeypatch):
    captured = []
    monkeypatch.setattr(jira_cli, "cmd_bot", lambda ns: captured.append(ns))
    jira_cli.main(["bot", "--setup"])
    assert captured[0].setup is True


def test_bot_subcommand_default_no_setup(monkeypatch):
    captured = []
    monkeypatch.setattr(jira_cli, "cmd_bot", lambda ns: captured.append(ns))
    jira_cli.main(["bot"])
    assert captured[0].setup is False


def test_setup_subcommand_routes_via_cmd_setup(monkeypatch):
    called = []
    monkeypatch.setattr(jira_cli, "cmd_setup", lambda ns: called.append(ns))
    jira_cli.main(["setup"])
    assert called


def test_digest_passes_through_dry_run_and_mock(monkeypatch):
    captured_argv = []

    def fake_digest_main(argv=None):
        captured_argv.append(argv)

    monkeypatch.setattr("jira_daily_digest.main", fake_digest_main)
    jira_cli.main(["digest", "--mock", "--dry-run", "--no-llm"])
    argv = captured_argv[0]
    assert "--dry-run" in argv
    assert "--mock" in argv
    assert "--no-llm" in argv
    assert "--backend" not in argv   # not specified


def test_digest_passes_backend_flag(monkeypatch):
    captured_argv = []

    def fake_digest_main(argv=None):
        captured_argv.append(argv)

    monkeypatch.setattr("jira_daily_digest.main", fake_digest_main)
    jira_cli.main(["digest", "--backend", "cli"])
    assert captured_argv[0] == ["--backend", "cli"]


def test_bot_handler_invokes_bot_main_with_setup_flag(monkeypatch):
    captured_argv = []

    def fake_bot_main(argv=None):
        captured_argv.append(argv)

    monkeypatch.setattr("bot.main", fake_bot_main)
    jira_cli.cmd_bot(argparse.Namespace(setup=True))
    assert captured_argv == [["--setup"]]


def test_bot_handler_invokes_bot_main_without_setup(monkeypatch):
    captured_argv = []

    def fake_bot_main(argv=None):
        captured_argv.append(argv)

    monkeypatch.setattr("bot.main", fake_bot_main)
    jira_cli.cmd_bot(argparse.Namespace(setup=False))
    assert captured_argv == [[]]


def test_validate_exits_2_on_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        "bot_lib.setup_wizard.validate_tokens",
        lambda env=None: [("JIRA_API_TOKEN", "401")],
    )
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: None)
    with pytest.raises(SystemExit) as exc:
        jira_cli.cmd_validate(argparse.Namespace())
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "JIRA_API_TOKEN" in err and "401" in err


def test_validate_prints_ok_on_success(monkeypatch, capsys):
    monkeypatch.setattr(
        "bot_lib.setup_wizard.validate_tokens",
        lambda env=None: [],
    )
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: None)
    jira_cli.cmd_validate(argparse.Namespace())
    out = capsys.readouterr().out
    assert "유효" in out


def test_unknown_subcommand_exits(monkeypatch):
    with pytest.raises(SystemExit):
        jira_cli.main(["nonsense"])

import pytest

from bot_lib.commands import CommandError, is_help, parse


# ---- run form happy path ----


def test_parse_with_dash_d_instruction():
    cmd = parse("run fix CDS-99 -d null check 추가")
    assert cmd.type == "fix"
    assert cmd.repo is None
    assert cmd.issue == "CDS-99"
    assert cmd.instruction == "null check 추가"


def test_parse_without_dash_d_means_jira_trust():
    cmd = parse("run fix CDS-99")
    assert cmd.type == "fix"
    assert cmd.issue == "CDS-99"
    assert cmd.instruction is None


def test_parse_dash_d_greedy_consumes_remainder():
    cmd = parse("run fix CDS-99 -d controller 만 수정. service 는 두기.")
    assert cmd.instruction == "controller 만 수정. service 는 두기."


def test_parse_dash_d_preserves_slashes_and_hyphens():
    cmd = parse("run fix CDS-99 -d /api/v2 경로 라우팅 — IOException 재사용")
    assert cmd.instruction == "/api/v2 경로 라우팅 — IOException 재사용"


def test_parse_dash_d_multiline():
    cmd = parse("run fix CDS-99 -d line1\nline2\nline3")
    assert cmd.instruction == "line1\nline2\nline3"


def test_parse_strips_outer_whitespace():
    cmd = parse("   run  fix  CDS-99   -d   x   ")
    assert cmd.type == "fix"
    assert cmd.issue == "CDS-99"
    assert cmd.instruction == "x"


def test_parse_dash_d_empty_value_treated_as_none():
    """`-d ` followed by nothing → instruction is None (jira-trust mode)."""
    cmd = parse("run fix CDS-99 -d ")
    assert cmd.instruction is None


def test_parse_dash_d_with_dashed_words_in_value():
    """Once -d starts, subsequent flag-looking tokens stay in instruction."""
    cmd = parse("run fix CDS-99 -d use --no-cache flag")
    assert cmd.instruction == "use --no-cache flag"


# ---- error cases ----


def test_parse_rejects_no_run_prefix():
    with pytest.raises(CommandError, match="run <type>"):
        parse("fix/CDS-99/x")  # old slash form no longer accepted


def test_parse_rejects_run_only():
    with pytest.raises(CommandError, match="run <type>"):
        parse("run")


def test_parse_rejects_missing_issue():
    with pytest.raises(CommandError, match="issue"):
        parse("run fix")


def test_parse_rejects_bad_type():
    with pytest.raises(CommandError, match="지원 type"):
        parse("run hotfix CDS-99 -d x")


@pytest.mark.parametrize(
    "issue",
    ["CDS99", "cds-99", "CDS-", "-99", "CDS-9a", "123-CDS"],
)
def test_parse_rejects_bad_issue_format(issue):
    with pytest.raises(CommandError, match="issue 형식"):
        parse(f"run fix {issue} -d x")


def test_parse_rejects_extra_positional_tokens():
    """Anything beyond `run <type> <issue>` (before -d) is an error."""
    with pytest.raises(CommandError, match="인식 못한 토큰"):
        parse("run fix CDS-99 extra-token")


# ---- is_help ----


@pytest.mark.parametrize("text", ["help", "HELP", "Help", "  help  ", "도움말", "  도움말  "])
def test_is_help_true(text):
    assert is_help(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "help me",
        "도움말 좀",
        "run fix CDS-99 -d help",  # 'help' inside instruction must NOT trigger help mode
        "halp",
    ],
)
def test_is_help_false(text):
    assert is_help(text) is False

import pytest

from bot_lib.commands import CommandError, is_help, parse


def test_parse_returns_four_fields():
    cmd = parse("fix/ceph-api/CDS-99/null check 추가")
    assert cmd.type == "fix"
    assert cmd.repo == "ceph-api"
    assert cmd.issue == "CDS-99"
    assert cmd.instruction == "null check 추가"


# ---- 3-token form (uses session context, repo=None) ----


def test_parse_three_token_returns_repo_none():
    cmd = parse("fix/CDS-99/null check 추가")
    assert cmd.type == "fix"
    assert cmd.repo is None
    assert cmd.issue == "CDS-99"
    assert cmd.instruction == "null check 추가"


def test_parse_three_token_preserves_slashes_in_instruction():
    cmd = parse("fix/CDS-99/path/with/slashes")
    assert cmd.repo is None
    assert cmd.issue == "CDS-99"
    assert cmd.instruction == "path/with/slashes"


def test_parse_three_token_multiline():
    cmd = parse("docs/CDS-1/line1\nline2")
    assert cmd.repo is None
    assert "line1" in cmd.instruction and "line2" in cmd.instruction


def test_parse_three_token_with_whitespace_around_tokens():
    cmd = parse(" fix / CDS-99 / x ")
    assert cmd.repo is None
    assert cmd.issue == "CDS-99"
    assert cmd.instruction == "x"


def test_parse_three_token_unknown_type_rejected():
    with pytest.raises(CommandError, match="지원 type"):
        parse("hotfix/CDS-99/x")


def test_parse_three_token_empty_instruction_rejected():
    with pytest.raises(CommandError, match="instruction"):
        parse("fix/CDS-99/")


# ---- ambiguity: a "repo" that happens to look like an issue regex ----
# Repo names are kebab-case lowercase by convention; issue regex requires
# UPPERCASE letters + dash + digits, so they cannot collide. This test
# documents the behavior on the boundary case.


def test_uppercase_in_token2_picks_three_token():
    """When token-2 matches ^[A-Z]+-\\d+$ the parser treats it as the issue
    key (3-token form). Repo aliases that violate the issue regex stay in
    4-token form."""
    cmd = parse("fix/MYREPO/CDS-99/x")  # MYREPO doesn't match issue regex (no -\d+)
    assert cmd.repo == "MYREPO"
    assert cmd.issue == "CDS-99"


def test_instruction_preserves_slashes():
    cmd = parse("fix/jira-digest/CDS-99/path/with/slashes")
    assert cmd.instruction == "path/with/slashes"


def test_instruction_allows_multiline():
    text = "fix/ceph-api/CDS-2099/새 예외 클래스 생성.\n- IoException 상속\n- /error 라우팅"
    cmd = parse(text)
    assert "라우팅" in cmd.instruction
    assert "\n" in cmd.instruction


def test_strips_outer_whitespace():
    cmd = parse("  fix/ceph-api/CDS-99/x  ")
    assert cmd.type == "fix"
    assert cmd.instruction == "x"


def test_strips_each_token():
    cmd = parse("fix / ceph-api / CDS-99 / 지시")
    assert cmd.repo == "ceph-api"
    assert cmd.issue == "CDS-99"


@pytest.mark.parametrize(
    "text",
    [
        "fix/ceph-api/CDS-99",         # 3 tokens
        "fix/ceph-api",                # 2 tokens
        "fix",                         # 1 token
        "",                            # empty
    ],
)
def test_rejects_non_four_tokens(text):
    with pytest.raises(CommandError, match="형식"):
        parse(text)


@pytest.mark.parametrize(
    "text",
    [
        "/ceph-api/CDS-99/x",           # empty type
        "fix//CDS-99/x",                # empty repo
        "fix/ceph-api//x",              # empty issue
        "fix/ceph-api/CDS-99/",         # empty instruction
        "fix/ceph-api/CDS-99/   ",      # whitespace-only instruction
    ],
)
def test_rejects_empty_token(text):
    with pytest.raises(CommandError, match="비어"):
        parse(text)


def test_rejects_unknown_type():
    with pytest.raises(CommandError, match="지원 type"):
        parse("hotfix/ceph-api/CDS-99/x")


@pytest.mark.parametrize(
    "issue",
    ["CDS99", "cds-99", "CDS-", "-99", "CDS-9a", "123-CDS"],
)
def test_rejects_bad_issue_format(issue):
    with pytest.raises(CommandError, match="issue 형식"):
        parse(f"fix/ceph-api/{issue}/x")


@pytest.mark.parametrize("text", ["help", "HELP", "Help", "  help  ", "도움말", "  도움말  "])
def test_is_help_true(text):
    assert is_help(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "help me",
        "도움말 좀",
        "fix/ceph-api/CDS-99/help",
        "도움말/x/y/z",
        "halp",
    ],
)
def test_is_help_false(text):
    assert is_help(text) is False

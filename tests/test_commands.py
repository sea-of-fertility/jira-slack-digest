import pytest

from bot_lib.commands import CommandError, is_help, parse


def test_parse_returns_four_fields():
    cmd = parse("fix/ceph-api/CDS-99/null check 추가")
    assert cmd.type == "fix"
    assert cmd.repo == "ceph-api"
    assert cmd.issue == "CDS-99"
    assert cmd.instruction == "null check 추가"


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

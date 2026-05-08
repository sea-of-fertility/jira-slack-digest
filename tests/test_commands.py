import pytest

from bot_lib.commands import (
    CommandError,
    ParsedCreate,
    ParsedGet,
    is_create,
    is_get,
    is_help,
    parse,
    parse_create,
    parse_get,
)


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


# ---- jira create — parse_create ----


@pytest.mark.parametrize(
    "text",
    [
        "jira create",
        "jira create -k 작업 -t 제목",
        "  jira create  -k 작업 -t 제목 ",
    ],
)
def test_is_create_true(text):
    assert is_create(text) is True


@pytest.mark.parametrize(
    "text",
    ["", "create -k 작업 -t 제목", "jira", "jiracreate -k 작업", "run fix CDS-99"],
)
def test_is_create_false(text):
    assert is_create(text) is False


def test_parse_create_minimal():
    p = parse_create("jira create -k 작업 -t API 개선")
    assert isinstance(p, ParsedCreate)
    assert p.kind == "Task"
    assert p.title == "API 개선"
    assert p.description is None


@pytest.mark.parametrize(
    "ko, en",
    [("에픽", "Epic"), ("작업", "Task"), ("버그", "Bug"), ("스토리", "Story")],
)
def test_parse_create_korean_kind_maps_to_canonical(ko, en):
    assert parse_create(f"jira create -k {ko} -t 제목").kind == en


@pytest.mark.parametrize(
    "raw, en",
    [("Epic", "Epic"), ("epic", "Epic"), ("BUG", "Bug"), ("Story", "Story")],
)
def test_parse_create_english_kind_case_insensitive(raw, en):
    assert parse_create(f"jira create -k {raw} -t 제목").kind == en


def test_parse_create_with_description_greedy():
    p = parse_create(
        "jira create -k 버그 -t 로그인 실패 -d Chrome 120 이상에서 재현됨"
    )
    assert p.kind == "Bug"
    assert p.title == "로그인 실패"
    assert p.description == "Chrome 120 이상에서 재현됨"


def test_parse_create_title_greedy_until_next_flag():
    p = parse_create("jira create -t API v2 설계 문서 리뷰 -k 스토리")
    assert p.title == "API v2 설계 문서 리뷰"
    assert p.kind == "Story"


def test_parse_create_dash_d_preserves_special_chars():
    p = parse_create("jira create -k 작업 -t 제목 -d /api/v2 — IOException")
    assert p.description == "/api/v2 — IOException"


def test_parse_create_dash_d_multiline():
    p = parse_create("jira create -k 작업 -t 제목 -d line1\nline2\nline3")
    assert p.description == "line1\nline2\nline3"


def test_parse_create_empty_dash_d_treated_as_none():
    p = parse_create("jira create -k 작업 -t 제목 -d ")
    assert p.description is None


def test_parse_create_rejects_missing_kind():
    with pytest.raises(CommandError, match="`-k` 필수"):
        parse_create("jira create -t 제목만")


def test_parse_create_rejects_missing_title():
    with pytest.raises(CommandError, match="`-t` 필수"):
        parse_create("jira create -k 작업")


def test_parse_create_rejects_unsupported_kind():
    with pytest.raises(CommandError, match="지원 분류"):
        parse_create("jira create -k 핫픽스 -t 제목")


def test_parse_create_rejects_dangling_flag_value():
    with pytest.raises(CommandError, match="-k.*값"):
        parse_create("jira create -k")


def test_parse_create_rejects_unknown_token_before_flags():
    with pytest.raises(CommandError, match="인식 못한 토큰"):
        parse_create("jira create extra -k 작업 -t 제목")


def test_parse_create_rejects_dash_p_as_unknown():
    """-p was removed; bare `-p` outside title-capture is not recognized."""
    with pytest.raises(CommandError, match="인식 못한 토큰"):
        parse_create("jira create -p OTHER -k 작업 -t 제목")


# ---- jira get — parse_get / is_get ----


@pytest.mark.parametrize(
    "text",
    ["jira get", "jira get -s todo", "  jira get  -s done ", "jira get -s all"],
)
def test_is_get_true(text):
    assert is_get(text) is True


@pytest.mark.parametrize(
    "text",
    ["", "get", "jira", "jiraget", "jira create", "run fix CDS-1"],
)
def test_is_get_false(text):
    assert is_get(text) is False


def test_parse_get_no_args_defaults_to_all():
    p = parse_get("jira get")
    assert isinstance(p, ParsedGet)
    assert p.alias == "all"
    assert p.status is None


def test_parse_get_explicit_all():
    p = parse_get("jira get -s all")
    assert p.alias == "all"
    assert p.status is None


@pytest.mark.parametrize(
    "alias, jira_status",
    [
        ("todo", "To Do"),
        ("inprogress", "In Progress"),
        ("review", "In Review"),
        ("resolved", "Resolved"),
        ("done", "Done"),
    ],
)
def test_parse_get_status_aliases(alias, jira_status):
    p = parse_get(f"jira get -s {alias}")
    assert p.alias == alias
    assert p.status == jira_status


def test_parse_get_alias_case_insensitive():
    p = parse_get("jira get -s INPROGRESS")
    assert p.alias == "inprogress"
    assert p.status == "In Progress"


def test_parse_get_rejects_unknown_alias():
    with pytest.raises(CommandError, match="지원 status"):
        parse_get("jira get -s blocked")


def test_parse_get_rejects_dangling_dash_s():
    with pytest.raises(CommandError, match="-s.*값"):
        parse_get("jira get -s")


def test_parse_get_rejects_unknown_token():
    with pytest.raises(CommandError, match="인식 못한 토큰"):
        parse_get("jira get extra")


def test_parse_get_rejects_unknown_flag():
    with pytest.raises(CommandError, match="인식 못한 토큰"):
        parse_get("jira get -x todo")


def test_parse_get_no_p_flag_leaves_project_none():
    p = parse_get("jira get")
    assert p.project is None


def test_parse_get_p_all_canonicalizes_to_all():
    p = parse_get("jira get -p all")
    assert p.project == "all"


def test_parse_get_p_all_case_insensitive():
    p = parse_get("jira get -p ALL")
    assert p.project == "all"


def test_parse_get_p_specific_key_uppercased():
    p = parse_get("jira get -p okt")
    assert p.project == "OKT"


def test_parse_get_p_combines_with_status():
    p = parse_get("jira get -s todo -p OKT")
    assert p.alias == "todo"
    assert p.status == "To Do"
    assert p.project == "OKT"


def test_parse_get_p_order_independent():
    a = parse_get("jira get -p OKT -s todo")
    b = parse_get("jira get -s todo -p OKT")
    assert a == b


def test_parse_get_rejects_dangling_dash_p():
    with pytest.raises(CommandError, match="-p.*값"):
        parse_get("jira get -p")


def test_parse_get_rejects_invalid_project_key():
    with pytest.raises(CommandError, match="프로젝트 키"):
        parse_get("jira get -p ABC-123")

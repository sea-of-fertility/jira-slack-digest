from dataclasses import dataclass

import pytest

from bot_lib.jira_client import (
    DEFAULT_PROJECT,
    JiraCreatedIssue,
    JiraIssue,
    _adf_doc_from_text,
    adf_to_text,
    create_issue,
    fetch_issue,
    get_my_account_id,
)


@dataclass
class _FakeResponse:
    payload: dict
    status_code: int = 200

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


def _install_fake_get(monkeypatch, response: _FakeResponse, captured: dict):
    def fake_get(url, auth=None, headers=None, timeout=None, params=None):
        captured["url"] = url
        captured["auth"] = auth
        captured["headers"] = headers
        captured["timeout"] = timeout
        return response

    monkeypatch.setattr("bot_lib.jira_client.requests.get", fake_get)


def _doc(*paragraphs: str) -> dict:
    return {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": p}]}
            for p in paragraphs
        ],
    }


def test_adf_none_returns_empty():
    assert adf_to_text(None) == ""


def test_adf_string_returns_itself():
    assert adf_to_text("hello") == "hello"


def test_adf_text_node():
    assert adf_to_text({"type": "text", "text": "hi"}) == "hi"


def test_adf_single_paragraph():
    assert adf_to_text(_doc("first line")) == "first line"


def test_adf_multiple_paragraphs_joined_by_newline():
    out = adf_to_text(_doc("first", "second"))
    assert "first" in out
    assert "second" in out
    assert "\n" in out


def test_adf_bullet_list():
    node = {
        "type": "bulletList",
        "content": [
            {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "a"}]}]},
            {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "b"}]}]},
        ],
    }
    out = adf_to_text(node)
    assert "a" in out
    assert "b" in out


def test_adf_unknown_node_type_returns_text_content():
    node = {"type": "unknownType", "content": [{"type": "text", "text": "kept"}]}
    assert "kept" in adf_to_text(node)


# ---- fetch_issue ----


def _sample_issue(comments: int = 0) -> dict:
    cs = [
        {
            "author": {"displayName": f"user{i}"},
            "body": _doc(f"comment {i}"),
        }
        for i in range(comments)
    ]
    return {
        "key": "CDS-99",
        "fields": {
            "summary": "버그 수정",
            "description": _doc("재현 단계: 1. 로그인", "2. 클릭"),
            "comment": {"comments": cs},
        },
    }


def test_fetch_issue_constructs_correct_url(monkeypatch):
    captured = {}
    _install_fake_get(monkeypatch, _FakeResponse(_sample_issue()), captured)
    fetch_issue("https://example.atlassian.net", "e@x.com", "tok", "CDS-99")
    assert captured["url"] == "https://example.atlassian.net/rest/api/3/issue/CDS-99"


def test_fetch_issue_strips_trailing_slash_in_base_url(monkeypatch):
    captured = {}
    _install_fake_get(monkeypatch, _FakeResponse(_sample_issue()), captured)
    fetch_issue("https://example.atlassian.net/", "e@x.com", "tok", "CDS-99")
    assert captured["url"] == "https://example.atlassian.net/rest/api/3/issue/CDS-99"


def test_fetch_issue_uses_basic_auth(monkeypatch):
    captured = {}
    _install_fake_get(monkeypatch, _FakeResponse(_sample_issue()), captured)
    fetch_issue("https://x.com", "e@x.com", "tok", "X-1")
    auth = captured["auth"]
    assert auth.username == "e@x.com"
    assert auth.password == "tok"


def test_fetch_issue_returns_populated_dataclass(monkeypatch):
    captured = {}
    _install_fake_get(monkeypatch, _FakeResponse(_sample_issue(comments=2)), captured)
    issue = fetch_issue("https://x.com", "e@x.com", "tok", "CDS-99")
    assert isinstance(issue, JiraIssue)
    assert issue.key == "CDS-99"
    assert issue.title == "버그 수정"
    assert "재현 단계" in issue.description
    assert "comment 0" in issue.comments_text
    assert "comment 1" in issue.comments_text


def test_fetch_issue_keeps_only_last_5_comments(monkeypatch):
    captured = {}
    _install_fake_get(monkeypatch, _FakeResponse(_sample_issue(comments=8)), captured)
    issue = fetch_issue("https://x.com", "e@x.com", "tok", "CDS-99")
    # comments 0..2 should be dropped, only 3..7 kept
    assert "comment 0" not in issue.comments_text
    assert "comment 2" not in issue.comments_text
    assert "comment 3" in issue.comments_text
    assert "comment 7" in issue.comments_text


def test_fetch_issue_handles_empty_fields(monkeypatch):
    captured = {}
    payload = {"key": "X-1", "fields": {}}
    _install_fake_get(monkeypatch, _FakeResponse(payload), captured)
    issue = fetch_issue("https://x.com", "e@x.com", "tok", "X-1")
    assert issue.key == "X-1"
    assert issue.title  # falls back to a non-empty placeholder
    assert issue.description == ""
    assert issue.comments_text == ""


def test_fetch_issue_propagates_http_error(monkeypatch):
    captured = {}
    _install_fake_get(monkeypatch, _FakeResponse({}, status_code=404), captured)
    with pytest.raises(RuntimeError, match="HTTP 404"):
        fetch_issue("https://x.com", "e@x.com", "tok", "missing")


# ---- _adf_doc_from_text ----


def test_adf_doc_single_line():
    doc = _adf_doc_from_text("hello")
    assert doc["type"] == "doc"
    assert doc["content"] == [
        {"type": "paragraph", "content": [{"type": "text", "text": "hello"}]}
    ]


def test_adf_doc_multiline_keeps_paragraphs_in_order():
    doc = _adf_doc_from_text("line1\nline2")
    texts = [
        c["content"][0]["text"] for c in doc["content"] if c.get("content")
    ]
    assert texts == ["line1", "line2"]


def test_adf_doc_blank_line_yields_empty_paragraph():
    doc = _adf_doc_from_text("line1\n\nline2")
    # 3 paragraphs total; middle one has no content
    assert len(doc["content"]) == 3
    assert "content" not in doc["content"][1]


# ---- create_issue ----


def _install_fake_post(monkeypatch, response: _FakeResponse, captured: dict):
    def fake_post(url, json=None, auth=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["auth"] = auth
        captured["headers"] = headers
        captured["timeout"] = timeout
        return response

    monkeypatch.setattr("bot_lib.jira_client.requests.post", fake_post)


def test_create_issue_constructs_correct_url(monkeypatch):
    captured = {}
    _install_fake_post(
        monkeypatch, _FakeResponse({"key": "CDS-1234"}), captured,
    )
    create_issue(
        "https://example.atlassian.net/", "e@x.com", "tok",
        project_key="CDS", issuetype="Task", summary="t",
    )
    assert captured["url"] == "https://example.atlassian.net/rest/api/3/issue"


def test_create_issue_sends_required_fields(monkeypatch):
    captured = {}
    _install_fake_post(
        monkeypatch, _FakeResponse({"key": "CDS-1"}), captured,
    )
    create_issue(
        "https://x.com", "e@x.com", "tok",
        project_key="CDS", issuetype="Bug", summary="로그인 실패",
    )
    fields = captured["json"]["fields"]
    assert fields["project"] == {"key": "CDS"}
    assert fields["issuetype"] == {"name": "Bug"}
    assert fields["summary"] == "로그인 실패"
    # description omitted when empty
    assert "description" not in fields


def test_create_issue_sends_description_as_adf(monkeypatch):
    captured = {}
    _install_fake_post(
        monkeypatch, _FakeResponse({"key": "CDS-1"}), captured,
    )
    create_issue(
        "https://x.com", "e@x.com", "tok",
        project_key="CDS", issuetype="Task", summary="제목",
        description="첫 줄\n두 번째 줄",
    )
    desc = captured["json"]["fields"]["description"]
    assert desc["type"] == "doc"
    assert any(
        p.get("content", [{}])[0].get("text") == "첫 줄"
        for p in desc["content"] if p.get("content")
    )


def test_create_issue_uses_basic_auth(monkeypatch):
    captured = {}
    _install_fake_post(
        monkeypatch, _FakeResponse({"key": "X-1"}), captured,
    )
    create_issue(
        "https://x.com", "e@x.com", "tok",
        project_key="X", issuetype="Task", summary="s",
    )
    assert captured["auth"].username == "e@x.com"
    assert captured["auth"].password == "tok"


def test_create_issue_returns_key_and_browse_url(monkeypatch):
    captured = {}
    _install_fake_post(
        monkeypatch, _FakeResponse({"key": "CDS-321"}), captured,
    )
    result = create_issue(
        "https://example.atlassian.net", "e@x.com", "tok",
        project_key="CDS", issuetype="Task", summary="s",
    )
    assert isinstance(result, JiraCreatedIssue)
    assert result.key == "CDS-321"
    assert result.url == "https://example.atlassian.net/browse/CDS-321"


def test_create_issue_propagates_http_error(monkeypatch):
    captured = {}
    _install_fake_post(
        monkeypatch, _FakeResponse({}, status_code=400), captured,
    )
    with pytest.raises(RuntimeError, match="HTTP 400"):
        create_issue(
            "https://x.com", "e@x.com", "tok",
            project_key="X", issuetype="Task", summary="s",
        )


def test_create_issue_defaults_project_to_constant(monkeypatch):
    """When project_key is omitted, DEFAULT_PROJECT (CDS) is used."""
    captured = {}
    _install_fake_post(
        monkeypatch, _FakeResponse({"key": f"{DEFAULT_PROJECT}-7"}), captured,
    )
    result = create_issue(
        "https://x.com", "e@x.com", "tok",
        issuetype="Task", summary="s",
    )
    assert captured["json"]["fields"]["project"] == {"key": DEFAULT_PROJECT}
    assert result.key == f"{DEFAULT_PROJECT}-7"


def test_create_issue_omits_assignee_when_not_given(monkeypatch):
    captured = {}
    _install_fake_post(
        monkeypatch, _FakeResponse({"key": "CDS-1"}), captured,
    )
    create_issue(
        "https://x.com", "e@x.com", "tok",
        issuetype="Task", summary="s",
    )
    assert "assignee" not in captured["json"]["fields"]


def test_create_issue_sets_assignee_when_given(monkeypatch):
    captured = {}
    _install_fake_post(
        monkeypatch, _FakeResponse({"key": "CDS-1"}), captured,
    )
    create_issue(
        "https://x.com", "e@x.com", "tok",
        issuetype="Task", summary="s",
        assignee_account_id="acc-123",
    )
    assert captured["json"]["fields"]["assignee"] == {"accountId": "acc-123"}


# ---- get_my_account_id ----


def test_get_my_account_id_hits_myself_endpoint(monkeypatch):
    captured = {}
    _install_fake_get(
        monkeypatch, _FakeResponse({"accountId": "acc-xyz"}), captured,
    )
    result = get_my_account_id("https://x.atlassian.net/", "e@x.com", "tok")
    assert captured["url"] == "https://x.atlassian.net/rest/api/3/myself"
    assert captured["auth"].username == "e@x.com"
    assert captured["auth"].password == "tok"
    assert result == "acc-xyz"


def test_get_my_account_id_propagates_http_error(monkeypatch):
    captured = {}
    _install_fake_get(
        monkeypatch, _FakeResponse({}, status_code=401), captured,
    )
    with pytest.raises(RuntimeError, match="HTTP 401"):
        get_my_account_id("https://x.com", "e@x.com", "tok")

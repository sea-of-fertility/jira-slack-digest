from dataclasses import dataclass

import pytest

from bot_lib.jira_client import JiraIssue, adf_to_text, fetch_issue


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

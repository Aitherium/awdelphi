"""Gateway client transport behavior.

A live proof run (2026-08-26) caught a real defect: the gateway answers
`notifications/initialized` with HTTP 202 and an EMPTY body, which is the
streamable-HTTP spec — and the client parsed the empty body as JSON, raising
'misleading gateway returned non-JSON' on every connect while the gateway was
serving fine. These tests pin the spec behavior: 202/empty notifications are
None, an empty body on a call that EXPECTS a result is a loud error.
"""

from __future__ import annotations

import json

import pytest
from awdelphi.gateway import GatewayClient, GatewayError


class _FakeResp:
    def __init__(self, status_code=200, headers=None, content=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content
        self.text = content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)


def _client() -> GatewayClient:
    c = GatewayClient(base_url="http://test.invalid/mcp")
    c._session_id = "sess-1"
    return c


def _fake_post(fake: _FakeResp):
    def _post(*args, **kwargs):
        return fake

    return _post


def test_notification_202_is_not_an_error(monkeypatch):
    monkeypatch.setattr(
        "awdelphi.gateway.httpx.post",
        _fake_post(_FakeResp(status_code=202)),
    )
    out = _client()._post(
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        expect_result=False,
    )
    assert out is None


def test_empty_200_notification_is_not_an_error(monkeypatch):
    monkeypatch.setattr(
        "awdelphi.gateway.httpx.post",
        _fake_post(_FakeResp(status_code=200, content=b"")),
    )
    out = _client()._post(
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        expect_result=False,
    )
    assert out is None


def test_empty_body_on_expect_result_raises_loudly(monkeypatch):
    monkeypatch.setattr(
        "awdelphi.gateway.httpx.post",
        _fake_post(_FakeResp(status_code=200, content=b"")),
    )
    with pytest.raises(GatewayError, match="EMPTY body"):
        _client()._post(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {}},
        )


def test_json_result_is_parsed(monkeypatch):
    body = json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"ok": True}}).encode()
    monkeypatch.setattr(
        "awdelphi.gateway.httpx.post",
        _fake_post(_FakeResp(status_code=200, content=body)),
    )
    out = _client()._post(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {}},
    )
    assert out == {"ok": True}

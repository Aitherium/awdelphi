"""Minimal Streamable-HTTP MCP client for the AitherOS gateway.

Speaks the verified wire protocol of the local gateway (measured against
`dev/tools/mcp_stdio_bridge.py` and the live gateway, 2026-08-25):

    POST {base_url}  initialize            -> Mcp-Session-Id header
    POST {base_url}  notifications/initialized
    POST {base_url}  tools/call            -> JSON or text/event-stream

Headers: `Authorization: Bearer <~/.aither/session-bearer>`,
`MCP-Protocol-Version: 2025-06-18`, `Mcp-Session-Id` on calls after init.

`127.0.0.1`, never `localhost`: measured 2026-07-31, ::1:8182 refused after
2120 ms vs 3 ms on IPv4.

Fail-loudly doctrine (same as awrelay): a gateway that cannot be reached
raises GatewayError with a message that names the fix. There is no offline
mode — a panel that silently runs zero rounds is worse than one that says
"no rounds were run".
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

DEFAULT_GATEWAY_URL = os.environ.get("AWDELPHI_GATEWAY", "http://127.0.0.1:8182/mcp")
DEFAULT_BEARER_FILE = Path.home() / ".aither" / "session-bearer"
PROTOCOL_VERSION = "2025-06-18"

# The `forge_subagent` tool lives on the gateway (mcp_agent_delegate.py).
FORGE_TOOL = "forge_subagent"


class GatewayError(RuntimeError):
    """The gateway refused, was unreachable, or returned something unusable."""


class ForgeError(RuntimeError):
    """The forge dispatch failed or the expert returned no usable answer."""


class GatewayClient:
    """A tiny MCP client: handshake once, call tools, re-handshake on 404.

    Two auth modes:
      * host sessions — `Authorization: Bearer <~/.aither/session-bearer>`
      * in-fleet callers — `X-Internal-Key: <AITHER_INTERNAL_SECRET>` (the
        gateway's interservice bypass, verified in gateway/auth.py). Set via
        the AWDELPHI_INTERNAL_KEY env (used by the Discord bot's /dgg group,
        which runs inside a container with the fleet secret but no session
        bearer).
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        bearer_file: Optional[Path] = None,
        timeout_s: float = 180.0,
        internal_key: Optional[str] = None,
    ) -> None:
        self.base_url = (base_url or DEFAULT_GATEWAY_URL).rstrip("/")
        self.bearer_file = Path(bearer_file) if bearer_file else DEFAULT_BEARER_FILE
        self.timeout_s = timeout_s
        self.internal_key = (
            internal_key if internal_key is not None else os.environ.get("AWDELPHI_INTERNAL_KEY")
        )
        self._session_id: Optional[str] = None

    # ------------------------------------------------------------------ auth

    def _bearer(self) -> str:
        try:
            token = self.bearer_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise GatewayError(
                f"cannot read bearer at {self.bearer_file} — run "
                "`python AitherOS/dev/tools/mint_session_bearer.py` to mint one"
            ) from exc
        if not token:
            raise GatewayError(
                f"bearer file {self.bearer_file} is empty — run "
                "`python AitherOS/dev/tools/mint_session_bearer.py`"
            )
        return token

    def _headers(self, with_session: bool = True) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.internal_key:
            headers["X-Internal-Key"] = self.internal_key
        else:
            headers["Authorization"] = f"Bearer {self._bearer()}"
        if with_session and self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    # ------------------------------------------------------------- handshake

    def connect(self) -> "GatewayClient":
        """initialize + notifications/initialized; returns self."""
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "awdelphi", "version": "0.1.0"},
            },
        }
        try:
            resp = httpx.post(
                self.base_url,
                json=body,
                headers=self._headers(with_session=False),
                timeout=self.timeout_s,
            )
        except httpx.HTTPError as exc:
            raise GatewayError(
                f"gateway unreachable at {self.base_url} — no rounds were run. "
                "Is the gateway up? (`docker ps --filter name=mcpgateway` / "
                "`curl http://127.0.0.1:8182/health`)"
            ) from exc

        if resp.status_code in (401, 403):
            raise GatewayError(
                f"gateway refused ({resp.status_code}) — "
                + (
                    "the internal key is wrong (AWDELPHI_INTERNAL_KEY)"
                    if self.internal_key
                    else "re-mint the session bearer: "
                    "`python AitherOS/dev/tools/mint_session_bearer.py`"
                )
            )
        if resp.status_code >= 400:
            raise GatewayError(f"gateway handshake failed: HTTP {resp.status_code}")

        session = resp.headers.get("Mcp-Session-Id")
        if not session:
            raise GatewayError("gateway did not return Mcp-Session-Id on initialize")
        self._session_id = session

        self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            expect_result=False,
        )
        return self

    def _post(self, body: Dict[str, Any], expect_result: bool = True) -> Any:
        """POST one JSON-RPC message; returns the result (or None)."""
        try:
            resp = httpx.post(
                self.base_url,
                json=body,
                headers=self._headers(),
                timeout=self.timeout_s,
            )
        except httpx.HTTPError as exc:
            raise GatewayError(f"gateway unreachable during call: {exc}") from exc

        if resp.status_code in (401, 403):
            raise GatewayError(
                f"gateway refused ({resp.status_code}) — "
                + (
                    "the internal key is wrong (AWDELPHI_INTERNAL_KEY)"
                    if self.internal_key
                    else "re-mint the session bearer: "
                    "`python AitherOS/dev/tools/mint_session_bearer.py`"
                )
            )
        if resp.status_code == 404:
            # Session lost server-side: handshake again once, then the caller
            # retries the operation.
            raise GatewayError("session lost — re-handshake required")
        if resp.status_code >= 400:
            raise GatewayError(f"gateway call failed: HTTP {resp.status_code}")

        # Notifications (and HTTP 202) carry NO response body — that is the
        # streamable-HTTP spec, not a broken gateway. Parsing an empty body
        # as JSON was producing the misleading 'gateway returned non-JSON'
        # on every notifications/initialized, which read as a dead gateway
        # while it was serving fine.
        if resp.status_code == 202 or not expect_result:
            return None
        if not resp.content.strip():
            raise GatewayError(
                f"gateway returned an EMPTY body for a call that expected a "
                f"result (HTTP {resp.status_code})"
            )

        payload = _parse_response(resp)
        if "error" in payload and payload.get("error"):
            raise GatewayError(f"gateway error: {payload['error']}")
        return payload.get("result")

    # ---------------------------------------------------------------- tools

    def call_tool(self, name: str, args: Dict[str, Any]) -> Any:
        """Call one MCP tool; returns the parsed result.

        Re-handshakes once on a lost session (404), then re-issues the call.
        """
        body = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
        try:
            result = self._post(body)
        except GatewayError as exc:
            if "session lost" not in str(exc):
                raise
            self._session_id = None
            self.connect()
            result = self._post(body)
        return _extract_text_result(result)

    def call_forge(
        self,
        task: str,
        agent_type: str,
        effort: int,
        max_turns: int = 2,
        max_seconds: int = 120,
    ) -> Dict[str, Any]:
        """Dispatch one expert through forge_subagent; returns the parsed
        ForgeTaskResult dict {status, output, result, acceptance_verdict}."""
        result = self.call_tool(
            FORGE_TOOL,
            {
                "task": task,
                "agent_type": agent_type,
                "effort": effort,
                "max_turns": max_turns,
                "max_seconds": max_seconds,
            },
        )
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
            except json.JSONDecodeError as exc:
                raise ForgeError(f"forge returned non-JSON output: {result[:200]!r}") from exc
            result = parsed
        if not isinstance(result, dict):
            raise ForgeError(f"forge returned an unusable result: {result!r}")
        status = str(result.get("status", ""))
        if status and status.lower() not in ("ok", "completed", "success", "done"):
            raise ForgeError(f"forge dispatch failed with status {status!r}")
        return result


def _parse_response(resp: httpx.Response) -> Dict[str, Any]:
    """Parse a JSON-RPC response that may be JSON or text/event-stream."""
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                try:
                    payload = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict) and ("result" in payload or "error" in payload):
                    return payload
        raise GatewayError("gateway SSE response carried no JSON-RPC result")
    try:
        payload = resp.json()
    except json.JSONDecodeError as exc:
        raise GatewayError(f"gateway returned non-JSON: {resp.text[:200]!r}") from exc
    if not isinstance(payload, dict):
        raise GatewayError(f"gateway returned an unusable payload: {payload!r}")
    return payload


def _extract_text_result(result: Any) -> Any:
    """MCP tools/call returns {content: [{type: text, text: ...}], isError}."""
    if not isinstance(result, dict):
        return result
    if result.get("isError"):
        raise ForgeError(f"tool error: {result.get('content', [])!r}")
    texts: List[str] = []
    for item in result.get("content", []) or []:
        if isinstance(item, dict) and item.get("type") == "text":
            texts.append(item.get("text", ""))
    if not texts:
        # Some tools return the value directly; pass it through.
        return result.get("result", result)
    joined = "\n".join(texts).strip()
    try:
        return json.loads(joined)
    except (json.JSONDecodeError, TypeError):
        return joined

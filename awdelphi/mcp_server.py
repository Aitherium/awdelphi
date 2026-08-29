"""An MCP server for awdelphi — let any coding agent run an expert panel.

    pip install "awdelphi[mcp]"

then point a client at `awdelphi mcp`:

    {"mcpServers": {"awdelphi": {"command": "awdelphi",
                                  "args": ["mcp"],
                                  "env": {"AWDELPHI_GATEWAY": "..."}}}}

DESIGN NOTES THAT MATTER
-------------------------
**Connection is fixed at server start**, from AWDELPHI_GATEWAY /
AWDELPHI_RUNS_DIR in the environment — not a per-call argument (same
doctrine as awrelay's mcp_server.py: a tool argument is caller-suppliable,
and letting a tool call redirect where a bearer token goes is the same shape
as accepting a caller-supplied identity for an authz decision,
security-review-patterns.md #2).

**delphi_run returns immediately with a run_id**; the panel executes on a
background thread and delphi_status polls the persisted run JSON. Panels
take minutes; a tool call that blocked for the whole run would time out the
client for no benefit, and the run JSON is the shared state any process can
read (the CLI's status/show read the same files).

SDK VERSION
-----------
Written against the `mcp` 2.x `MCPServer` API, matching awrelay's
mcp_server.py — see that module's docstring for why the version is pinned
rather than detected.
"""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict

_INSTALL_HINT = (
    "The MCP server needs the `mcp` package: pip install \"awdelphi[mcp]\". "
    "Raised rather than degraded, because an MCP server that starts and "
    "serves no tools looks to the client exactly like a server with nothing "
    "to offer."
)


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def build_server():
    """Construct the MCP server. Raises ImportError if `mcp` is absent."""
    try:
        from mcp.server import MCPServer
    except ImportError as exc:  # pragma: no cover - exercised by a bare install
        raise ImportError(_INSTALL_HINT) from exc

    from pathlib import Path

    from awdelphi.engine import DEFAULT_RUNS_DIR, DelphiEngine, RunNotFoundError
    from awdelphi.gateway import GatewayClient, GatewayError
    from awdelphi.protocol import ProtocolError, RunRequest

    gateway_url = _env("AWDELPHI_GATEWAY", "http://127.0.0.1:8182/mcp")
    arena_url = _env("AWDELPHI_ARENA_URL", "http://127.0.0.1:8179")
    runs_dir = Path(_env("AWDELPHI_RUNS_DIR", str(DEFAULT_RUNS_DIR)))
    pool = ThreadPoolExecutor(max_workers=2)

    server = MCPServer(
        name="awdelphi",
        instructions=(
            "Anonymous multi-round expert panels. delphi_run starts a panel: "
            "N roster experts answer a question independently (round 1), then "
            "see only the anonymized group view and revise, until convergence "
            "(agreement + stability) or max rounds. The deliverable carries "
            "the full round trace; a panel that does not converge says so with "
            "a dissent map. delphi_status polls a running panel."
        ),
    )

    @server.tool(
        name="delphi_run",
        description=(
            "Start an anonymous expert panel on a question and return a run_id "
            "immediately. Poll delphi_status(run_id) for the outcome. Experts "
            "default to demiurge, athena, hydra. Mode review wraps the "
            "questionnaire in the duel-arena CRITIC/ADVOCATE structure and "
            "maps the verdict to approve | approve_with_conditions | reject."
        ),
    )
    async def delphi_run(
        question: str,
        experts: str | None = None,
        context: str | None = None,
        max_rounds: int = 3,
        threshold: float = 0.7,
        effort: int = 5,
        mode: str = "decision",
        arena: bool = False,
        relay_channel: str | None = None,
    ) -> str:
        """
        Args:
            question: The decision the panel must answer.
            experts: Comma-separated roster agent names (default demiurge,athena,hydra).
            context: Background the experts should read before answering.
            max_rounds: 1..6 (default 3). Round 1 never converges.
            threshold: Agreement needed to converge, 0.5..1.0 (default 0.7).
            effort: 1..10 passed through to forge.
            mode: decision | review. Review wraps CRITIC/ADVOCATE rounds.
            arena: import the deliverable into the KodokEvo arena (ELO/archive).
            relay_channel: post a finding to this relay channel (soft-failing).
        """
        try:
            request = RunRequest(
                question=question,
                context=context or "",
                experts=[e.strip() for e in (experts or "demiurge,athena,hydra").split(",")],
                max_rounds=max_rounds,
                threshold=threshold,
                effort=effort,
                mode=mode,
            )
        except ProtocolError as exc:
            return json.dumps({"error": str(exc)})
        engine = DelphiEngine(
            request, runs_dir=runs_dir, gateway=GatewayClient(base_url=gateway_url)
        )

        def _run_panel() -> Dict[str, Any]:
            try:
                deliverable = engine.execute().to_dict()
                if arena and deliverable.get("outcome"):
                    try:
                        import httpx

                        resp = httpx.post(
                            f"{arena_url.rstrip('/')}/panels/import",
                            json=engine.arena_import_payload() or deliverable,
                            timeout=30.0,
                        )
                        if resp.status_code >= 400:
                            print(
                                f"awdelphi: arena import skipped ({resp.status_code})",
                                file=sys.stderr,
                            )
                    except httpx.HTTPError as exc:
                        # Best-effort by design — the run stands either way —
                        # but the drop must be LOUD, never silent:
                        print(f"awdelphi: arena import skipped ({exc})", file=sys.stderr)
                return deliverable
            except GatewayError as exc:
                return {"error": str(exc), "run_id": engine.run_id}

        pool.submit(_run_panel)
        return json.dumps({"run_id": engine.run_id, "status": "started"})

    @server.tool(
        name="delphi_status",
        description="Poll the state and progress of a panel run.",
    )
    async def delphi_status(run_id: str) -> str:
        try:
            engine = DelphiEngine.from_run(run_id, runs_dir=runs_dir)
        except RunNotFoundError as exc:
            return json.dumps({"error": f"no such run: {exc}"})
        return json.dumps(engine.status())

    @server.tool(
        name="delphi_list",
        description="List local panel runs with their state and outcome.",
    )
    async def delphi_list() -> str:
        rows: list[Dict[str, Any]] = []
        if runs_dir.exists():
            for path in sorted(runs_dir.glob("*.json")):
                try:
                    engine = DelphiEngine.from_run(path.stem, runs_dir=runs_dir)
                except RunNotFoundError:
                    continue
                rows.append(engine.status())
        return json.dumps(rows)

    @server.tool(
        name="delphi_cancel",
        description="Cancel a running panel; the deliverable records failed/cancelled.",
    )
    async def delphi_cancel(run_id: str) -> str:
        try:
            engine = DelphiEngine.from_run(run_id, runs_dir=runs_dir)
        except RunNotFoundError as exc:
            return json.dumps({"error": f"no such run: {exc}"})
        deliverable = engine.cancel()
        return json.dumps(deliverable.to_dict())

    @server.tool(
        name="delphi_history",
        description="The full run snapshot: request, round trace, deliverable.",
    )
    async def delphi_history(run_id: str) -> str:
        try:
            engine = DelphiEngine.from_run(run_id, runs_dir=runs_dir)
        except RunNotFoundError as exc:
            return json.dumps({"error": f"no such run: {exc}"})
        return json.dumps(engine._snapshot())  # noqa: SLF001 — the snapshot is the point

    return server


def main(argv: list[str] | None = None) -> int:
    """Serve over stdio. Blocks until the client disconnects."""
    import asyncio

    try:
        server = build_server()
    except ImportError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        asyncio.run(server.run_stdio_async())
    except KeyboardInterrupt:
        return 130
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

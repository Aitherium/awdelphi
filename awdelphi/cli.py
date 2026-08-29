"""awdelphi CLI — run and manage anonymous expert panels.

Exit codes (awrelay convention): 0 ok, 1 the gateway/run refused or failed,
2 usage or protocol error. `--self-test` proves the machinery offline.

    awdelphi run "Should the fleet pin the model pool to the DGX?" \\
        --experts demiurge,athena,hydra --context "..." --arena
    awdelphi status <run_id>
    awdelphi show <run_id> --json
    awdelphi list
    awdelphi cancel <run_id>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from awdelphi.engine import DEFAULT_RUNS_DIR, DelphiEngine, RunNotFoundError
from awdelphi.gateway import GatewayClient, GatewayError
from awdelphi.protocol import ProtocolError, RunRequest

ARENA_DEFAULT_URL = "http://127.0.0.1:8179"


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _load_run(run_id: str, runs_dir: Path) -> DelphiEngine:
    return DelphiEngine.from_run(run_id, runs_dir=runs_dir)


def cmd_run(args: argparse.Namespace) -> int:
    try:
        request = RunRequest(
            question=args.question,
            context=args.context or "",
            experts=(
                [e.strip() for e in args.experts.split(",")]
                if args.experts
                else ["demiurge", "athena", "hydra"]
            ),
            max_rounds=args.max_rounds,
            threshold=args.threshold,
            effort=args.effort,
            per_expert_timeout_s=args.timeout,
            mode=args.mode,
        )
    except ProtocolError as exc:
        print(f"awdelphi: {exc}", file=sys.stderr)
        return 2

    gateway = GatewayClient() if args.gateway is None else GatewayClient(base_url=args.gateway)
    # The door check: a dead gateway must fail BEFORE any round runs — a
    # mid-round gateway death is a missing-expert (honest failed run), but
    # "no rounds were run" is a different, louder failure.
    try:
        gateway.connect()
    except GatewayError as exc:
        print(f"awdelphi: {exc}", file=sys.stderr)
        return 1

    engine = DelphiEngine(request, runs_dir=args.runs_dir, gateway=gateway)
    print(f"awdelphi: run {engine.run_id} — panel {request.question!r}", file=sys.stderr)

    try:
        deliverable = engine.execute()
    except GatewayError as exc:
        print(f"awdelphi: {exc}", file=sys.stderr)
        return 1

    d = deliverable.to_dict()
    if args.arena:
        _post_arena(engine.arena_import_payload() or d, args.arena_url)
    if args.relay_channel:
        from awdelphi.relay import post_deliverable

        post_deliverable(d, args.relay_channel)

    if args.json:
        _print_json(d)
    else:
        _print_summary(d)
    return 0


def _post_arena(d: Dict[str, Any], url: str) -> None:
    import httpx

    try:
        resp = httpx.post(f"{url.rstrip('/')}/panels/import", json=d, timeout=30.0)
        if resp.status_code >= 400:
            print(f"awdelphi: arena import skipped ({resp.status_code})", file=sys.stderr)
        else:
            print(f"awdelphi: panel imported to arena at {url}", file=sys.stderr)
    except httpx.HTTPError as exc:
        print(f"awdelphi: arena import skipped ({exc})", file=sys.stderr)


def _print_summary(d: Dict[str, Any]) -> None:
    print(f"run_id        {d['run_id']}")
    print(f"question      {d['question']}")
    print(f"outcome       {d['outcome']}")
    if d.get("consensus_verdict"):
        print(f"verdict       {d['consensus_verdict']}")
        print(f"confidence    {d['confidence']:.2f} (threshold {d['threshold']})")
    if d.get("failed_reason"):
        print(f"failed        {d['failed_reason']}")
    counts = d.get("final_counts") or {}
    if counts:
        print("final counts  " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    for r in d.get("round_trace", []):
        line = (
            f"  round {r['round']}: agreement={r['agreement']:.2f} "
            f"stable={r['stability']} "
            f"answered={len(r['answers'])}"
        )
        if r.get("refused"):
            line += f" refused={r['refused']}"
        if r.get("missing"):
            line += f" missing={r['missing']}"
        print(line)
    for g in d.get("dissent_map", []):
        print(f"  {g['verdict']}: {', '.join(g['aliases'])}")


def cmd_status(args: argparse.Namespace) -> int:
    try:
        engine = _load_run(args.run_id, args.runs_dir)
    except RunNotFoundError as exc:
        print(f"awdelphi: no such run: {exc}", file=sys.stderr)
        return 1
    if args.json:
        _print_json(engine.status())
    else:
        status = engine.status()
        print(
            f"{status['run_id']}  {status['state']}"
            + (f"  outcome={status['outcome']}" if status.get("outcome") else "")
            + f"  round={status['current_round']}  {status['question'][:60]!r}"
        )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    rows: List[Dict[str, Any]] = []
    if args.runs_dir.exists():
        for path in sorted(args.runs_dir.glob("*.json")):
            try:
                engine = DelphiEngine.from_run(path.stem, runs_dir=args.runs_dir)
            except RunNotFoundError:
                continue
            rows.append(engine.status())
    if args.json:
        _print_json(rows)
    else:
        for s in rows:
            print(
                f"{s['run_id']}  {s['state']}"
                + (f"  outcome={s['outcome']}" if s.get("outcome") else "")
                + f"  {s['question'][:60]!r}"
            )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    try:
        engine = _load_run(args.run_id, args.runs_dir)
    except RunNotFoundError as exc:
        print(f"awdelphi: no such run: {exc}", file=sys.stderr)
        return 1
    snapshot = engine._snapshot()  # noqa: SLF001 — the full run state is the point of `show`
    if args.json:
        _print_json(snapshot)
    else:
        _print_summary(
            (snapshot.get("deliverable") or {})
            | {"run_id": snapshot["run_id"], "question": snapshot["request"]["question"]}
        )
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    try:
        engine = _load_run(args.run_id, args.runs_dir)
    except RunNotFoundError as exc:
        print(f"awdelphi: no such run: {exc}", file=sys.stderr)
        return 1
    deliverable = engine.cancel()
    if args.json:
        _print_json(deliverable.to_dict())
    else:
        print(f"awdelphi: run {args.run_id} cancelled → {deliverable.outcome}")
    return 0


def cmd_self_test(args: argparse.Namespace) -> int:
    from awdelphi._doctor import selftest

    ok = selftest(verbose=True)
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    # Shared options must work BOTH before the subcommand and after it
    # (`awdelphi --runs-dir X run ...` and `awdelphi run ... --runs-dir X`),
    # which argparse only allows via a common parent parser.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    common.add_argument(
        "--gateway",
        default=None,
        help="MCP gateway URL (default: AWDELPHI_GATEWAY or http://127.0.0.1:8182/mcp)",
    )

    parser = argparse.ArgumentParser(
        prog="awdelphi",
        description="Anonymous multi-round expert panels — a converged answer with a trace.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run a panel to a deliverable", parents=[common])
    p_run.add_argument("question")
    p_run.add_argument(
        "--experts",
        default=None,
        help="comma-separated roster names (default: demiurge,athena,hydra)",
    )
    p_run.add_argument("--context", default="")
    p_run.add_argument("--max-rounds", type=int, default=3)
    p_run.add_argument("--threshold", type=float, default=0.7)
    p_run.add_argument("--effort", type=int, default=5)
    p_run.add_argument("--timeout", type=int, default=120, help="per-expert seconds")
    p_run.add_argument("--mode", choices=["decision", "review"], default="decision")
    p_run.add_argument(
        "--arena", action="store_true", help="import the deliverable into the KodokEvo arena"
    )
    p_run.add_argument("--arena-url", default=ARENA_DEFAULT_URL)
    p_run.add_argument(
        "--relay-channel", default=None, help="post a finding to a relay channel (soft-failing)"
    )
    p_run.add_argument("--json", action="store_true")

    for name in ("status", "show", "cancel"):
        p = sub.add_parser(name, help=f"{name} a run", parents=[common])
        p.add_argument("run_id")
        if name == "status":
            p.add_argument("--json", action="store_true")
        elif name == "show":
            p.add_argument("--json", action="store_true")

    p_list = sub.add_parser("list", help="list local runs", parents=[common])
    p_list.add_argument("--json", action="store_true")
    sub.add_parser("self-test", help="prove the machinery offline", parents=[common])
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "run": cmd_run,
        "status": cmd_status,
        "list": cmd_list,
        "show": cmd_show,
        "cancel": cmd_cancel,
        "self-test": cmd_self_test,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())

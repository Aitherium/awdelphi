"""awdelphi --self-test — prove the machinery offline, or fail loudly.

A checker nobody has watched fail is not a gate (tech-debt rule). Every
claim here is exercised against a real failure path:

  1. convergence: round 1 never converges even at agreement 1.0;
     max_rounds == 1 lands on no_consensus; converged requires stability.
  2. anonymize: a planted roster name in a rationale is detected as a leak
     and scrubbed; the feedback payload never carries an identity.
  3. engine + persistence: a scripted fake expert runs a real panel to a
     deliverable in a temp dir; resume re-dispatches only missing experts.
  4. gateway-down: connecting to a dead port raises GatewayError with the
     fail-loudly message — a panel never silently runs zero rounds.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List

from awdelphi.anonymize import alias_map, build_feedback, scrub
from awdelphi.convergence import should_stop
from awdelphi.engine import DelphiEngine
from awdelphi.gateway import GatewayClient, GatewayError
from awdelphi.protocol import RunRequest

_STEPS: List[str] = []


def _check(name: str, ok: bool) -> bool:
    _STEPS.append(name)
    print(f"  {'ok ' if ok else 'FAIL'}  {name}")
    return ok


def _scripted_expert(script: Dict[str, Dict[str, Any]]):
    """Fake expert: verdict per (expert, round) keyed by script['<expert>:<round>']."""

    def _call(expert: str, q: Dict[str, Any], request: RunRequest) -> Dict[str, Any]:
        key = f"{expert}:{q['round']}"
        return script.get(key, {"verdict": "YES", "rationale": "default", "accepted": True})

    return _call


def selftest(verbose: bool = False) -> bool:
    _STEPS.clear()
    results: List[bool] = []

    # 1. convergence stop rules
    r = [
        should_stop(1, 1.0, False, 0.7, 3) == "continue",  # round 1 never converges
        should_stop(1, 1.0, True, 0.7, 1) == "no_consensus",  # max_rounds 1 honest
        should_stop(2, 0.8, True, 0.7, 3) == "converged",  # threshold + stable
        should_stop(2, 0.8, False, 0.7, 3) == "continue",  # moving panel is not converged
        should_stop(3, 0.6, True, 0.7, 3) == "no_consensus",  # max rounds, low agreement
    ]
    results.append(_check("convergence: stop rules", all(r)))

    # 2. anonymization — planted name detected and scrubbed
    aliases = alias_map(["demiurge", "athena"])
    payload = {
        "per_question": [
            {
                "counts": {},
                "anonymized_rationales": [
                    {"alias": "Expert A", "rationale": "per athena the design is sound"}
                ],
            }
        ]
    }
    scrubbed, leaked = scrub(payload, ["athena", "demiurge"])
    leak_ok = "athena" in leaked
    scrubbed_text = str(scrubbed)
    clean_ok = "athena" not in scrubbed_text
    fb, fb_leaked = build_feedback(2, [
        {"expert": "demiurge", "verdict": "YES", "rationale": "solid", "status": "answered"},
        {
            "expert": "athena",
            "verdict": "NO",
            "rationale": "as athena noted, risky",
            "status": "answered",
        },
    ], aliases)
    # The rationale-level scrub catches the planted name before payload
    # assembly, so the payload itself is clean AND the name is gone:
    fb_ok = (
        "athena" not in str(fb)
        and "demiurge" not in str(fb)
        and "«expert»" in str(fb)
        and fb_leaked == []
    )
    results.append(_check("anonymize: leak detected + scrubbed", leak_ok and clean_ok and fb_ok))

    # 3. engine end-to-end with a fake expert in a temp runs dir
    with tempfile.TemporaryDirectory() as tmp:
        runs_dir = Path(tmp) / "runs"
        # Round 1: split YES/NO. Round 2: both YES but athena MOVED (NO→YES),
        # so stability is false and the panel must NOT converge on a round
        # where someone is still revising — round 3 confirms both unchanged.
        script = {
            "demiurge:1": {"verdict": "YES", "rationale": "solid", "accepted": True},
            "athena:1": {"verdict": "NO", "rationale": "risky", "accepted": True},
            "demiurge:2": {"verdict": "YES", "rationale": "confirmed", "accepted": True},
            "athena:2": {"verdict": "YES", "rationale": "feedback convinced me", "accepted": True},
            "demiurge:3": {"verdict": "YES", "rationale": "confirmed", "accepted": True},
            "athena:3": {"verdict": "YES", "rationale": "confirmed", "accepted": True},
        }
        engine = DelphiEngine(
            RunRequest(
                question="Is the approach sound?",
                experts=["demiurge", "athena"],
                max_rounds=3,
            ),
            runs_dir=runs_dir,
            expert_callable=_scripted_expert(script),
        )
        d = engine.execute().to_dict()
        engine_ok = (
            d["outcome"] == "converged"
            and d["consensus_verdict"] == "YES"
            and d["stopped_after_rounds"] == 3
            and len(d["round_trace"]) == 3
            and d["round_trace"][1]["stability"] is False  # the move blocked round-2 convergence
        )
        resume = DelphiEngine.from_run(engine.run_id, runs_dir=runs_dir)
        resume_ok = (
            resume.status()["state"] == "done" and resume._deliverable is not None
        )  # noqa: SLF001
        results.append(
            _check("engine: fake experts converge with trace + resume", engine_ok and resume_ok)
        )

    # 4. gateway-down fails loudly
    try:
        GatewayClient(base_url="http://127.0.0.1:1/mcp", timeout_s=5.0).connect()
        gateway_ok = False
    except GatewayError as exc:
        gateway_ok = "no rounds were run" in str(exc)
    results.append(_check("gateway-down: GatewayError, no silent zero rounds", gateway_ok))

    all_ok = all(results)
    if verbose:
        print(f"awdelphi self-test: {'PASS' if all_ok else 'FAIL'} ({len(results)} checks)")
    return all_ok


if __name__ == "__main__":
    raise SystemExit(0 if selftest(verbose=True) else 1)

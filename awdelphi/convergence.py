"""Convergence math for awdelphi — pure, deterministic, no runtime deps.

The stop rule mirrors the Delphi method's monitor judgment:

  * agreement  — how close the panel is: modal-answer share per question,
                 averaged over questions. v1 runs a single question, but the
                 shapes aggregate so a multi-item questionnaire slots in.
  * stability  — did anyone move? Evaluated from round 2: every expert who
                 answered both rounds kept their verdict. Revision is the
                 mechanism the method relies on, so a panel that is still
                 moving is NOT converged even at high agreement.
  * stop       — converged: agreement >= threshold AND stable.
                 no_consensus: max rounds reached without the above.
                 Round 1 NEVER converges: no feedback has been seen yet, so
                 agreement there is coincidence, not consensus (the
                 anti-bandwagon core of the method).
"""

from __future__ import annotations

from typing import Any, Dict, List

from awdelphi.protocol import VERDICTS


def count_verdicts(answers: List[Dict[str, Any]]) -> Dict[str, int]:
    """Count answered verdicts; refused/missing answers are not votes."""
    counts = {v: 0 for v in VERDICTS}
    for a in answers:
        if a.get("status", "answered") == "answered":
            verdict = a.get("verdict", "CONDITIONAL")
            counts[verdict] = counts.get(verdict, 0) + 1
    return counts


def answered_count(answers: List[Dict[str, Any]]) -> int:
    return sum(1 for a in answers if a.get("status", "answered") == "answered")


def modal_verdict(counts: Dict[str, int]) -> str:
    """Most-voted verdict; ties break to the verdict with the greater
    commitment (NO over CONDITIONAL over YES is NOT the rule — tie-break by
    VERDICTS order: YES, NO, CONDITIONAL — a deliberate, documented choice:
    a tie on YES/NO means the panel is genuinely split and the modal label is
    only used for counting, never for verdict engineering)."""
    best, best_n = VERDICTS[0], -1
    for v in VERDICTS:
        if counts.get(v, 0) > best_n:
            best, best_n = v, counts[v]
    return best


def agreement(counts: Dict[str, int]) -> float:
    """Modal share of answered votes: max(count)/answered. 0 when nobody
    answered (a round with zero answers is not 100% agreement)."""
    total = sum(counts.values())
    if total == 0:
        return 0.0
    return max(counts.values()) / total


def overall_agreement(counts_list: List[Dict[str, int]]) -> float:
    """Mean agreement over all questions in the questionnaire."""
    if not counts_list:
        return 0.0
    return sum(agreement(c) for c in counts_list) / len(counts_list)


def stability(prev: List[Dict[str, Any]], curr: List[Dict[str, Any]]) -> bool:
    """True when every expert who answered BOTH rounds kept their verdict.

    Experts who were missing or refused in either round are not counted
    against stability — the comparison is over the experts who actually
    participated in both.
    """
    prev_by_expert = {
        a["expert"]: a.get("verdict", "CONDITIONAL")
        for a in prev
        if a.get("status", "answered") == "answered"
    }
    curr_by_expert = {
        a["expert"]: a.get("verdict", "CONDITIONAL")
        for a in curr
        if a.get("status", "answered") == "answered"
    }
    common = set(prev_by_expert) & set(curr_by_expert)
    if not common:
        # Nobody participated in both rounds: there is no revision signal.
        return False
    return all(prev_by_expert[e] == curr_by_expert[e] for e in common)


def should_stop(
    round_no: int,
    agreement_score: float,
    stable: bool,
    threshold: float,
    max_rounds: int,
) -> str:
    """One of: continue | converged | no_consensus.

    Round 1 is never `converged` even at agreement 1.0 — no feedback has
    been seen, so round-1 agreement is coincidence, not consensus. A panel
    whose max_rounds == 1 therefore lands on `no_consensus` after its single
    round (honest: one independent answer round is not a Delphi).
    """
    if round_no >= max_rounds:
        if round_no >= 2 and agreement_score >= threshold and stable:
            return "converged"
        return "no_consensus"
    if round_no < 2:
        return "continue"
    if agreement_score >= threshold and stable:
        return "converged"
    return "continue"


def consensus_verdict_for(modal: str, mode: str) -> str:
    """Map the panel's modal verdict to the arena verdict vocabulary.

    decision mode keeps the YES/NO/CONDITIONAL verdict; review mode maps to
    the duel-arena shape: approve | approve_with_conditions | reject.
    """
    if mode == "review":
        return {
            "YES": "approve",
            "CONDITIONAL": "approve_with_conditions",
            "NO": "reject",
        }[modal]
    return modal


def dissent_map(answers: List[Dict[str, Any]], modal: str) -> List[Dict[str, Any]]:
    """Group final answers by verdict, modal group first.

    Every verdict group carries its aliases and rationales, so a
    no_consensus deliverable shows the full disagreement structure — the
    minority is never silently folded into the majority.
    """
    groups: Dict[str, Dict[str, Any]] = {}
    for a in answers:
        if a.get("status", "answered") != "answered":
            continue
        verdict = a.get("verdict", "CONDITIONAL")
        g = groups.setdefault(verdict, {"verdict": verdict, "aliases": [], "rationales": []})
        g["aliases"].append(a["alias"])
        if a.get("rationale"):
            g["rationales"].append({"alias": a["alias"], "rationale": a["rationale"]})
    ordered = sorted(
        groups.values(),
        key=lambda g: 0 if g["verdict"] == modal else 1,
    )
    return ordered


def supporting_rationale_for(answers: List[Dict[str, Any]], modal: str) -> str:
    """Concatenate the modal group's rationales as the consensus rationale."""
    parts = []
    for a in answers:
        if a.get("status", "answered") != "answered":
            continue
        if a.get("verdict", "CONDITIONAL") == modal and a.get("rationale"):
            parts.append(a["rationale"].strip())
    return " ".join(parts) if parts else ""

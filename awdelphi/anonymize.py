"""Anonymization for awdelphi — the method's anti-bandwagon core.

Experts must never see WHO said what, only WHAT was said. Two mechanisms:

  1. alias map — each run assigns stable aliases (Expert A, Expert B, ...)
     to the sorted expert list. Aliases persist in the run JSON so identity
     is never re-derived and aliases are stable across rounds.
  2. scrubber — every feedback payload is walked recursively: identity keys
     (expert/sender/nick/author/agent) are DROPPED silently, and any known
     roster name appearing in surviving string content is replaced. The
     scrubber RETURNS the names it found in surviving content; the engine
     hard-fails the round if any leaked — a feedback payload that carries an
     identity is worse than no feedback.

The identity keys are dropped WITHOUT being reported as leaks: the whole
point of those fields is that they are removed, so finding a name in them is
expected, not an error. Leak detection applies only to content that SHIPS.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from awdelphi.convergence import answered_count, modal_verdict

# Roster names an expert might slip into a rationale. Deliberately broad:
# a false positive scrubs one word; a false negative leaks an identity.
ROSTER_NAMES = [
    "demiurge",
    "atlas",
    "athena",
    "hydra",
    "apollo",
    "prometheus",
    "lyra",
    "saga",
    "vera",
    "hera",
    "mr_robot",
    "aither",
    "genesis",
    "aeon",
    "council",
    "orchestrator",
    "scribe",
    "tester",
    "viviane",
    "morgana",
    "iris",
]

_IDENTITY_KEYS = {"expert", "sender", "nick", "author", "agent", "agent_id", "expert_name"}

_ALIAS_PREFIX = "Expert "


def alias_map(experts: List[str]) -> Dict[str, str]:
    """Stable per-run aliases: sorted experts -> Expert A, Expert B, ..."""
    names = sorted(set(experts))
    return {name: f"{_ALIAS_PREFIX}{chr(ord('A') + i)}" for i, name in enumerate(names)}


def alias_for(expert: str, aliases: Dict[str, str]) -> str:
    return aliases.get(expert, _ALIAS_PREFIX + "?")


def _name_pattern(names: List[str]) -> re.Pattern:
    escaped = [re.escape(n) for n in names if n]
    if not escaped:
        return re.compile(r"(?!)")  # matches nothing
    return re.compile(rf"\b(?:{'|'.join(escaped)})\b", re.IGNORECASE)


def scrub(
    payload: Any,
    names: Optional[List[str]] = None,
    _pattern: Optional[re.Pattern] = None,
) -> Tuple[Any, List[str]]:
    """Recursively drop identity fields and replace known names.

    Returns (scrubbed_payload, leaked_names). Empty leak list = clean.
    Identity keys are removed silently — see module docstring.

    Leak propagation: a hit at a string leaf is merged up through every
    parent level. Collecting leaks from the SCRUBBED payload instead (the
    earlier bug) always returns [] — the replaced name is gone by the time
    the parent looks, so the fail-closed gate never fired.
    """
    names = names or []
    pattern = _pattern or _name_pattern(names)

    if isinstance(payload, dict):
        out: Dict[str, Any] = {}
        leaked: List[str] = []
        for key, value in payload.items():
            if key in _IDENTITY_KEYS:
                continue  # dropped by design, never reported
            out[key], sub_leaks = scrub(value, names, pattern)
            leaked.extend(sub_leaks)
        return out, leaked

    if isinstance(payload, list):
        out_list: List[Any] = []
        leaked_list: List[str] = []
        for item in payload:
            scrubbed, sub_leaks = scrub(item, names, pattern)
            out_list.append(scrubbed)
            leaked_list.extend(sub_leaks)
        return out_list, leaked_list

    if isinstance(payload, str):
        hits = pattern.findall(payload)
        return (pattern.sub("«expert»", payload), hits) if hits else (payload, [])

    return payload, []


def build_feedback(
    round_no: int,
    answers: List[Dict[str, Any]],
    aliases: Dict[str, str],
) -> Tuple[Dict[str, Any], List[str]]:
    """Build the anonymized feedback payload for the NEXT round.

    Returns (feedback, leaked_names). The engine refuses to use feedback
    with any leak — identity never ships.
    """
    counts: Dict[str, int] = {"YES": 0, "NO": 0, "CONDITIONAL": 0}
    rationales: List[Dict[str, str]] = []
    names = list(ROSTER_NAMES)
    for a in answers:
        if a.get("status", "answered") != "answered":
            continue
        verdict = a.get("verdict", "CONDITIONAL")
        counts[verdict] = counts.get(verdict, 0) + 1
        rationale = a.get("rationale", "")
        alias = alias_for(a["expert"], aliases)
        scrubbed_text, _ = scrub(rationale, names)
        rationales.append({"alias": alias, "verdict": verdict, "rationale": scrubbed_text})

    feedback = {
        "round": round_no,
        "per_question": [
            {
                "question_index": 0,
                "counts": counts,
                "modal": modal_verdict(counts),
                "answered": answered_count(answers),
                "anonymized_rationales": rationales,
            }
        ],
    }
    scrubbed_feedback, leaked = scrub(feedback, names)
    return scrubbed_feedback, leaked

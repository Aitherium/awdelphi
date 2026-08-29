"""Schemas for an awdelphi panel.

Pure dataclasses and validation — no runtime dependencies beyond the stdlib,
so the protocol stays importable on any machine that has the package.

Verdict vocabulary is deliberately small: YES / NO / CONDITIONAL. A free-text
verdict from an expert maps to the nearest member (`parse_verdict`), so a
panel can never be derailed by an unparseable answer.

RunRequest is single-question in v1. The Feedback shape carries
`per_question` arrays so a multi-item questionnaire can grow later without
changing the wire format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

VERDICTS = ("YES", "NO", "CONDITIONAL")
MODES = ("decision", "review")
OUTCOMES = ("converged", "no_consensus", "failed")

DEFAULT_MAX_ROUNDS = 3
DEFAULT_THRESHOLD = 0.7
DEFAULT_EFFORT = 5
DEFAULT_PER_EXPERT_TIMEOUT_S = 120


class ProtocolError(ValueError):
    """A run request or answer violated the protocol."""


def parse_verdict(text: str) -> str:
    """Map a free-text verdict to YES / NO / CONDITIONAL.

    Any text that does not clearly say yes or no counts as CONDITIONAL —
    a hedged answer is a conditional answer, not an error.
    """
    t = (text or "").strip().upper()
    if t.startswith("YES"):
        return "YES"
    if t.startswith("NO"):
        return "NO"
    return "CONDITIONAL"


@dataclass
class RunRequest:
    """A panel to run.

    experts must contain at least two DISTINCT names — a panel of one is not
    a panel (the method requires at least two expert roles), and a duplicate
    name is deduped with a warning at construction.
    """

    question: str
    context: str = ""
    experts: List[str] = field(default_factory=lambda: ["demiurge", "athena", "hydra"])
    max_rounds: int = DEFAULT_MAX_ROUNDS
    threshold: float = DEFAULT_THRESHOLD
    effort: int = DEFAULT_EFFORT
    per_expert_timeout_s: int = DEFAULT_PER_EXPERT_TIMEOUT_S
    mode: str = "decision"

    def __post_init__(self) -> None:
        if not self.question or not self.question.strip():
            raise ProtocolError("question must be non-empty")
        deduped: List[str] = []
        for name in self.experts:
            name = (name or "").strip()
            if not name:
                continue
            if name not in deduped:
                deduped.append(name)
        if len(deduped) < 2:
            raise ProtocolError("a panel needs at least two distinct experts")
        self.experts = deduped
        if not 1 <= self.max_rounds <= 6:
            raise ProtocolError("max_rounds must be 1..6")
        if not 0.5 <= self.threshold <= 1.0:
            raise ProtocolError("threshold must be 0.5..1.0")
        if not 1 <= self.effort <= 10:
            raise ProtocolError("effort must be 1..10")
        if self.per_expert_timeout_s < 10:
            raise ProtocolError("per_expert_timeout_s must be >= 10")
        if self.mode not in MODES:
            raise ProtocolError(f"mode must be one of {MODES}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "context": self.context,
            "experts": list(self.experts),
            "max_rounds": self.max_rounds,
            "threshold": self.threshold,
            "effort": self.effort,
            "per_expert_timeout_s": self.per_expert_timeout_s,
            "mode": self.mode,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RunRequest":
        return cls(
            question=d["question"],
            context=d.get("context", ""),
            experts=list(d.get("experts", ["demiurge", "athena", "hydra"])),
            max_rounds=int(d.get("max_rounds", DEFAULT_MAX_ROUNDS)),
            threshold=float(d.get("threshold", DEFAULT_THRESHOLD)),
            effort=int(d.get("effort", DEFAULT_EFFORT)),
            per_expert_timeout_s=int(d.get("per_expert_timeout_s", DEFAULT_PER_EXPERT_TIMEOUT_S)),
            mode=d.get("mode", "decision"),
        )


@dataclass
class ExpertAnswer:
    """One expert's answer in one round.

    status: answered | refused | missing
      refused  — the expert returned acceptance_verdict.approved == False
                 (an abstention; excluded from the agreement denominator).
      missing  — the expert never returned an answer after retries.
    """

    round: int
    expert: str
    verdict: str = "CONDITIONAL"
    rationale: str = ""
    revision_note: Optional[str] = None
    status: str = "answered"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "round": self.round,
            "expert": self.expert,
            "verdict": self.verdict,
            "rationale": self.rationale,
            "revision_note": self.revision_note,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ExpertAnswer":
        return cls(
            round=int(d["round"]),
            expert=d["expert"],
            verdict=d.get("verdict", "CONDITIONAL"),
            rationale=d.get("rationale", ""),
            revision_note=d.get("revision_note"),
            status=d.get("status", "answered"),
        )


@dataclass
class RoundRecord:
    """Everything the monitor kept from one round — the trace unit."""

    round: int
    answers: List[Dict[str, Any]]  # alias-encoded: {alias, verdict, rationale}
    agreement: float = 0.0
    stability: bool = False
    feedback_given: bool = False
    refused: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "round": self.round,
            "answers": self.answers,
            "agreement": self.agreement,
            "stability": self.stability,
            "feedback_given": self.feedback_given,
            "refused": self.refused,
            "missing": self.missing,
        }


@dataclass
class Deliverable:
    """The panel's final output. A no_consensus / failed outcome is still a
    deliverable — the method's honesty rule is that a panel that did not
    converge says so, with the dissent map, instead of forcing one."""

    run_id: str
    question: str
    context: str
    mode: str
    outcome: str
    consensus_verdict: Optional[str]
    confidence: float
    supporting_rationale: str
    final_counts: Dict[str, int]
    round_trace: List[Dict[str, Any]]
    dissent_map: List[Dict[str, Any]]
    stopped_after_rounds: int
    threshold: float
    max_rounds: int
    failed_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "question": self.question,
            "context": self.context,
            "mode": self.mode,
            "outcome": self.outcome,
            "consensus_verdict": self.consensus_verdict,
            "confidence": self.confidence,
            "supporting_rationale": self.supporting_rationale,
            "final_counts": self.final_counts,
            "round_trace": self.round_trace,
            "dissent_map": self.dissent_map,
            "stopped_after_rounds": self.stopped_after_rounds,
            "threshold": self.threshold,
            "max_rounds": self.max_rounds,
            "failed_reason": self.failed_reason,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Deliverable":
        return cls(
            run_id=d["run_id"],
            question=d["question"],
            context=d.get("context", ""),
            mode=d.get("mode", "decision"),
            outcome=d["outcome"],
            consensus_verdict=d.get("consensus_verdict"),
            confidence=float(d.get("confidence", 0.0)),
            supporting_rationale=d.get("supporting_rationale", ""),
            final_counts=d.get("final_counts", {}),
            round_trace=d.get("round_trace", []),
            dissent_map=d.get("dissent_map", []),
            stopped_after_rounds=int(d.get("stopped_after_rounds", 0)),
            threshold=float(d.get("threshold", DEFAULT_THRESHOLD)),
            max_rounds=int(d.get("max_rounds", DEFAULT_MAX_ROUNDS)),
            failed_reason=d.get("failed_reason"),
        )


def questionnaire(
    round_no: int, request: RunRequest, feedback: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Build the questionnaire delivered to one expert for one round.

    Round 1 carries NO feedback field — the independence rule. Feedback first
    appears in round 2, and only as the anonymized group view.
    """
    q: Dict[str, Any] = {
        "round": round_no,
        "question": request.question,
        "context": request.context,
        "mode": request.mode,
    }
    if feedback is not None:
        q["feedback"] = feedback
    return q


def forge_task_text(q: Dict[str, Any], alias: str) -> str:
    """The task text sent to forge_subagent for one expert in one round."""
    lines = [
        f"You are Expert {alias} on an anonymous panel answering one question.",
        f"ROUND {q['round']}",
        f"QUESTION: {q['question']}",
    ]
    if q.get("context"):
        lines.append(f"CONTEXT: {q['context']}")
    if q.get("feedback"):
        lines.append("GROUP VIEW FROM THE PREVIOUS ROUND (anonymized):")
        lines.append(_render_feedback(q["feedback"]))
    lines.append(
        "Answer with a JSON object exactly like: "
        '{"verdict": "YES" | "NO" | "CONDITIONAL", '
        '"rationale": "<your reasoning, specific to your expertise>", '
        '"revision_note": "<only if you changed your verdict vs last round; else null>"}'
    )
    if q["mode"] == "review" and q["round"] == 1:
        lines.append(
            "This is an adversarial review. Your rationale must be numbered findings, "
            'each as {"id": 1, "severity": "high|medium|low", "finding": "...", '
            '"mechanism": "..."} in a JSON list under "findings".'
        )
    if q["mode"] == "review" and q.get("feedback"):
        lines.append(
            "You are now the advocate. For each anonymized finding above, state "
            '"agree-and-fix", "refute", or "accept-with-conditions" with a one-line '
            "reason, as a JSON list under \"responses\"."
        )
    return "\n".join(lines)


def _render_feedback(fb: Dict[str, Any]) -> str:
    rows = []
    for q in fb.get("per_question", []):
        counts = ", ".join(f"{k}={v}" for k, v in q.get("counts", {}).items())
        rows.append(f"  counts [{counts}] modal={q.get('modal')}")
        for r in q.get("anonymized_rationales", []):
            rows.append(f"  - {r.get('alias')}: {r.get('rationale')}")
    return "\n".join(rows)

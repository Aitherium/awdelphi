"""The DelphiEngine — runs a panel to its deliverable.

State machine: created → round1 → feedback → roundN → converged | no_consensus
| failed → done. The run JSON is persisted after EVERY transition (atomic
tmp+rename) under ~/.aither/delphi/<run_id>.json, so status/show/resume work
at any point and a crashed run resumes by re-dispatching only the experts
whose answers are missing for the current round.

The monitor's work is entirely deterministic; the ONLY LLM calls are the
expert questionnaires. Round-1 answers are independent (no feedback field).
From round 2 the questionnaire carries the ANONYMIZED group view, and the
scrubber's leak check is a hard gate: feedback that would ship an identity
fails the round, never ships.

An expert that returns acceptance_verdict.approved == False abstains
(excluded from the agreement denominator). Fewer than two answered experts in
a round fails the run — a 1-expert panel is not a panel, and fake consensus
is worse than none.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from awdelphi.anonymize import alias_for, alias_map, build_feedback
from awdelphi.convergence import (
    consensus_verdict_for,
    count_verdicts,
    dissent_map,
    modal_verdict,
    overall_agreement,
    should_stop,
    stability,
    supporting_rationale_for,
)
from awdelphi.gateway import GatewayClient
from awdelphi.protocol import (
    Deliverable,
    ExpertAnswer,
    RoundRecord,
    RunRequest,
    forge_task_text,
    parse_verdict,
)

DEFAULT_RUNS_DIR = Path.home() / ".aither" / "delphi"


class RunNotFoundError(KeyError):
    """No run JSON exists for the requested run_id."""


class DelphiEngine:
    """One panel. Construct with a RunRequest and execute(); or resume an
    existing run with from_run(run_id)."""

    def __init__(
        self,
        request: RunRequest,
        runs_dir: Optional[Path] = None,
        expert_callable: Optional[Callable[[str, Dict[str, Any], RunRequest], Dict[str, Any]]]
        = None,
        gateway: Optional[GatewayClient] = None,
    ) -> None:
        self.request = request
        self.runs_dir = Path(runs_dir) if runs_dir else DEFAULT_RUNS_DIR
        self.gateway = gateway
        self.expert_callable = expert_callable or self._default_expert
        self.run_id = uuid.uuid4().hex[:12]
        self.state = "created"
        self.current_round = 0
        self.rounds: List[RoundRecord] = []
        self._last_feedback: Optional[Dict[str, Any]] = None
        self._failed_reason: Optional[str] = None
        self._aliases = alias_map(request.experts)
        self._previous_answers: List[Dict[str, Any]] = []
        self._deliverable: Optional[Deliverable] = None

    # ------------------------------------------------------------ persistence

    def _run_path(self) -> Path:
        return self.runs_dir / f"{self.run_id}.json"

    def _persist(self) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        data = self._snapshot()
        fd, tmp = tempfile.mkstemp(dir=self.runs_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self._run_path())
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _snapshot(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "request": self.request.to_dict(),
            "state": self.state,
            "current_round": self.current_round,
            "rounds": [r.to_dict() for r in self.rounds],
            "aliases": self._aliases,
            "previous_answers": self._previous_answers,
            "failed_reason": self._failed_reason,
            "deliverable": self._deliverable.to_dict() if self._deliverable else None,
        }

    @classmethod
    def from_run(
        cls,
        run_id: str,
        runs_dir: Optional[Path] = None,
        expert_callable: Optional[Callable[[str, Dict[str, Any], RunRequest], Dict[str, Any]]]
        = None,
        gateway: Optional[GatewayClient] = None,
    ) -> "DelphiEngine":
        runs_dir = Path(runs_dir) if runs_dir else DEFAULT_RUNS_DIR
        path = runs_dir / f"{run_id}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RunNotFoundError(run_id) from exc
        except json.JSONDecodeError as exc:
            raise RunNotFoundError(f"{run_id} (corrupt run JSON: {exc})") from exc

        engine = cls(
            RunRequest.from_dict(data["request"]),
            runs_dir=runs_dir,
            expert_callable=expert_callable,
            gateway=gateway,
        )
        engine.run_id = run_id
        engine.state = data.get("state", "created")
        engine.current_round = int(data.get("current_round", 0))
        engine.rounds = [RoundRecord(**r) for r in data.get("rounds", [])]
        engine._aliases = data.get("aliases", engine._aliases)
        engine._previous_answers = data.get("previous_answers", [])
        engine._failed_reason = data.get("failed_reason")
        if data.get("deliverable"):
            engine._deliverable = Deliverable.from_dict(data["deliverable"])
        return engine

    # ------------------------------------------------------------ expert call

    def _default_expert(
        self, expert: str, q: Dict[str, Any], request: RunRequest
    ) -> Dict[str, Any]:
        """The production expert caller: forge_subagent through the gateway."""
        if self.gateway is None:
            self.gateway = GatewayClient()
        if self.gateway._session_id is None:  # noqa: SLF001 — private by design
            self.gateway.connect()
        alias = alias_for(expert, self._aliases)
        task = forge_task_text(q, alias)
        forge_result = self.gateway.call_forge(
            task,
            agent_type=expert,
            effort=request.effort,
            max_turns=2,
            max_seconds=request.per_expert_timeout_s,
        )
        accepted = True
        acceptance = forge_result.get("acceptance_verdict") or {}
        if acceptance.get("approved") is False:
            accepted = False
        parsed = forge_result.get("result", forge_result.get("output", ""))
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except json.JSONDecodeError:
                parsed = {"rationale": parsed}
        if not isinstance(parsed, dict):
            parsed = {"rationale": str(parsed)}
        return {
            "verdict": parse_verdict(str(parsed.get("verdict", "CONDITIONAL"))),
            "rationale": str(parsed.get("rationale", "")),
            "revision_note": parsed.get("revision_note"),
            "accepted": accepted,
        }

    # ------------------------------------------------------------- execution

    def execute(self) -> Deliverable:
        """Run the panel to a deliverable. Idempotent for finished runs."""
        if self._deliverable is not None:
            return self._deliverable
        if self.state == "failed":
            return self._deliver_failed()

        max_rounds = self.request.max_rounds
        for round_no in range(self.current_round + 1, max_rounds + 1):
            verdict = self._run_round(round_no)
            if verdict == "converged":
                return self._deliver("converged")
            if verdict == "no_consensus":
                return self._deliver("no_consensus")
            if verdict == "failed":
                return self._deliver_failed()

        # Loop exhausted without a stop verdict (max_rounds hit with the
        # stop rule returning continue at round < 2 — max_rounds == 1 case).
        if self._last_feedback is not None:
            return self._deliver("no_consensus")
        return self._deliver_failed()

    def _run_round(self, round_no: int) -> str:
        """Run one round; returns continue | converged | no_consensus | failed."""
        self.current_round = round_no
        self.state = f"round{round_no}"
        self._persist()

        # Feedback for round >= 2 — built from the previous round's answers,
        # with the scrubber gate: identity in feedback fails the round.
        feedback: Optional[Dict[str, Any]] = None
        if round_no >= 2 and self._previous_answers:
            feedback, leaked = build_feedback(round_no, self._previous_answers, self._aliases)
            if leaked:
                self._fail(f"identity leak in feedback for round {round_no}: {sorted(set(leaked))}")
                return "failed"
            self._last_feedback = feedback

        answers = self._dispatch_round(round_no, feedback)
        answered = [a for a in answers if a.status == "answered"]
        if len(answered) < 2:
            self._fail(
                f"round {round_no} got fewer than 2 answered experts "
                f"({len(answered)}) — a 1-expert panel is not a panel"
            )
            return "failed"

        record = self._record_round(round_no, answers)
        self.rounds.append(record)
        self._previous_answers = [a.to_dict() for a in answers]
        self._persist()

        # Stability comes from the ROUND RECORD (computed against the
        # previous round's answers before _previous_answers was overwritten)
        # — comparing round N against itself is always "stable".
        counts = count_verdicts([a.to_dict() for a in answers])
        agg = overall_agreement([counts])
        return should_stop(
            round_no, agg, record.stability, self.request.threshold, self.request.max_rounds
        )

    def _dispatch_round(
        self, round_no: int, feedback: Optional[Dict[str, Any]]
    ) -> List[ExpertAnswer]:
        """Dispatch all experts concurrently; one retry per expert on
        failure/timeout; missing experts are recorded, not fabricated."""
        answers: List[ExpertAnswer] = []
        missing: List[str] = []
        with ThreadPoolExecutor(max_workers=len(self.request.experts)) as pool:
            futures: Dict[Future, str] = {}
            for expert in self.request.experts:
                q = self._questionnaire_for(round_no, feedback)
                futures[pool.submit(self.expert_callable, expert, q, self.request)] = expert

            for future, expert in futures.items():
                result = self._await_expert(future, expert)
                if result is None:
                    missing.append(expert)
                    continue
                answers.append(self._to_answer(round_no, expert, result))

        # One retry per missing expert (a fresh dispatch; the gateway client
        # re-handshakes on a lost session inside its own retry logic).
        for expert in list(missing):
            q = self._questionnaire_for(round_no, feedback)
            try:
                result = self.expert_callable(expert, q, self.request)
            except Exception:
                continue  # a crashed expert callable is a missing answer, not a panel crash
            if result is None:
                continue
            missing.remove(expert)
            answers.append(self._to_answer(round_no, expert, result))

        for expert in missing:
            answers.append(ExpertAnswer(round=round_no, expert=expert, status="missing"))
        return answers

    def _to_answer(self, round_no: int, expert: str, result: Dict[str, Any]) -> ExpertAnswer:
        if not result.get("accepted", True):
            return ExpertAnswer(round=round_no, expert=expert, status="refused")
        return ExpertAnswer(
            round=round_no,
            expert=expert,
            verdict=result.get("verdict", "CONDITIONAL"),
            rationale=result.get("rationale", ""),
            revision_note=result.get("revision_note"),
            status="answered",
        )

    def _await_expert(self, future: Future, expert: str) -> Optional[Dict[str, Any]]:
        timeout = self.request.per_expert_timeout_s + 10
        try:
            return future.result(timeout=timeout)
        except FutureTimeout:
            return None
        except Exception:
            # Any expert-callable crash is a missing answer, not a panel
            # crash: the caller gives the expert one retry, then records it
            # missing. (GatewayError/ForgeError/ProtocolError included.)
            return None

    def _questionnaire_for(
        self, round_no: int, feedback: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        from awdelphi.protocol import questionnaire

        return questionnaire(round_no, self.request, feedback)

    def _alias_encoded(self, answers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Map internal answers (keyed by expert name) to alias-encoded ones."""
        out: List[Dict[str, Any]] = []
        for a in answers:
            if a.get("status", "answered") != "answered":
                continue
            out.append(
                {
                    "alias": alias_for(a["expert"], self._aliases),
                    "verdict": a.get("verdict", "CONDITIONAL"),
                    "rationale": a.get("rationale", ""),
                }
            )
        return out

    def _record_round(self, round_no: int, answers: List[ExpertAnswer]) -> RoundRecord:
        alias_answers: List[Dict[str, Any]] = []
        for a in answers:
            if a.status != "answered":
                continue
            alias_answers.append(
                {
                    "alias": alias_for(a.expert, self._aliases),
                    "verdict": a.verdict,
                    "rationale": a.rationale,
                }
            )
        counts = count_verdicts([a.to_dict() for a in answers])
        stable = stability(self._previous_answers, [a.to_dict() for a in answers])
        return RoundRecord(
            round=round_no,
            answers=alias_answers,
            agreement=overall_agreement([counts]),
            stability=stable,
            feedback_given=self._last_feedback is not None and round_no > 1,
            refused=[a.expert for a in answers if a.status == "refused"],
            missing=[a.expert for a in answers if a.status == "missing"],
        )

    # ------------------------------------------------------------ outcomes

    def _fail(self, reason: str) -> None:
        self.state = "failed"
        self._failed_reason = reason
        self._persist()

    def cancel(self, reason: str = "cancelled by caller") -> Deliverable:
        if self._deliverable is None:
            self._fail(reason)
        return self._deliver_failed()

    def _deliver(self, outcome: str) -> Deliverable:
        counts = count_verdicts(self._previous_answers)
        modal = modal_verdict(counts)
        agg = overall_agreement([counts]) if counts else 0.0
        final_answers = self._alias_encoded(self._previous_answers)
        d = Deliverable(
            run_id=self.run_id,
            question=self.request.question,
            context=self.request.context,
            mode=self.request.mode,
            outcome=outcome,
            # A verdict only exists when the panel CONVERGED — a no_consensus
            # run ships the counts and dissent map, not a fabricated answer.
            consensus_verdict=(
                consensus_verdict_for(modal, self.request.mode)
                if outcome == "converged"
                else None
            ),
            confidence=agg,
            supporting_rationale=supporting_rationale_for(final_answers, modal),
            final_counts=counts,
            round_trace=[r.to_dict() for r in self.rounds],
            dissent_map=dissent_map(final_answers, modal),
            stopped_after_rounds=self.current_round,
            threshold=self.request.threshold,
            max_rounds=self.request.max_rounds,
        )
        self._deliverable = d
        self.state = "done"
        self._persist()
        return d

    def _deliver_failed(self) -> Deliverable:
        d = Deliverable(
            run_id=self.run_id,
            question=self.request.question,
            context=self.request.context,
            mode=self.request.mode,
            outcome="failed",
            consensus_verdict=None,
            confidence=0.0,
            supporting_rationale="",
            final_counts={},
            round_trace=[r.to_dict() for r in self.rounds],
            dissent_map=[],
            stopped_after_rounds=self.current_round,
            threshold=self.request.threshold,
            max_rounds=self.request.max_rounds,
            failed_reason=self._failed_reason,
        )
        self._deliverable = d
        self.state = "done"
        self._persist()
        return d

    def arena_import_payload(self) -> Dict[str, Any]:
        """The deliverable plus the leaderboard handoff.

        The deliverable is anonymized by design; the arena's ELO needs real
        names, so this handoff adds participants / consensus_side /
        dissent_side / abstains derived from the final internal answers.
        Only meaningful after execute() produced a deliverable.
        """
        d = self._deliverable.to_dict() if self._deliverable else None
        if d is None:
            return {}
        counts = count_verdicts(self._previous_answers)
        modal = modal_verdict(counts)
        consensus: List[str] = []
        dissent: List[str] = []
        abstains: List[str] = []
        for a in self._previous_answers:
            if a.get("status") == "refused":
                abstains.append(a["expert"])
                continue
            if a.get("verdict", "CONDITIONAL") == modal:
                consensus.append(a["expert"])
            else:
                dissent.append(a["expert"])
        return {
            **d,
            "participants": list(self.request.experts),
            "consensus_side": consensus,
            "dissent_side": dissent,
            "abstains": abstains,
        }

    # -------------------------------------------------------------- queries

    def status(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "state": self.state,
            "current_round": self.current_round,
            "outcome": self._deliverable.outcome if self._deliverable else None,
            "question": self.request.question,
            "experts": list(self.request.experts),
        }

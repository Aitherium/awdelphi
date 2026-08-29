"""Engine end-to-end with scripted fake experts — no gateway, deterministic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from awdelphi.engine import DelphiEngine
from awdelphi.gateway import GatewayError
from awdelphi.protocol import ProtocolError, RunRequest


def scripted(script):
    """Fake expert: script['<expert>:<round>'] → answer dict; default abstain."""

    def _call(expert, q, request):
        return script.get(
            f"{expert}:{q['round']}",
            {"verdict": "CONDITIONAL", "rationale": "default", "accepted": False},
        )

    return _call


def run_panel(question, script, experts=("demiurge", "athena"), **kwargs):
    engine = DelphiEngine(
        RunRequest(question=question, experts=list(experts), **kwargs),
        runs_dir=Path(
            kwargs.pop("runs_dir", None)
            or (Path(__import__("tempfile").mkdtemp()) / "runs")
        ),
        expert_callable=scripted(script),
    )
    return engine.execute().to_dict()


@pytest.fixture()
def tmp_runs(tmp_path):
    return tmp_path / "runs"


class TestPanelLifecycle:
    def test_split_then_converge(self, tmp_runs):
        d = run_panel(
            "Q",
            {
                "demiurge:1": {"verdict": "YES", "rationale": "a", "accepted": True},
                "athena:1": {"verdict": "NO", "rationale": "b", "accepted": True},
                "demiurge:2": {"verdict": "YES", "rationale": "a", "accepted": True},
                "athena:2": {"verdict": "YES", "rationale": "b", "accepted": True},
                "demiurge:3": {"verdict": "YES", "rationale": "a", "accepted": True},
                "athena:3": {"verdict": "YES", "rationale": "b", "accepted": True},
            },
        )
        assert d["outcome"] == "converged"
        assert d["consensus_verdict"] == "YES"
        assert d["stopped_after_rounds"] == 3
        assert len(d["round_trace"]) == 3

    def test_converges_early_when_stable_from_round2(self, tmp_runs):
        # Round 1: unanimous YES (agreement 1.0, but round 1 never converges).
        # Round 2: unanimous and unchanged → converged at round 2.
        d = run_panel(
            "Q",
            {
                "demiurge:1": {"verdict": "YES", "rationale": "a", "accepted": True},
                "athena:1": {"verdict": "YES", "rationale": "b", "accepted": True},
                "demiurge:2": {"verdict": "YES", "rationale": "a", "accepted": True},
                "athena:2": {"verdict": "YES", "rationale": "b", "accepted": True},
            },
        )
        assert d["outcome"] == "converged"
        assert d["stopped_after_rounds"] == 2

    def test_split_below_threshold_continues(self, tmp_runs):
        # A stable 2-way split is agreement 0.5 — below threshold — so the
        # panel keeps going even though nobody moved.
        d = run_panel(
            "Q",
            {
                "demiurge:1": {"verdict": "YES", "rationale": "a", "accepted": True},
                "athena:1": {"verdict": "NO", "rationale": "b", "accepted": True},
                "demiurge:2": {"verdict": "YES", "rationale": "a", "accepted": True},
                "athena:2": {"verdict": "NO", "rationale": "still no", "accepted": True},
                "demiurge:3": {"verdict": "YES", "rationale": "a", "accepted": True},
                "athena:3": {"verdict": "NO", "rationale": "still no", "accepted": True},
            },
            max_rounds=3,
        )
        assert d["outcome"] == "no_consensus"
        assert d["stopped_after_rounds"] == 3

    def test_diverge_forever_hits_max_rounds_no_consensus(self, tmp_runs):
        # Experts swap positions every round: agreement may be 1.0 but the
        # panel never stabilizes → no_consensus at max_rounds, honest.
        d = run_panel(
            "Q",
            {
                "demiurge:1": {"verdict": "YES", "rationale": "a", "accepted": True},
                "athena:1": {"verdict": "NO", "rationale": "b", "accepted": True},
                "demiurge:2": {"verdict": "NO", "rationale": "a", "accepted": True},
                "athena:2": {"verdict": "YES", "rationale": "b", "accepted": True},
                "demiurge:3": {"verdict": "YES", "rationale": "a", "accepted": True},
                "athena:3": {"verdict": "NO", "rationale": "b", "accepted": True},
            },
            max_rounds=3,
        )
        assert d["outcome"] == "no_consensus"
        assert d["consensus_verdict"] is None
        assert d["stopped_after_rounds"] == 3
        verdicts = {g["verdict"] for g in d["dissent_map"]}
        assert verdicts == {"YES", "NO"}
        # The dissenting rationale is preserved, aliased:
        no_group = next(g for g in d["dissent_map"] if g["verdict"] == "NO")
        assert no_group["aliases"] == ["Expert A"]

    def test_round1_feedback_absent(self, tmp_runs):
        """Round-1 questionnaire must carry no feedback — the independence rule."""
        seen = {}

        def spy(expert, q, request):
            seen[q["round"]] = q
            return {"verdict": "YES", "rationale": "", "accepted": True}

        engine = DelphiEngine(
            RunRequest(question="Q", experts=["demiurge", "athena"], max_rounds=2),
            runs_dir=tmp_runs,
            expert_callable=spy,
        )
        engine.execute()
        assert "feedback" not in seen[1]
        assert "feedback" in seen[2]

    def test_round2_feedback_is_anonymized(self, tmp_runs):
        """Round-2 questionnaire must carry aliases, never expert names."""
        seen = {}

        def spy(expert, q, request):
            seen[q["round"]] = q
            return {"verdict": "YES", "rationale": "", "accepted": True}

        engine = DelphiEngine(
            RunRequest(question="Q", experts=["demiurge", "athena"], max_rounds=2),
            runs_dir=tmp_runs,
            expert_callable=spy,
        )
        engine.execute()
        fb = json.dumps(seen[2])
        assert "Expert" in fb
        assert "demiurge" not in fb
        assert "athena" not in fb

    def test_review_mode_verdict_mapping(self, tmp_runs):
        d = run_panel(
            "Q",
            {
                "demiurge:1": {
                    "verdict": "CONDITIONAL",
                    "rationale": "needs work",
                    "accepted": True,
                },
                "athena:1": {"verdict": "CONDITIONAL", "rationale": "needs work", "accepted": True},
                "demiurge:2": {
                    "verdict": "CONDITIONAL",
                    "rationale": "needs work",
                    "accepted": True,
                },
                "athena:2": {"verdict": "CONDITIONAL", "rationale": "needs work", "accepted": True},
            },
            mode="review",
        )
        assert d["outcome"] == "converged"
        assert d["consensus_verdict"] == "approve_with_conditions"


class TestRefusalAndMissing:
    def test_refusal_abstains_and_is_excluded(self, tmp_runs):
        # 3 experts, athena abstains every round: the remaining 2 carry the
        # panel, and the abstention is excluded from the denominator.
        d = run_panel(
            "Q",
            {
                "demiurge:1": {"verdict": "YES", "rationale": "a", "accepted": True},
                "athena:1": {"verdict": "NO", "rationale": "b", "accepted": False},
                "apollo:1": {"verdict": "YES", "rationale": "c", "accepted": True},
                "demiurge:2": {"verdict": "YES", "rationale": "a", "accepted": True},
                "athena:2": {"verdict": "NO", "rationale": "b", "accepted": False},
                "apollo:2": {"verdict": "YES", "rationale": "c", "accepted": True},
            },
            experts=("demiurge", "athena", "apollo"),
        )
        assert d["outcome"] == "converged"
        assert d["confidence"] == 1.0  # 2/2 answered, abstain excluded
        assert d["round_trace"][0]["refused"] == ["athena"]

    def test_fewer_than_two_answers_fails(self, tmp_runs):
        d = run_panel(
            "Q",
            {
                "demiurge:1": {"verdict": "YES", "rationale": "a", "accepted": True},
                "athena:1": {"verdict": "NO", "rationale": "b", "accepted": False},
            },
        )
        assert d["outcome"] == "failed"
        assert "1-expert panel" in (d["failed_reason"] or "")

    def test_missing_expert_recorded_not_fabricated(self, tmp_runs):
        """An expert that never answers is missing — never counted as a vote."""

        def flaky(expert, q, request):
            raise GatewayError("gateway unreachable")

        engine = DelphiEngine(
            RunRequest(question="Q", experts=["demiurge", "athena"], max_rounds=2),
            runs_dir=tmp_runs,
            expert_callable=flaky,
        )
        d = engine.execute().to_dict()
        assert d["outcome"] == "failed"
        assert d["failed_reason"] and "fewer than 2" in d["failed_reason"]

    def test_deliverable_has_full_trace(self, tmp_runs):
        d = run_panel(
            "Q",
            {
                "demiurge:1": {"verdict": "YES", "rationale": "a", "accepted": True},
                "athena:1": {"verdict": "NO", "rationale": "b", "accepted": True},
                "demiurge:2": {"verdict": "YES", "rationale": "a", "accepted": True},
                "athena:2": {"verdict": "NO", "rationale": "b", "accepted": True},
            },
        )
        for r in d["round_trace"]:
            assert "round" in r
            assert "agreement" in r
            assert "stability" in r
            for answer in r["answers"]:
                assert "alias" in answer
                assert "expert" not in answer  # trace is alias-encoded


class TestArenaHandoff:
    def test_arena_payload_carries_identity_but_deliverable_stays_clean(self, tmp_path):
        """arena_import_payload() adds the leaderboard handoff (real names)
        to the anonymized deliverable — the trace itself never carries them."""
        runs_dir = tmp_path / "runs"
        engine = DelphiEngine(
            RunRequest(question="Q", experts=["demiurge", "athena"], max_rounds=3),
            runs_dir=runs_dir,
            expert_callable=scripted(
                {
                    "demiurge:1": {"verdict": "YES", "rationale": "a", "accepted": True},
                    "athena:1": {"verdict": "NO", "rationale": "b", "accepted": True},
                    "demiurge:2": {"verdict": "YES", "rationale": "a", "accepted": True},
                    "athena:2": {"verdict": "YES", "rationale": "b", "accepted": True},
                    "demiurge:3": {"verdict": "YES", "rationale": "a", "accepted": True},
                    "athena:3": {"verdict": "YES", "rationale": "b", "accepted": True},
                }
            ),
        )
        d = engine.execute().to_dict()
        assert d["outcome"] == "converged"
        assert "demiurge" not in str(d["round_trace"])  # trace is alias-encoded

        payload = engine.arena_import_payload()
        assert payload["participants"] == ["demiurge", "athena"]
        # athena revised to YES in round 2, so the final consensus side is both:
        assert payload["consensus_side"] == ["demiurge", "athena"]
        assert payload["dissent_side"] == []
        assert payload["abstains"] == []
        assert payload["outcome"] == "converged"

    def test_arena_payload_split_panel(self, tmp_path):
        runs_dir = tmp_path / "runs"
        engine = DelphiEngine(
            RunRequest(question="Q", experts=["demiurge", "athena"], max_rounds=2),
            runs_dir=runs_dir,
            expert_callable=scripted(
                {
                    "demiurge:1": {"verdict": "YES", "rationale": "a", "accepted": True},
                    "athena:1": {"verdict": "NO", "rationale": "b", "accepted": True},
                    "demiurge:2": {"verdict": "YES", "rationale": "a", "accepted": True},
                    "athena:2": {"verdict": "NO", "rationale": "b", "accepted": True},
                }
            ),
        )
        d = engine.execute().to_dict()
        assert d["outcome"] == "no_consensus"
        payload = engine.arena_import_payload()
        assert payload["consensus_side"] == ["demiurge"]  # modal YES
        assert payload["dissent_side"] == ["athena"]
        # No consensus → the arena grants no ELO, but the handoff still ships
        # the split so the archive is complete:
        assert payload["outcome"] == "no_consensus"


class TestRequestValidation:
    def test_panel_of_one_refused(self):
        with pytest.raises(ProtocolError):
            RunRequest(question="Q", experts=["demiurge"])

    def test_empty_question_refused(self):
        with pytest.raises(ProtocolError):
            RunRequest(question="", experts=["demiurge", "athena"])

    def test_duplicates_deduped(self):
        req = RunRequest(question="Q", experts=["demiurge", "demiurge", "athena"])
        assert req.experts == ["demiurge", "athena"]

    def test_round_bounds(self):
        with pytest.raises(ProtocolError):
            RunRequest(question="Q", experts=["a", "b"], max_rounds=7)
        with pytest.raises(ProtocolError):
            RunRequest(question="Q", experts=["a", "b"], max_rounds=0)

    def test_threshold_bounds(self):
        with pytest.raises(ProtocolError):
            RunRequest(question="Q", experts=["a", "b"], threshold=1.5)

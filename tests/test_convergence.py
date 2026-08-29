"""Convergence math — the stop rule is the method, so it gets the strictest tests."""

from __future__ import annotations

import pytest
from awdelphi.convergence import (
    agreement,
    consensus_verdict_for,
    count_verdicts,
    dissent_map,
    modal_verdict,
    overall_agreement,
    should_stop,
    stability,
    supporting_rationale_for,
)


def _ans(expert, verdict, status="answered", rationale="", alias=None):
    d = {"expert": expert, "verdict": verdict, "status": status, "rationale": rationale}
    if alias:
        d["alias"] = alias
    return d


class TestAgreement:
    def test_modal_share(self):
        counts = {"YES": 2, "NO": 1, "CONDITIONAL": 0}
        assert agreement(counts) == pytest.approx(2 / 3)

    def test_zero_answers_is_zero_agreement(self):
        assert agreement({"YES": 0, "NO": 0, "CONDITIONAL": 0}) == 0.0

    def test_overall_is_mean(self):
        assert overall_agreement([{"YES": 3, "NO": 0, "CONDITIONAL": 0}]) == 1.0
        assert overall_agreement([]) == 0.0

    def test_modal_tie_breaks_by_order(self):
        assert modal_verdict({"YES": 2, "NO": 2, "CONDITIONAL": 0}) == "YES"
        assert modal_verdict({"YES": 1, "NO": 2, "CONDITIONAL": 2}) == "NO"

    def test_refused_and_missing_are_not_votes(self):
        answers = [
            _ans("demiurge", "YES"),
            _ans("athena", "NO"),
            _ans("apollo", "YES", status="refused"),
            _ans("lyra", "YES", status="missing"),
        ]
        counts = count_verdicts(answers)
        assert counts == {"YES": 1, "NO": 1, "CONDITIONAL": 0}


class TestStability:
    def test_no_one_moved(self):
        prev = [_ans("demiurge", "YES"), _ans("athena", "NO")]
        curr = [_ans("demiurge", "YES"), _ans("athena", "NO")]
        assert stability(prev, curr) is True

    def test_someone_moved(self):
        prev = [_ans("demiurge", "YES"), _ans("athena", "NO")]
        curr = [_ans("demiurge", "YES"), _ans("athena", "YES")]
        assert stability(prev, curr) is False

    def test_missing_in_one_round_does_not_block(self):
        prev = [_ans("demiurge", "YES"), _ans("athena", "NO")]
        curr = [_ans("demiurge", "YES")]
        assert stability(prev, curr) is True

    def test_no_common_participants_is_not_stable(self):
        prev = [_ans("demiurge", "YES")]
        curr = [_ans("athena", "NO")]
        assert stability(prev, curr) is False


class TestShouldStop:
    def test_round1_never_converges_even_at_agreement_1(self):
        assert should_stop(1, 1.0, True, 0.7, 3) == "continue"

    def test_max_rounds_1_is_honest_no_consensus(self):
        assert should_stop(1, 1.0, True, 0.7, 1) == "no_consensus"

    def test_converged_requires_threshold_and_stability(self):
        assert should_stop(2, 0.8, True, 0.7, 3) == "converged"
        assert should_stop(2, 0.8, False, 0.7, 3) == "continue"  # still moving
        assert should_stop(2, 0.6, True, 0.7, 3) == "continue"  # below threshold

    def test_max_rounds_forces_no_consensus(self):
        assert should_stop(3, 0.6, True, 0.7, 3) == "no_consensus"
        # Converged only if BOTH hold even on the last round:
        assert should_stop(3, 0.8, False, 0.7, 3) == "no_consensus"

    def test_threshold_boundary(self):
        assert should_stop(2, 0.7, True, 0.7, 3) == "converged"
        assert should_stop(2, 0.69, True, 0.7, 3) == "continue"


class TestVerdictMapping:
    def test_review_mode_maps_to_arena_shape(self):
        assert consensus_verdict_for("YES", "review") == "approve"
        assert consensus_verdict_for("CONDITIONAL", "review") == "approve_with_conditions"
        assert consensus_verdict_for("NO", "review") == "reject"

    def test_decision_mode_keeps_verdicts(self):
        assert consensus_verdict_for("YES", "decision") == "YES"


class TestDissentMap:
    def test_modal_group_first(self):
        answers = [
            _ans("demiurge", "YES", alias="Expert B", rationale="good"),
            _ans("athena", "NO", alias="Expert A", rationale="bad"),
        ]
        groups = dissent_map(answers, "YES")
        assert groups[0]["verdict"] == "YES"
        assert groups[1]["verdict"] == "NO"
        assert groups[1]["aliases"] == ["Expert A"]
        assert groups[1]["rationales"][0]["alias"] == "Expert A"

    def test_dissent_rationales_survive(self):
        answers = [_ans("athena", "NO", alias="Expert A", rationale="the risk is real")]
        groups = dissent_map(answers, "YES")
        assert groups[0]["rationales"] == [{"alias": "Expert A", "rationale": "the risk is real"}]

    def test_supporting_rationale_joins_modal_group(self):
        answers = [
            _ans("demiurge", "YES", alias="Expert B", rationale="one."),
            _ans("athena", "NO", alias="Expert A", rationale="ignored."),
        ]
        assert supporting_rationale_for(answers, "YES") == "one."

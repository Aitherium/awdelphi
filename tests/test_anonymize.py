"""Anonymization — identity must never ship; the scrubber is fail-closed."""

from __future__ import annotations

from awdelphi.anonymize import ROSTER_NAMES, alias_map, build_feedback, scrub


class TestAliasMap:
    def test_stable_sorted_aliases(self):
        aliases = alias_map(["athena", "demiurge"])
        assert aliases == {"athena": "Expert A", "demiurge": "Expert B"}

    def test_duplicate_names_collapse(self):
        assert alias_map(["demiurge", "demiurge", "athena"]) == {
            "athena": "Expert A",
            "demiurge": "Expert B",
        }


class TestScrub:
    def test_identity_fields_dropped_silently(self):
        payload = {
            "expert": "demiurge",
            "sender": "athena",
            "rationale": "the design is fine",
        }
        scrubbed, leaked = scrub(payload, ["demiurge", "athena"])
        assert "expert" not in scrubbed
        assert "sender" not in scrubbed
        assert leaked == []  # dropped fields are not leaks

    def test_name_in_surviving_text_is_replaced_and_reported(self):
        scrubbed, leaked = scrub("per athena the design is sound", ["athena"])
        assert "athena" not in scrubbed
        assert "«expert»" in scrubbed
        assert leaked == ["athena"]

    def test_leaks_propagate_through_nested_payload(self):
        payload = {
            "per_question": [
                {"anonymized_rationales": [{"alias": "Expert A", "rationale": "as demiurge said"}]}
            ]
        }
        scrubbed, leaked = scrub(payload, ["demiurge"])
        assert "demiurge" not in str(scrubbed)
        assert leaked == ["demiurge"]

    def test_nested_identity_field_dropped(self):
        payload = {"a": [{"b": {"agent": "hydra", "text": "ok"}}]}
        scrubbed, leaked = scrub(payload, ["hydra"])
        assert "agent" not in str(scrubbed)
        assert leaked == []

    def test_word_boundary_no_false_positives(self):
        scrubbed, leaked = scrub("athenaeum", ["athena"])
        assert scrubbed == "athenaeum"
        assert leaked == []

    def test_case_insensitive(self):
        _, leaked = scrub("per ATHENA", ["athena"])
        assert leaked == ["ATHENA"]


class TestBuildFeedback:
    def test_feedback_carries_counts_and_aliases_only(self):
        aliases = alias_map(["demiurge", "athena"])
        fb, leaked = build_feedback(
            2,
            [
                {
                    "expert": "demiurge",
                    "verdict": "YES",
                    "rationale": "solid",
                    "status": "answered",
                },
                {"expert": "athena", "verdict": "NO", "rationale": "risky", "status": "answered"},
            ],
            aliases,
        )
        assert leaked == []
        q = fb["per_question"][0]
        assert q["counts"] == {"YES": 1, "NO": 1, "CONDITIONAL": 0}
        assert q["modal"] == "YES"
        aliases_in_fb = {r["alias"] for r in q["anonymized_rationales"]}
        assert aliases_in_fb == {"Expert A", "Expert B"}

    def test_planted_name_is_scrubbed_before_payload_assembly(self):
        aliases = alias_map(["demiurge", "athena"])
        fb, leaked = build_feedback(
            2,
            [
                {
                    "expert": "demiurge",
                    "verdict": "YES",
                    "rationale": "solid",
                    "status": "answered",
                },
                {
                    "expert": "athena",
                    "verdict": "NO",
                    "rationale": "as athena noted, risky",
                    "status": "answered",
                },
            ],
            aliases,
        )
        assert leaked == []  # caught at the rationale level
        assert "athena" not in str(fb)
        assert "«expert»" in str(fb)

    def test_refused_and_missing_excluded_from_feedback(self):
        aliases = alias_map(["demiurge", "athena"])
        fb, leaked = build_feedback(
            2,
            [
                {
                    "expert": "demiurge",
                    "verdict": "YES",
                    "rationale": "solid",
                    "status": "answered",
                },
                {"expert": "athena", "verdict": "NO", "rationale": "risky", "status": "refused"},
                {"expert": "apollo", "verdict": "NO", "rationale": "gone", "status": "missing"},
            ],
            aliases,
        )
        assert leaked == []
        q = fb["per_question"][0]
        assert q["counts"] == {"YES": 1, "NO": 0, "CONDITIONAL": 0}
        assert q["answered"] == 1

    def test_roster_names_are_all_scrubbed(self):
        # Every name in the roster must be caught when it appears verbatim.
        for name in ROSTER_NAMES:
            scrubbed, leaked = scrub(f"the {name} perspective", [name])
            assert name not in scrubbed, name
            assert leaked == [name], name

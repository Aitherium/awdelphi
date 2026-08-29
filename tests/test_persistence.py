"""Persistence and resume — the run JSON is the shared state."""

from __future__ import annotations

import pytest
from awdelphi.engine import DelphiEngine, RunNotFoundError
from awdelphi.protocol import RunRequest


def scripted(script):
    def _call(expert, q, request):
        return script.get(
            f"{expert}:{q['round']}",
            {"verdict": "YES", "rationale": "default", "accepted": True},
        )

    return _call


class TestPersistence:
    def test_written_after_execution(self, tmp_path):
        runs_dir = tmp_path / "runs"
        engine = DelphiEngine(
            RunRequest(question="Q", experts=["demiurge", "athena"], max_rounds=2),
            runs_dir=runs_dir,
            expert_callable=scripted({}),
        )
        engine.execute()
        assert (runs_dir / f"{engine.run_id}.json").exists()

    def test_corrupt_json_is_clear_error(self, tmp_path):
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        (runs_dir / "dead.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(RunNotFoundError, match="corrupt"):
            DelphiEngine.from_run("dead", runs_dir=runs_dir)

    def test_missing_run_is_clear_error(self, tmp_path):
        with pytest.raises(RunNotFoundError):
            DelphiEngine.from_run("nope", runs_dir=tmp_path / "runs")

    def test_cancel_transitions_to_failed(self, tmp_path):
        runs_dir = tmp_path / "runs"
        engine = DelphiEngine(
            RunRequest(question="Q", experts=["demiurge", "athena"], max_rounds=2),
            runs_dir=runs_dir,
            expert_callable=scripted({}),
        )
        d = engine.cancel("owner said stop")
        assert d.outcome == "failed"
        assert d.failed_reason == "owner said stop"
        loaded = DelphiEngine.from_run(engine.run_id, runs_dir=runs_dir)
        assert loaded.status()["state"] == "done"

    def test_finished_run_reexecutes_to_same_deliverable(self, tmp_path):
        runs_dir = tmp_path / "runs"
        engine = DelphiEngine(
            RunRequest(question="Q", experts=["demiurge", "athena"], max_rounds=2),
            runs_dir=runs_dir,
            expert_callable=scripted({}),
        )
        d1 = engine.execute()
        d2 = engine.execute()  # idempotent
        assert d1.to_dict() == d2.to_dict()


class TestResume:
    def test_resume_redispatch_only_missing_experts(self, tmp_path):
        """A crashed mid-round run retries only the crashed expert."""
        runs_dir = tmp_path / "runs"
        calls: list[tuple[str, int]] = []
        script = {
            "demiurge:1": {"verdict": "YES", "rationale": "a", "accepted": True},
            "athena:1": {"verdict": "YES", "rationale": "b", "accepted": True},
            "demiurge:2": {"verdict": "YES", "rationale": "a", "accepted": True},
            "athena:2": {"verdict": "YES", "rationale": "b", "accepted": True},
        }
        failed_once = {"done": False}

        def flaky(expert, q, request):
            calls.append((expert, q["round"]))
            if q["round"] == 2 and expert == "demiurge" and not failed_once["done"]:
                failed_once["done"] = True
                raise RuntimeError("crashed mid-round")  # dies exactly once
            return script[f"{expert}:{q['round']}"]

        engine = DelphiEngine(
            RunRequest(question="Q", experts=["demiurge", "athena"], max_rounds=2),
            runs_dir=runs_dir,
            expert_callable=flaky,
        )
        d = engine.execute().to_dict()
        assert d["outcome"] == "converged"
        # demiurge round 2 was attempted twice (first crashed, retry succeeded);
        # athena round 2 exactly once — the retry asked only who was missing.
        assert calls.count(("demiurge", 2)) == 2
        assert calls.count(("athena", 2)) == 1

    def test_resume_from_snapshot_matches_original(self, tmp_path):
        runs_dir = tmp_path / "runs"
        engine = DelphiEngine(
            RunRequest(question="Q", experts=["demiurge", "athena"], max_rounds=2),
            runs_dir=runs_dir,
            expert_callable=scripted({}),
        )
        d1 = engine.execute().to_dict()
        resumed = DelphiEngine.from_run(engine.run_id, runs_dir=runs_dir)
        assert resumed.status()["state"] == "done"
        assert resumed._deliverable.to_dict() == d1  # noqa: SLF001

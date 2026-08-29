"""CLI behavior — exit codes, fail-loudly, offline subcommands."""

from __future__ import annotations

import json

from awdelphi.cli import main

DEAD_GATEWAY = "http://127.0.0.1:1/mcp"


def test_run_with_dead_gateway_exits_1_with_exact_message(tmp_path, capsys):
    code = main(
        [
            "run",
            "Q",
            "--gateway",
            DEAD_GATEWAY,
            "--runs-dir",
            str(tmp_path),
        ]
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "no rounds were run" in err


def test_run_with_dead_gateway_persists_zero_rounds(tmp_path):
    runs_dir = tmp_path / "runs"
    main(["run", "Q", "--gateway", DEAD_GATEWAY, "--runs-dir", str(runs_dir)])
    assert not runs_dir.exists() or not list(runs_dir.glob("*.json"))


def test_panel_of_one_exits_2(tmp_path, capsys):
    code = main(["run", "Q", "--experts", "demiurge", "--runs-dir", str(tmp_path)])
    assert code == 2
    assert "at least two" in capsys.readouterr().err


def test_invalid_threshold_exits_2(tmp_path):
    code = main(
        ["run", "Q", "--experts", "a,b", "--threshold", "9", "--runs-dir", str(tmp_path)]
    )
    assert code == 2


def test_status_missing_run_exits_1(tmp_path, capsys):
    code = main(["status", "nope", "--runs-dir", str(tmp_path)])
    assert code == 1
    assert "no such run" in capsys.readouterr().err


def test_list_empty_is_zero_and_json(tmp_path, capsys):
    code = main(["list", "--json", "--runs-dir", str(tmp_path)])
    assert code == 0
    assert json.loads(capsys.readouterr().out) == []


def test_show_offline_run_json(tmp_path, capsys):
    runs_dir = tmp_path / "runs"
    # Seed a run by executing an engine directly (no CLI gateway needed).
    from awdelphi.engine import DelphiEngine
    from awdelphi.protocol import RunRequest

    engine = DelphiEngine(
        RunRequest(question="Q", experts=["demiurge", "athena"], max_rounds=2),
        runs_dir=runs_dir,
        expert_callable=lambda e, q, r: {"verdict": "YES", "rationale": "x", "accepted": True},
    )
    engine.execute()

    code = main(["show", engine.run_id, "--json", "--runs-dir", str(runs_dir)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["run_id"] == engine.run_id
    assert out["request"]["question"] == "Q"


def test_self_test_exits_0():
    assert main(["self-test"]) == 0

# awdelphi for agents

Read this if you are an agent (or a human) editing this package. Short on
purpose: the commands, the traps that cost a session, and where the rest lives.
Nothing here is read at runtime — it is for you.

## What this is

PyPI distribution **`awdelphi`** (version in `pyproject.toml`), import package
`awdelphi`, Python >= 3.10. Anonymous multi-round expert panels — a converged
answer with a trace. The Delphi method, run by your own models.

This repository is a **synced mirror** of the AitherOS monorepo (lane
`.github/workflows/sync-awdelphi.yml`). Hand edits made here are overwritten on
the next sync — change the source and let the lane publish.

## Build, test, verify

```bash
python -m pytest tests -q        # the suite: 68 tests, green at v0.1.0
pip install -e .                 # editable install for developing against it
```

The suite was run from a source checkout with no prior install. The publish
lane (`publish-brick.yml`) additionally builds the wheel, installs it and
imports it — a tree that tests green can still ship a broken wheel.

## Rules that keep this useful

- **Anonymity is the method, and it is tested.** `test_anonymize.py` exists
  because a panel where experts can identify each other converges on
  seniority, not on the answer — any new field that carries identity
  (model name, endpoint, style tells) lands with its anonymization case.
- **Convergence is tested deterministically.** `test_engine_fake_experts.py`
  drives the loop with scripted experts and `test_convergence.py` pins when
  the panel is declared converged — a round limit and a consensus are
  different ends, and the trace must say which one you got.
- **The trace is the deliverable.** `test_persistence.py` pins that a
  finished panel can be re-read round by round; a converged number without
  the path to it is an opinion with formatting.
- **The registry drives the public surface.** This repo's README header,
  `llms.txt` and `aither-manifest.json` are generated from the ecosystem
  registry (one yaml in the AitherOS monorepo) and rewritten on every sync.
  Change the registry; do not hand-edit the generated blocks.

## Read next

- `llms.txt` — the install/use card written for an agent to execute
- `README.md` — the human front door
- `docs/` — the generated docs site source

"""awdelphi — Aither World Delphi.

Anonymous multi-round expert panels. A panel runs a question past a roster of
expert agents in rounds: round 1 answers are independent, the monitor feeds
back the group's anonymized view, experts revise, and the panel stops at
convergence (agreement + stability) or max rounds — delivering a verdict with
a full round trace, or an honest no-consensus with a dissent map.

    from awdelphi import DelphiEngine, RunRequest

    run = DelphiEngine(RunRequest(question="...", experts=["demiurge", "athena", "hydra"]))
    deliverable = run.execute()

See `docs/protocol.md` for the state machine, schemas, and the anonymity
rules, and `engine.py` for why the monitor never forces a consensus.
"""

from __future__ import annotations

from awdelphi.engine import DelphiEngine, RunNotFoundError
from awdelphi.protocol import Deliverable, RunRequest

__version__ = "0.1.0"

__all__ = ["DelphiEngine", "RunRequest", "Deliverable", "RunNotFoundError"]

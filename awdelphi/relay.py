"""Optional relay posting — soft-failing by design.

A panel's deliverable can be broadcast to an AitherRelay-shaped channel so a
human (or another session) can read the verdict and trace. This is
deliberately NOT part of the protocol: a relay refusal (auth, missing
channel) must never fail or block a finished panel, so every failure here
prints a warning and returns False.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

import httpx

DEFAULT_RELAY_URL = os.environ.get("AWRELAY_URL", "http://127.0.0.1:8205")


def _token() -> str:
    token = os.environ.get("AWRELAY_TOKEN")
    if token:
        return token
    bearer = os.environ.get("AWDELPHI_BEARER_FILE", str(Path.home() / ".aither" / "session-bearer"))
    try:
        return Path(bearer).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def post_deliverable(
    deliverable: Dict[str, Any],
    channel: str,
    url: Optional[str] = None,
    token: Optional[str] = None,
) -> bool:
    """Post a one-line verdict + trace pointer to a relay channel.

    Returns True on a confirmed post, False otherwise — never raises.
    """
    try:
        base = (url or DEFAULT_RELAY_URL).rstrip("/")
        headers = {"Authorization": f"Bearer {token or _token()}"}
        text = (
            f"awdelphi panel {deliverable.get('run_id', '?')} → "
            f"{deliverable.get('outcome')} "
            f"verdict={deliverable.get('consensus_verdict')} "
            f"confidence={deliverable.get('confidence', 0):.2f} "
            f"rounds={deliverable.get('stopped_after_rounds')}"
        )
        payload = {
            "kind": "finding",
            "text": text,
            "payload": {
                "run_id": deliverable.get("run_id"),
                "question": deliverable.get("question"),
                "outcome": deliverable.get("outcome"),
                "consensus_verdict": deliverable.get("consensus_verdict"),
                "confidence": deliverable.get("confidence"),
                "stopped_after_rounds": deliverable.get("stopped_after_rounds"),
            },
        }
        resp = httpx.post(
            f"{base}/v1/channels/{quote(channel, safe='')}/messages",
            json=payload,
            headers=headers,
            timeout=20.0,
        )
        if resp.status_code >= 400:
            print(
                f"awdelphi: relay post skipped ({resp.status_code} on {channel})",
                file=sys.stderr,
            )
            return False
        return True
    except httpx.HTTPError as exc:
        print(f"awdelphi: relay post skipped ({exc})", file=sys.stderr)
        return False

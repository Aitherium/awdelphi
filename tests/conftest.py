"""Every awdelphi test gets its OWN bearer file, never the developer's.

`GatewayClient` falls back to `DEFAULT_BEARER_FILE` (~/.aither/session-bearer)
when no path is given, and the tests gave none. Two consequences, both bad:

1. The verdict depended on the machine. Measured 2026-09-10: the suite is
   "1 failed, 67 passed" on a workstation that has a bearer and
   "6 failed, 62 passed" on the publish runner, which does not -- `_bearer()`
   raises before any assertion in the test can matter, so five tests about
   transport behaviour were really testing whether a credential file existed.
   That failure blocked awdelphi 0.1.0 from PyPI on every 6-hourly run of
   publish-bricks-paced, naming awdelphi.

2. A test that reads the real file puts a LIVE session bearer in the process.
   The transport is faked here so nothing was sent, but a suite that loads a
   credential it does not need is one refactor away from sending it.

Autouse, so a new test cannot forget.
"""
import pytest

from awdelphi import gateway


@pytest.fixture(autouse=True)
def _isolated_bearer(tmp_path, monkeypatch):
    fake = tmp_path / "session-bearer"
    fake.write_text("test-bearer-not-a-real-credential", encoding="utf-8")
    monkeypatch.setattr(gateway, "DEFAULT_BEARER_FILE", fake)
    return fake

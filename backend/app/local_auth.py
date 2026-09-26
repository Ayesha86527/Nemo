"""Shared-secret auth for the local API.

Electron generates a per-launch random token and hands it to both the
sidecar (NEMO_AUTH_TOKEN env) and the renderer (via the preload bridge).
Every /api/ request must carry it, so a random webpage probing 127.0.0.1
cannot read CV data or trigger agent actions even if it finds the port.

Auth is disabled when the variable is unset (manual runs, docker), which
is safe: those setups are already behind the operator's own controls.
"""

import os
import secrets

TOKEN_HEADER = "x-nemo-token"


def expected_token() -> str:
    return os.environ.get("NEMO_AUTH_TOKEN", "")


def is_authorized(provided: str) -> bool:
    expected = expected_token()
    if not expected:
        return True
    return bool(provided) and secrets.compare_digest(provided, expected)

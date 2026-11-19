"""API-key auth for the MUTATING routes (deploy / publish).

The GS API holds the Mender PAT + MinIO creds; a state-changing call (rolling
software to rigs) is privileged, so it's gated by a SEPARATE key (X-GS-Key) — NOT
the Mender token, which never leaves the backend. Read routes stay open (trusted
network). Two callers present the key:
  - colony's first-install task (over HTTP, the network path)
  - a remote operator script

When GS_API_KEY is unset, the gate is OPEN (a localhost-only deployment where the
GS-host CLI is the only caller). Set it the moment the API is network-reachable.
"""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from .settings import settings


def key_ok(got: str | None) -> bool:
    want = settings().api_key
    return bool(want) and bool(got) and hmac.compare_digest(got.encode(), want.encode())


def require_key(x_gs_key: str | None = Header(default=None)) -> None:
    """FastAPI dependency: X-GS-Key, always (there is no open mode: the app refuses to start without a key)."""
    if not key_ok(x_gs_key):
        raise HTTPException(status_code=401, detail="invalid or missing X-GS-Key")

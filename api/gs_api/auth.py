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

from fastapi import Header, HTTPException

from .settings import settings


def require_key(x_gs_key: str | None = Header(default=None)) -> None:
    """FastAPI dependency — enforce X-GS-Key on a mutating route iff a key is set."""
    s = settings()
    if not s.api_key:
        return  # gate disabled (localhost-only deployment)
    if x_gs_key != s.api_key:
        raise HTTPException(status_code=401, detail="invalid or missing X-GS-Key")

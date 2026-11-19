"""The fleet VPN backend of the GS: mints the per-rig Tailscale auth key at provision time.

One backend (the fleet tailnet, colony docs/fleet-vpn.md): a rig joins as `tag:fleet-rig` with a key that is
single-use, pre-authorised, tagged and short-lived. The key goes to colony-api as a deployment SECRET (0600 vars
file on the controller, 0600 file on the rig, removed after the join): never a tag, a log or a command line.

Config (environment): TS_API_TOKEN_FILE (preferred: a 0600 file) or TS_API_TOKEN; TS_TAILNET (default '-': the
token's own tailnet); GS_VPN_RIG_TAG (default tag:fleet-rig); GS_VPN_KEY_TTL seconds (default 3600).
Not configured -> enabled() is False and provisioning proceeds without the VPN join (stated in the response).
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request

_API = "https://api.tailscale.com/api/v2"


def _token() -> str:
    f = os.environ.get("TS_API_TOKEN_FILE", "")
    if f and os.path.isfile(f):
        return open(f, encoding="utf-8").read().strip()
    return os.environ.get("TS_API_TOKEN", "").strip()


def enabled() -> bool:
    return bool(_token())


def mint_rig_key(machine: str) -> str:
    """A single-use, pre-authorised, tagged, short-lived auth key for ONE rig. Raises on an API error."""
    tok = _token()
    if not tok:
        raise RuntimeError("fleet VPN not configured (TS_API_TOKEN_FILE / TS_API_TOKEN)")
    tailnet = os.environ.get("TS_TAILNET", "-")
    body = {
        "capabilities": {"devices": {"create": {
            "reusable": False, "ephemeral": False, "preauthorized": True,
            "tags": [os.environ.get("GS_VPN_RIG_TAG", "tag:fleet-rig")]}}},
        "expirySeconds": int(os.environ.get("GS_VPN_KEY_TTL", "3600")),
        "description": ("rig-enroll " + machine)[:50],
    }
    req = urllib.request.Request(f"{_API}/tailnet/{tailnet}/keys", data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Basic " + base64.b64encode((tok + ":").encode()).decode())
    with urllib.request.urlopen(req, timeout=15) as r:
        key = json.loads(r.read() or b"{}").get("key", "")
    if not key:
        raise RuntimeError("the Tailscale API returned no key")
    return key

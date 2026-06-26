"""Devices — the fleet inventory (Mender inventory API), normalized for the UI.

A device's Mender inventory attributes carry everything the operator dashboard
shows: device_type (the hardware class = our <fleet>), group, the running
artifact_name (== runtime release or app), and — once the rig reports it — Theia's
own health/SM-state/UCM-version inventory. We flatten the attribute list into a
dict so the UI doesn't re-walk it.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..clients import mender_client
from ..settings import settings

router = APIRouter(prefix="/api/devices", tags=["devices"])


def _scalar(v):
    """Mender attribute values can be a scalar OR a list (multi-valued, e.g. a NIC
    with several IPs). Coerce to a single hashable/renderable value for the UI:
    a 1-element list → its element, a longer list → comma-joined string."""
    if isinstance(v, list):
        # dedup preserving order (Mender often repeats device_type across scopes)
        seen = list(dict.fromkeys(str(x) for x in v))
        return seen[0] if len(seen) == 1 else ", ".join(seen)
    return v


def _flatten(dev: dict) -> dict:
    """Mender device → a flat record the UI renders directly."""
    attrs = {a["name"]: _scalar(a["value"]) for a in dev.get("attributes", []) or []}
    return {
        "id": dev.get("id"),
        "updated_ts": dev.get("updated_ts"),
        # the hardware-capability fleet (Mender device_type) — our <fleet> key
        "fleet": attrs.get("device_type"),
        "group": attrs.get("group"),
        # what's RUNNING on the rig (Mender's record of the installed artifact)
        "artifact": attrs.get("artifact_name") or attrs.get("rootfs-image.version"),
        # Theia inventory (present once the rig reports it; absent on a bare rig)
        "health": attrs.get("theia_health"),
        "sm_state": attrs.get("theia_sm_state"),
        "ucm_version": attrs.get("theia_ucm_version"),
        "ip": attrs.get("ipv4_eth0") or attrs.get("network_interfaces"),
        # keep the raw attrs for a detail view / future panels
        "attributes": attrs,
    }


@router.get("")
def list_devices(group: str | None = Query(default=None)) -> dict:
    """All enrolled devices (optionally filtered to a Mender group)."""
    s = settings()
    try:
        raw = mender_client(s).devices(group)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"mender inventory: {e}")
    devices = [_flatten(d) for d in raw]
    # group rollup for the UI's fleet selector
    fleets: dict[str, int] = {}
    for d in devices:
        if d["fleet"]:
            fleets[d["fleet"]] = fleets.get(d["fleet"], 0) + 1
    return {"devices": devices, "count": len(devices), "fleets": fleets}


@router.get("/{device_id}")
def get_device(device_id: str) -> dict:
    s = settings()
    try:
        for d in mender_client(s).devices():
            if d.get("id") == device_id:
                return _flatten(d)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"mender inventory: {e}")
    raise HTTPException(status_code=404, detail="device not found")

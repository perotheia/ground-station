"""Devices — the fleet inventory (Mender inventory API), normalized for the UI.

A device's Mender inventory attributes carry everything the operator dashboard
shows: device_type (the hardware class = our <fleet>), group, the running
artifact_name (== runtime release or app), and — once the rig reports it — Theia's
own health/SM-state/UCM-version inventory. We flatten the attribute list into a
dict so the UI doesn't re-walk it.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from .. import com_client
from ..clients import mender_client
from ..settings import settings

router = APIRouter(prefix="/api/devices", tags=["devices"])


def _require_key(x_gs_key: str | None = Header(default=None)) -> None:
    want = settings().api_key
    if want and x_gs_key != want:
        raise HTTPException(status_code=401, detail="invalid or missing X-GS-Key")


# The com AGGREGATOR endpoint that answers ListMachines (the central board's com).
# The whole cluster's Observability presence comes from this one hub.
def _aggregator() -> str:
    return os.environ.get("GS_COM_AGGREGATOR", "10.0.0.99:7700")


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
        # a human name for the row — the rig hostname (raspberrypi / jason)
        "name": attrs.get("hostname") or attrs.get("name"),
        # the hardware-capability fleet (Mender device_type) — our <fleet> key
        "fleet": attrs.get("device_type"),
        "group": attrs.get("group"),
        # the installed BASE runtime — the P2 colony base-state mirror TAG. This is
        # the compatibility key the app deploy gate checks against requires_runtime.
        "base_version": attrs.get("base_version"),
        "base_authority": attrs.get("base_authority"),
        # operator pin (guards deletion). "true"/"false" tag → bool.
        "pinned": str(attrs.get("pinned", "")).lower() == "true",
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
    # ── connected state: cross-reference Mender (accepted) with the Observability
    #    cluster (ListMachines). Two states, no can't-tell — the whole fleet runs
    #    ListMachines: mender+com (present in the cluster) or mender-only (accepted
    #    but the supervisor isn't published — a real health gap). (com-only boards
    #    aren't in Mender inventory → they surface via /pending.) ────────────────
    observed = _observed()              # cluster view (raises 502 on fault)
    by_inst = observed["by_inst"]
    by_name = observed["by_name"]
    rel_by_inst = observed["rel_by_inst"]
    rel_by_name = observed["rel_by_name"]
    for d in devices:
        attrs = d.get("attributes", {}) or {}
        # 1. by INSTANCE (authoritative): the device's machine_instance tag
        inst = attrs.get("machine_instance")
        in_cluster = bool(inst is not None and by_inst.get(str(inst)))
        live_rel = rel_by_inst.get(str(inst)) if inst is not None else ""
        # 2. by NAME (the manifest machine name, if the board reports it)
        if not in_cluster:
            mach = (attrs.get("machine") or d.get("name") or "").lower()
            for mn, present in by_name.items():
                if present and (mn.lower() == mach
                                or (mach and (mn.lower() in mach or mach in mn.lower()))):
                    in_cluster = True
                    live_rel = live_rel or rel_by_name.get(mn, "")
                    break
        d["connected"] = "mender+com" if in_cluster else "mender-only"
        # STATELESS base_version: prefer the LIVE supervisor-reported release; fall
        # back to the Mender mirror tag (transition: old runtimes report no release).
        d["base_version"] = live_rel or d.get("base_version")
        d["base_source"] = "live" if live_rel else ("mirror" if d.get("base_version") else None)
    # group rollup for the UI's fleet selector
    fleets: dict[str, int] = {}
    for d in devices:
        if d["fleet"]:
            fleets[d["fleet"]] = fleets.get(d["fleet"], 0) + 1
    return {"devices": devices, "count": len(devices), "fleets": fleets,
            "cluster": by_name}


# ── Connect Device — the GS funnel (dual registration) ───────────────────────
# A board is "connected" only when it's in BOTH Mender (accepted, deployable) AND
# the Observability cluster (present in ListMachines). GS is the single onboarding
# path; nothing is connected until both are true. See docs/design/gs-ux-design.md §4.

def _observed() -> dict:
    """The ListMachines cluster view from the com aggregator. The whole fleet runs
    a runtime with ListMachines (no degrade) — a failure here is a REAL fault
    (aggregator down / wrong endpoint), surfaced as 502, not swallowed. Returns
    {by_name: {name: present}, by_inst: {instance: present}} so a device matches
    by INSTANCE (robust to the m<N> name fallback when a board doesn't report its
    manifest machine name) or by name."""
    try:
        machines = com_client.list_machines(_aggregator())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"ListMachines @ {_aggregator()} failed: {e}. The Observability "
                   "aggregator must be reachable (the fleet runs ListMachines "
                   "everywhere — no degrade).")
    return {
        "by_name": {m["name"]: m["present"] for m in machines},
        "by_inst": {str(m["instance"]): m["present"] for m in machines},
        # LIVE base version (stateless) per instance/name, from the supervisor's
        # release_version — the authoritative source GS reads (vs a stored tag).
        "rel_by_inst": {str(m["instance"]): m.get("release_version", "") for m in machines},
        "rel_by_name": {(m.get("machine_name") or m["name"]): m.get("release_version", "")
                        for m in machines},
    }


@router.get("/pending")
def pending() -> dict:
    """Boards Mender knows but hasn't accepted yet (auth-set status=pending) — the
    Connect candidates. Keyed by MAC (the stable Connect handle)."""
    s = settings()
    try:
        auth = mender_client(s).auth_devices()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"mender devauth: {e}")
    out = [{"id": d["id"], "status": d.get("status"),
            "mac": (d.get("identity_data") or {}).get("mac"),
            "identity": d.get("identity_data")}
           for d in auth if d.get("status") == "pending"]
    return {"pending": out, "count": len(out)}


class ConnectRequest(BaseModel):
    mac: str
    fleet: str | None = None        # expected device_type (compatibility class)
    group: str | None = None        # optional Mender group


@router.post("/connect", dependencies=[Depends(_require_key)])
def connect(req: ConnectRequest) -> dict:
    """Onboard a board: ACCEPT its pending Mender auth-set (by MAC) + confirm it is
    present in the Observability cluster. The com-half is observational — a board
    self-publishes over TIPC, so we verify rather than register."""
    s = settings()
    m = mender_client(s)
    # ── Mender-half: accept the pending auth-set by MAC ──────────────────────
    dev = m.find_by_mac(req.mac)
    if not dev:
        raise HTTPException(status_code=404,
                            detail=f"no Mender device with mac {req.mac} "
                                   "(is the board's mender client checking in?)")
    if dev.get("status") != "accepted":
        auth_sets = dev.get("auth_sets") or []
        pend = next((a for a in auth_sets if a.get("status") == "pending"), None)
        if not pend:
            raise HTTPException(status_code=409,
                                detail=f"device {req.mac} has no pending auth-set "
                                       f"(status={dev.get('status')})")
        m.accept_device(dev["id"], pend["id"])
        accepted = True
    else:
        accepted = False        # already accepted — idempotent
    # optional group assignment
    if req.group:
        try:
            m.assign_group(dev["id"], req.group)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass            # group is best-effort; accept already succeeded
    # ── com-half: is the board present in the Observability cluster? ─────────
    observed = _observed()
    return {
        "mac": req.mac,
        "device_id": dev["id"],
        "mender": {"accepted": True, "newly_accepted": accepted,
                   "fleet_expected": req.fleet},
        "observability": {"present_in_cluster": any(observed["by_name"].values()),
                          "machines": observed["by_name"],
                          "aggregator": _aggregator()},
        "connected": True,   # Mender-accepted; com presence may warm up on next poll
    }


def _device_pinned(m, device_id: str) -> bool:
    for d in m.devices():
        if d.get("id") == device_id:
            for a in d.get("attributes", []) or []:
                if a["name"] == "pinned":
                    v = a["value"]
                    return str(v[0] if isinstance(v, list) and v else v).lower() == "true"
    return False


class PinRequest(BaseModel):
    pinned: bool


@router.post("/{device_id}/pin", dependencies=[Depends(_require_key)])
def pin(device_id: str, req: PinRequest) -> dict:
    """Pin/unpin a device (a `pinned` Mender tag). A pinned device is guarded from
    deletion — you must unpin first (a deliberate two-step for the destructive op)."""
    s = settings()
    m = mender_client(s)
    # preserve other tags isn't trivial via PUT-replace; pin is a single managed
    # tag here — set_tags replaces the tag set, so re-merge the base-state tags.
    keep = {}
    for d in m.devices():
        if d.get("id") == device_id:
            for a in d.get("attributes", []) or []:
                if a.get("scope") == "tags" and a["name"] != "pinned":
                    v = a["value"]
                    keep[a["name"]] = v[0] if isinstance(v, list) and v else v
            break
    tags = dict(keep)
    if req.pinned:
        tags["pinned"] = "true"
    try:
        m.set_tags(device_id, tags)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"pin: {e}")
    return {"device_id": device_id, "pinned": req.pinned}


@router.delete("/{device_id}", dependencies=[Depends(_require_key)])
def decommission(device_id: str) -> dict:
    """The inverse of Connect: decommission the Mender device. GUARDED — a PINNED
    device must be unpinned first (the destructive op is deliberately two-step)."""
    s = settings()
    m = mender_client(s)
    if _device_pinned(m, device_id):
        raise HTTPException(status_code=409,
                            detail="device is pinned — unpin before deleting")
    try:
        m.decommission_device(device_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"decommission: {e}")
    return {"device_id": device_id, "decommissioned": True}


# Registered LAST so the static routes (/pending, /connect) win the path match —
# FastAPI matches in declaration order, and /{device_id} is a catch-all.
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

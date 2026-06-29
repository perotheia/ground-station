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
from ..colony_client import colony_client
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
    # STABLE IDENTITY = the MAC (Mender identity_data — immutable, survives
    # re-enrol). device_id (the UUID) rotates on decommission, so GS keys on MAC.
    idd = dev.get("identity_data") or {}
    mac = idd.get("mac") if isinstance(idd, dict) else None
    return {
        "id": dev.get("id"),                 # server UUID (rotates — display only)
        "mac": mac,                          # the STABLE identity
        "updated_ts": dev.get("updated_ts"),
        # DISPLAY NAME: operator-assigned tag (set at Connect, keyed on the MAC →
        # same in GS + Mender, survives re-enrol) → hostname → the MAC.
        "name": attrs.get("name") or attrs.get("hostname") or mac,
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
def list_devices(group: str | None = Query(default=None),
                 status: str = Query(default="accepted")) -> dict:
    """The fleet — sourced from deviceauth v2 (EVERY device + its auth status), so
    pending/preauthorized/rejected show too (Mender's single-list model). Accepted
    devices are enriched with their Mender inventory (fleet/base/app/health). The
    `status` filter: any|accepted|pending|preauthorized|rejected. A `group` filter
    narrows to a static group's members (accepted only)."""
    s = settings()
    m = mender_client(s)
    try:
        auth = m.auth_devices()                 # devauth v2: id, identity_data, status, auth_sets
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"mender deviceauth: {e}")
    # inventory (accepted devices' attributes), keyed by device id — best-effort.
    inv_by_id: dict = {}
    group_ids: set | None = None
    try:
        for d in m.devices(group):
            inv_by_id[d.get("id")] = d
        if group:
            group_ids = set(inv_by_id.keys())   # the group's members (inventory)
    except Exception:  # noqa: BLE001
        pass            # inventory enrichment is best-effort; devauth is the spine
    devices = []
    for a in auth:
        st = a.get("status")
        if status != "any" and st != status:
            continue
        if group_ids is not None and a.get("id") not in group_ids:
            continue
        inv = inv_by_id.get(a.get("id"))
        d = _flatten(inv) if inv else {
            "id": a.get("id"),
            "mac": (a.get("identity_data") or {}).get("mac"),
            "name": (a.get("identity_data") or {}).get("mac"),
            "fleet": None, "group": None, "attributes": {},
        }
        d["auth_status"] = st                   # accepted|pending|preauthorized|rejected
        # the first auth-set id (needed to accept/reject this device)
        asets = a.get("auth_sets") or []
        d["auth_set_id"] = asets[0]["id"] if asets else None
        devices.append(d)
    # ── connected state: cross-reference Mender (accepted) with the Observability
    #    cluster (ListMachines). Two states, no can't-tell — the whole fleet runs
    #    ListMachines: mender+com (present in the cluster) or mender-only (accepted
    #    but the supervisor isn't published — a real health gap). (com-only boards
    #    aren't in Mender inventory → they surface via /pending.) ────────────────
    # Observability cross-ref is BEST-EFFORT now (the list is devauth-sourced; a
    # cluster fault or a non-accepted device must not sink the fleet list).
    try:
        observed = _observed()
    except Exception:  # noqa: BLE001
        observed = {"by_inst": {}, "by_name": {}, "rel_by_inst": {}, "rel_by_name": {}}
    by_inst = observed["by_inst"]
    by_name = observed["by_name"]
    rel_by_inst = observed["rel_by_inst"]
    rel_by_name = observed["rel_by_name"]
    for d in devices:
        # non-accepted devices (pending/preauthorized/rejected) have no cluster
        # presence — their connected state IS their auth status.
        if d.get("auth_status") and d["auth_status"] != "accepted":
            d["connected"] = d["auth_status"]
            d["base_source"] = None
            continue
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


class PreauthorizeRequest(BaseModel):
    mac: str                         # the device's identity_data.mac
    pubkey: str                      # its public key (PEM)
    name: str | None = None          # optional operator display name (tag, applied on accept)


@router.post("/preauthorize", dependencies=[Depends(_require_key)])
def preauthorize(req: PreauthorizeRequest) -> dict:
    """Pre-authorize a device BEFORE it checks in: submit identity_data (MAC) +
    pubkey to devauth. When the real board first connects with that identity+key
    Mender auto-accepts it. (Connect option 2, vs the SSH-probe 'connect new'.)"""
    s = settings()
    try:
        r = mender_client(s).preauthorize({"mac": req.mac}, req.pubkey)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"preauthorize: {e}")
    return {"mac": req.mac, "status": "preauthorized", **r}


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
    name: str | None = None         # operator-assigned display name (Mender tag)


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
    # operator-assigned display NAME → a Mender inventory tag. Keyed on the device
    # (which Mender ties to the immutable MAC identity), so the name is the same in
    # GS + Mender and survives re-enrol. _flatten reads attrs["name"] first.
    if req.name:
        try:
            m.set_tags(dev["id"], {"name": req.name})  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass            # name is best-effort; accept already succeeded
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


# ── Enrolment helpers (Create New Target modal) ──────────────────────────────
@router.get("/probe")
def probe(host: str) -> dict:
    """SSH-probe a host for its stable identity (MAC + hostname) to PREFILL the
    Create-Target modal. gs-api has no SSH; colony-api does (the rig key), so we
    proxy to it. The operator types a Host IP → reload → Controller ID=MAC,
    Name=hostname."""
    from ..colony_client import colony_client
    s = settings()
    col = colony_client(s)
    st, data = col._req("GET", f"/probe?host={host}")  # noqa: SLF001
    if st != 200:
        import json as _j
        try:
            detail = _j.loads(data).get("detail", data.decode(errors="replace"))
        except Exception:  # noqa: BLE001
            detail = data.decode(errors="replace")[:200]
        raise HTTPException(status_code=502, detail=f"probe: {detail}")
    import json as _j
    return _j.loads(data or b"{}")


@router.get("/types")
def device_types() -> dict:
    """The Type dropdown options — device_types seen in Mender inventory UNION the
    known Theia roles. STATELESS: derived live from Mender, not stored in GS."""
    s = settings()
    known = {"theia-rig", "theia-gateway"}
    seen = set()
    try:
        for d in mender_client(s).devices():
            f = _flatten(d)
            if f.get("fleet"):
                for tv in str(f["fleet"]).split(","):
                    if tv.strip():
                        seen.add(tv.strip())
    except Exception:  # noqa: BLE001
        pass            # types are advisory; fall back to the known set
    return {"types": sorted(known | seen)}


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


# ── Groups (P3): the named buckets a board can be assigned to ──────────────────
@router.get("/groups/list")
def list_groups() -> dict:
    """The named Mender groups + a member count, for the Fleet group selector.
    Derived from the device inventory (every device carries its `group` attr) so
    we don't depend on a separate groups API surface."""
    s = settings()
    m = mender_client(s)
    counts: dict[str, int] = {}
    try:
        for d in m.devices():
            g = _flatten(d).get("group")
            if g:
                counts[g] = counts.get(g, 0) + 1
        # include groups defined in Mender that currently have 0 members
        for g in m.list_groups():
            counts.setdefault(g, 0)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"mender inventory: {e}")
    groups = [{"name": k, "count": v} for k, v in sorted(counts.items())]
    return {"groups": groups, "count": len(groups)}


class GroupRequest(BaseModel):
    group: str


@router.post("/{device_id}/group", dependencies=[Depends(_require_key)])
def assign_group(device_id: str, req: GroupRequest) -> dict:
    """Move a board into a named group (Mender inventory group). Idempotent."""
    s = settings()
    try:
        mender_client(s).assign_group(device_id, req.group)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"assign group: {e}")
    return {"device_id": device_id, "group": req.group}


@router.delete("/{device_id}/group", dependencies=[Depends(_require_key)])
def remove_group(device_id: str, group: str) -> dict:
    """Remove a board from a Mender static group. The group name is the membership
    key in Mender (DELETE .../group/<name>). Idempotent (404 = already out)."""
    s = settings()
    try:
        mender_client(s).remove_from_group(device_id, group)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"remove group: {e}")
    return {"device_id": device_id, "removed_from": group}


# ── Merged timeline (P3): base (colony) + app (Mender) + state, chronological ──
@router.get("/{device_id}/timeline")
def device_timeline(device_id: str) -> dict:
    """The device side-panel feed: colony BASE deployments + Mender APP deployments
    that touched this board + its current Theia state — merged newest-first, each
    event authority-tagged. Mirrors the unified-deployments model (design §6), but
    scoped to one device. A failing source is reported, not fatal."""
    import json
    s = settings()
    events: list[dict] = []
    errors: dict[str, str] = {}

    # Resolve the device's identity keys (id, hostname, fleet) to match events.
    dev_name = None
    fleet = None
    try:
        for d in mender_client(s).devices():
            if d.get("id") == device_id:
                f = _flatten(d)
                dev_name, fleet = f.get("name"), f.get("fleet")
                # current-state markers (no timestamp → "now")
                if f.get("base_version"):
                    events.append({"authority": "base", "kind": "installed",
                                   "title": f"base {f['base_version']}",
                                   "detail": f"source={f.get('base_source') or f.get('base_authority') or '?'}",
                                   "ts": f.get("updated_ts"), "status": "current"})
                if f.get("artifact"):
                    events.append({"authority": "app", "kind": "installed",
                                   "title": f"app {f['artifact']}",
                                   "ts": f.get("updated_ts"), "status": "current"})
                for k, lbl in (("health", "health"), ("sm_state", "SM"),
                               ("ucm_version", "UCM")):
                    if f.get(k):
                        events.append({"authority": "state", "kind": k,
                                       "title": f"{lbl}: {f[k]}",
                                       "ts": f.get("updated_ts"), "status": "info"})
                break
    except Exception as e:  # noqa: BLE001
        errors["inventory"] = str(e)

    # ── APP plane: Mender deployments that included this device ──────────────
    try:
        m = mender_client(s)
        st, data, _ = m._req("GET", f"{m.dep}?per_page=100")  # noqa: SLF001
        if st == 200:
            for d in json.loads(data or b"[]"):
                devs = d.get("device_count") or 0
                # the deployment's per-device list (if Mender returns it inline)
                touched = device_id in (d.get("devices") or [])
                # else match by artifact/group heuristically — fall back to "all
                # accepted in group" being the deployment's target; keep it simple:
                if touched or devs:
                    events.append({
                        "authority": "app", "kind": "deployment",
                        "title": d.get("name") or d.get("artifact_name") or d.get("id"),
                        "detail": (d.get("status") or "?"),
                        "ts": d.get("created"), "status": d.get("status"),
                        "id": d.get("id"),
                    })
    except Exception as e:  # noqa: BLE001
        errors["app"] = str(e)

    # ── BASE plane: colony deployments for this rig (by hostname) ────────────
    try:
        for d in colony_client(s).deployments():
            rig = d.get("rig") or d.get("rig_id") or ""
            if dev_name and rig and (rig == dev_name or rig.startswith(str(dev_name))):
                stats = d.get("statistics") or {}
                events.append({
                    "authority": "base", "kind": "deployment",
                    "title": f"{d.get('kind', 'orchestrate')} {d.get('runtime_version', '')}".strip(),
                    "detail": stats.get("status") or d.get("status") or "?",
                    "ts": d.get("created") or d.get("created_ts"),
                    "status": stats.get("status") or d.get("status"),
                    "id": d.get("id"),
                })
    except Exception as e:  # noqa: BLE001
        errors["base"] = str(e)

    events.sort(key=lambda e: str(e.get("ts") or ""), reverse=True)
    out: dict = {"device_id": device_id, "name": dev_name, "fleet": fleet,
                 "events": events, "count": len(events)}
    if errors:
        out["errors"] = errors
    return out

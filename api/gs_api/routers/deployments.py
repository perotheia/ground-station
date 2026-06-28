"""Deployments — the OTA rollout control plane (Mender deployments + artifacts).

list/status/statistics for the rollout timeline + create a deployment of a known
artifact to a device group (the operator's "roll out <artifact> to <fleet>"). The
group is resolved to its devices here (the v1 API takes a device list).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import com_client
from ..auth import require_key
from ..clients import mender_client, resolve_fleet
from ..colony_client import colony_client
from ..settings import settings

router = APIRouter(prefix="/api/deployments", tags=["deployments"])


@router.get("")
def list_deployments() -> dict:
    """The UNIFIED rollout history — Mender (APP) deployments + colony (BASE)
    deployments, merged into one newest-first list, each row authority-tagged
    (`base`|`app`). The operator's one surface for two authorities (design §6).
    A source that errors is reported but doesn't sink the other."""
    import json
    s = settings()
    rows: list[dict] = []
    errors: dict[str, str] = {}

    # ── APP plane: Mender deployments ────────────────────────────────────────
    try:
        m = mender_client(s)
        st, data, _ = m._req("GET", f"{m.dep}?per_page=100")  # noqa: SLF001
        if st != 200:
            raise RuntimeError(f"[{st}] {data.decode(errors='replace')[:200]}")
        for d in json.loads(data or b"[]"):
            d["authority"] = "app"
            rows.append(d)
    except Exception as e:  # noqa: BLE001
        errors["app"] = str(e)

    # ── BASE plane: colony-api deployments ───────────────────────────────────
    try:
        for d in colony_client(s).deployments():
            d["authority"] = "base"   # colony rows already carry statistics.status
            rows.append(d)
    except Exception as e:  # noqa: BLE001
        errors["base"] = str(e)

    rows.sort(key=lambda d: str(d.get("created") or d.get("created_ts") or 0),
              reverse=True)
    out: dict = {"deployments": rows, "count": len(rows)}
    if errors:
        out["errors"] = errors
    return out


@router.get("/{dep_id}")
def deployment(dep_id: str) -> dict:
    s = settings()
    m = mender_client(s)
    try:
        return {"deployment": m.deployment_status(dep_id),
                "statistics": m.deployment_statistics(dep_id)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"mender deployment {dep_id}: {e}")


@router.get("/artifacts/list")
def artifacts() -> dict:
    """Artifacts uploaded to the Mender GW (what CAN be deployed)."""
    s = settings()
    try:
        return {"artifacts": mender_client(s).list_artifacts()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"mender artifacts: {e}")


# ── BASE deployment (colony) + the base-state mirror into Mender ─────────────
def _mender_device_for_rig(s, m, rig: str) -> dict | None:
    """Map a colony rig → its Mender device for the base-state mirror. The robust
    key is the rig's `ansible_host` IP (registry) matched against the device's
    reported ipv4_* (the rpi4 reports hostname=raspberrypi, NOT the rig name
    'central', so a name match alone fails). Hostname/name match is the fallback."""
    rig_ip = None
    try:
        rinfo = next((r for r in colony_client(s).rigs() if r.get("name") == rig), {})
        rig_ip = str(rinfo.get("ansible_host") or "")
    except Exception:  # noqa: BLE001
        pass
    rigl = rig.lower()
    for d in m.devices():
        attrs = {a["name"]: (a["value"][0] if isinstance(a["value"], list) and a["value"]
                             else a["value"]) for a in d.get("attributes", []) or []}
        # by IP (authoritative): any ipv4_* attr whose address equals the rig host
        if rig_ip:
            for k, v in attrs.items():
                if k.startswith("ipv4") and str(v).split("/")[0] == rig_ip:
                    return d
        # by name/hostname (fallback)
        cand = str(attrs.get("machine") or attrs.get("hostname")
                   or attrs.get("name") or "").lower()
        if cand and (cand == rigl or rigl in cand or cand in rigl):
            return d
    return None


class BaseDeployRequest(BaseModel):
    rig: str
    kind: str = "orchestrate"        # provision | orchestrate
    schedule: float | None = None
    mirror: bool = True              # write the base-state tags on finish


@router.post("/base", dependencies=[Depends(require_key)])
def deploy_base(req: BaseDeployRequest) -> dict:
    """Run a BASE deployment via colony-api, then MIRROR the result into the rig's
    Mender device tags (base_version / base_authority=colony / base_deployed_at) so
    Mender UX reflects what colony drove (design §5). Synchronous for a lab fleet:
    trigger → poll-to-finish → mirror. A scheduled deploy returns immediately (the
    mirror then happens on a later /base/{id}/mirror or the next poll)."""
    import time
    s = settings()
    col = colony_client(s)
    dep = col.create(req.rig, req.kind, req.schedule)
    did = dep["id"]
    if req.schedule:          # future-dated → don't block; mirror later
        return {"deployment": dep, "mirrored": False, "note": "scheduled; mirror on finish"}
    # poll to finish (orchestrate ~60s; cap a few minutes)
    deadline = time.time() + 600
    while time.time() < deadline:
        dep = col.deployment(did)
        if dep.get("status") == "finished":
            break
        time.sleep(3)
    stats = (dep.get("statistics") or {}).get("status", {})
    ok = stats.get("success", 0) > 0 and stats.get("failure", 0) == 0
    mirrored = False
    if req.mirror and ok:
        mirrored = _mirror_base_state(s, req.rig, dep)
    return {"deployment": dep, "ok": ok, "mirrored": mirrored}


def _mirror_base_state(s, rig: str, dep: dict) -> bool:
    """Write the base-state tags onto the rig's Mender device. The version is the
    rig's runtime_version (the release colony staged); authority is always colony."""
    import time
    m = mender_client(s)
    dev = _mender_device_for_rig(s, m, rig)
    if not dev:
        return False
    # the runtime_version the rig pulled — from colony-api's rig registry
    ver = "unknown"
    try:
        rinfo = next((r for r in colony_client(s).rigs() if r.get("name") == rig), {})
        ver = rinfo.get("runtime_version") or "unknown"
    except Exception:  # noqa: BLE001
        pass
    try:
        m.set_tags(dev["id"], {
            "base_version": ver,
            "base_authority": "colony",
            "base_kind": dep.get("kind", "orchestrate"),
            "base_deployed_at": str(int(time.time())),
            "base_status": "success",
        })
        return True
    except Exception:  # noqa: BLE001
        return False


@router.post("/base/{did}/mirror", dependencies=[Depends(require_key)])
def mirror_base(did: str, rig: str) -> dict:
    """Mirror a (already-finished) colony deployment's base-state into Mender tags.
    Used for scheduled deploys whose finish wasn't awaited inline."""
    s = settings()
    dep = colony_client(s).deployment(did)
    return {"mirrored": _mirror_base_state(s, rig, dep), "deployment_id": did}


class DeployRequest(BaseModel):
    artifact_name: str
    # Target EITHER a fleet (device_type — the default, device-by-device) OR a
    # Mender group. Exactly one is used; fleet wins if both are set.
    fleet: str | None = None
    group: str | None = None
    name: str | None = None


@router.post("", dependencies=[Depends(require_key)])
def create_deployment(req: DeployRequest) -> dict:
    """Roll out <artifact_name> to a fleet (device_type, device-by-device) or a
    Mender group. Returns the new deployment id. Mutating → X-GS-Key gated."""
    s = settings()
    m = mender_client(s)
    target = req.fleet or req.group
    if not target:
        raise HTTPException(status_code=400, detail="need a fleet or group to target")
    try:
        devices = (resolve_fleet(m, req.fleet) if req.fleet
                   else m.device_ids_in_group(req.group))
        if not devices:
            raise HTTPException(
                status_code=400,
                detail=f"no devices match {'fleet' if req.fleet else 'group'} "
                       f"'{target}' (enrolled?)")
        name = req.name or f"theia-{req.artifact_name}-{target}"
        dep_id = m.create_deployment(name, req.artifact_name, devices)
        return {"id": dep_id, "name": name, "devices": len(devices),
                "target": target, "artifact_name": req.artifact_name}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"create deployment: {e}")


@router.post("/{dep_id}/abort", dependencies=[Depends(require_key)])
def abort_deployment(dep_id: str) -> dict:
    """Abort an in-flight deployment (the Mender transport-plane cancel). Devices
    not yet finished stop; the on-device UCM verify-window rolls back any
    half-applied install. Mutating → X-GS-Key gated."""
    s = settings()
    try:
        mender_client(s).abort_deployment(dep_id)
        return {"id": dep_id, "aborted": True}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"abort {dep_id}: {e}")


@router.get("/{dep_id}/rollout")
def rollout(dep_id: str) -> dict:
    """The COMBINED rollout view — both planes for one deployment:
      transport plane: the Mender deployment status + statistics (download/install).
      ECU plane:       per-device UCM FSM lifecycle + SM-session state (over com).
    The operator sees the bytes arriving (Mender) AND the AUTOSAR install running
    (UCM/SM) in one view. The ECU plane is best-effort (a rig unreachable over com
    just shows ecu.ok=false)."""
    s = settings()
    m = mender_client(s)
    try:
        dep = m.deployment_status(dep_id)
        stats = m.deployment_statistics(dep_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"deployment {dep_id}: {e}")

    # the ECU plane: poll each target device's UCM/SM via com. Resolve endpoints
    # from inventory (ipv4); skip devices we can't reach.
    devices = m.devices()
    by_id = {d.get("id"): d for d in devices}
    ecu = []
    for dev_id in _deployment_device_ids(m, dep_id, dep):
        target = _com_endpoint(by_id.get(dev_id))
        rec = {"device": dev_id, "target": target}
        if target:
            try:
                rec["progress"] = com_client.get_progress(target, timeout=4.0)
            except Exception as e:  # noqa: BLE001
                rec["error"] = str(e)
        ecu.append(rec)

    return {"transport": {"deployment": dep, "statistics": stats},
            "ecu": ecu}


def _deployment_device_ids(m, dep_id: str, dep: dict) -> list[str]:
    """The device ids a deployment targets. Mender's per-deployment /devices is only
    populated once a device CHECKS IN for the deployment (poll interval), so for a
    fresh/pending deployment it's empty. Fall back to the fleet (device_type from
    the deployment's artifact compatibility) so the ECU plane shows the target rigs
    immediately — the operator watches them even before they pick up the artifact."""
    import json
    try:
        st, data, _ = m._req("GET", f"{m.dep}/{dep_id}/devices?per_page=100")  # noqa: SLF001
        if st == 200:
            ids = [d.get("id") for d in json.loads(data or b"[]") if d.get("id")]
            if ids:
                return ids
    except Exception:  # noqa: BLE001
        pass
    # fallback: the deployment's name encodes the fleet (theia-<artifact>-<fleet>),
    # but simplest + robust — show every enrolled device whose device_type matches
    # the deployment's compatible types. The artifact's compatibility is on the
    # artifact, not the deployment dict, so resolve it; if that's unavailable, show
    # all enrolled rigs (the deployment targeted the fleet).
    compat: list[str] = []
    for art_id in (dep.get("artifacts") or []):
        try:
            st, data, _ = m._req("GET", f"{m.art}/{art_id}")  # noqa: SLF001
            if st == 200:
                compat += json.loads(data or b"{}").get("device_types_compatible", [])
        except Exception:  # noqa: BLE001
            pass
    out = []
    for d in m.devices():
        dt = next((a["value"] for a in d.get("attributes", []) or []
                   if a["name"] == "device_type"), None)
        dtv = dt[0] if isinstance(dt, list) and dt else dt
        if not compat or dtv in compat:
            out.append(d["id"])
    return out


def _com_endpoint(dev: dict | None) -> str | None:
    if not dev:
        return None
    attrs = {a["name"]: a["value"] for a in dev.get("attributes", []) or []}
    for k, v in attrs.items():
        if k.startswith("ipv4") and v:
            ip = (v[0] if isinstance(v, list) else v)
            return f"{str(ip).split('/')[0]}:7700"
    return None

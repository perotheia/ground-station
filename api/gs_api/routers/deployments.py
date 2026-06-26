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
from ..settings import settings

router = APIRouter(prefix="/api/deployments", tags=["deployments"])


@router.get("")
def list_deployments() -> dict:
    """All deployments (the rollout history). Mender returns newest-first."""
    s = settings()
    m = mender_client(s)
    try:
        # the deployments list endpoint shares the dep base
        st, data, _ = m._req("GET", f"{m.dep}?per_page=100")  # noqa: SLF001
        if st != 200:
            raise RuntimeError(f"[{st}] {data.decode(errors='replace')[:200]}")
        import json
        return {"deployments": json.loads(data or b"[]")}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"mender deployments: {e}")


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
    """The device ids a deployment targets (from the deployment's device_count /
    devices list — Mender's per-deployment device endpoint)."""
    try:
        st, data, _ = m._req("GET", f"{m.dep}/{dep_id}/devices?per_page=100")  # noqa: SLF001
        if st == 200:
            import json
            return [d.get("id") for d in json.loads(data or b"[]") if d.get("id")]
    except Exception:  # noqa: BLE001
        pass
    return []


def _com_endpoint(dev: dict | None) -> str | None:
    if not dev:
        return None
    attrs = {a["name"]: a["value"] for a in dev.get("attributes", []) or []}
    for k, v in attrs.items():
        if k.startswith("ipv4") and v:
            ip = (v[0] if isinstance(v, list) else v)
            return f"{str(ip).split('/')[0]}:7700"
    return None

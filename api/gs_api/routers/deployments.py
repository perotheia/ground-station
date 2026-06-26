"""Deployments — the OTA rollout control plane (Mender deployments + artifacts).

list/status/statistics for the rollout timeline + create a deployment of a known
artifact to a device group (the operator's "roll out <artifact> to <fleet>"). The
group is resolved to its devices here (the v1 API takes a device list).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

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

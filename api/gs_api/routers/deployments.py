"""Deployments — the OTA rollout control plane (Mender deployments + artifacts).

list/status/statistics for the rollout timeline + create a deployment of a known
artifact to a device group (the operator's "roll out <artifact> to <fleet>"). The
group is resolved to its devices here (the v1 API takes a device list).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..clients import mender_client
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
    group: str
    name: str | None = None


@router.post("")
def create_deployment(req: DeployRequest) -> dict:
    """Roll out <artifact_name> to every device in <group> (the Mender device-group
    = the hardware-capability fleet). Returns the new deployment id."""
    s = settings()
    m = mender_client(s)
    try:
        devices = m.device_ids_in_group(req.group)
        if not devices:
            raise HTTPException(
                status_code=400,
                detail=f"no devices in group '{req.group}' (enrolled + grouped?)")
        name = req.name or f"theia-{req.artifact_name}-{req.group}"
        dep_id = m.create_deployment(name, req.artifact_name, devices)
        return {"id": dep_id, "name": name, "devices": len(devices),
                "group": req.group, "artifact_name": req.artifact_name}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"create deployment: {e}")

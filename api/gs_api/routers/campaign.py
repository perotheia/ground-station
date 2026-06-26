"""Campaign — the L4-B VEHICLE plane (GS drives V-UCM, the campaign coordinator).

UCM (the /api/ucm router) drives ONE board's installer. A real robot is several
ECUs; the VEHICLE campaign is V-UCM's job: it fans the package to every board's
UCM, holds CMP_CONFIRMING until ALL boards are PROVISIONAL (read from the shared
etcd markers), then fans the aggregate Confirm so the boards activate together
(or any-fail/timeout → fan Cancel → all roll back). GS drives the CAMPAIGN here.

The campaign is addressed by the COORDINATOR board's com endpoint (<ip>:7700 —
the central/gateway board, the one running V-UCM). A worker-only board's com
returns UNAVAILABLE for these RPCs.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import com_client
from ..auth import require_key
from ..clients import mender_client
from ..settings import settings

router = APIRouter(prefix="/api/campaign", tags=["campaign"])

COM_PORT = 7700


def _coordinator_endpoint(device_id: str) -> str:
    """Resolve a Mender device id → its com gRPC endpoint (<ip>:7700). For L4-B the
    device_id is the CENTRAL/gateway board (the one Mender device-of-record for the
    robot) — the coordinator running V-UCM."""
    s = settings()
    for d in mender_client(s).devices():
        if d.get("id") != device_id:
            continue
        attrs = {a["name"]: a["value"] for a in d.get("attributes", []) or []}
        ip = None
        for k, v in attrs.items():
            if k.startswith("ipv4") and v:
                ip = v[0] if isinstance(v, list) else v
                ip = str(ip).split("/")[0]
                break
        if not ip:
            raise HTTPException(status_code=400,
                                detail=f"device {device_id} reports no ipv4 in inventory")
        return f"{ip}:{COM_PORT}"
    raise HTTPException(status_code=404, detail="device not found")


@router.get("/{device_id}/status")
def status(device_id: str) -> dict:
    """The current vehicle CampaignProgress (the aggregate-barrier state) from the
    coordinator board. valid=false → no V-UCM there / no active campaign."""
    target = _coordinator_endpoint(device_id)
    try:
        return {"device": device_id, "target": target,
                "campaign": com_client.campaign_status(target)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"campaign status ({target}): {e}")


class CampaignStart(BaseModel):
    campaign_id: str           # the Mender deployment id (or any vehicle-campaign id)
    version: str               # target release, e.g. "2026.08"
    scope: int = 0             # 0=FULL 1=PARTIAL


@router.post("/{device_id}/start", dependencies=[Depends(require_key)])
def start(device_id: str, req: CampaignStart) -> dict:
    """Start a VEHICLE campaign on the coordinator board (GS as the fleet operator):
    V-UCM fans the package to every board's UCM + runs the CMP_CONFIRMING aggregate
    barrier. Mutating → X-GS-Key gated. Poll /campaign/{id}/status to watch the
    barrier (INSTALLING → CONFIRMING → VALIDATING → DONE)."""
    target = _coordinator_endpoint(device_id)
    try:
        r = com_client.check_for_campaign(target, req.campaign_id, req.version,
                                          scope=req.scope)
        return {"device": device_id, "target": target, **r}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"campaign start ({target}): {e}")

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
from ..addressing import COM_PORT, NoReachableAddress, com_endpoint
from ..auth import require_key
from ..clients import mender_client
from ..settings import settings

router = APIRouter(prefix="/api/campaign", tags=["campaign"])


def _coordinator_endpoint(device_id: str) -> str:
    """Resolve a Mender device id → its com gRPC endpoint (<ip>:7700). For L4-B the
    device_id is the CENTRAL/gateway board (the one Mender device-of-record for the
    robot) — the coordinator running V-UCM.
    The address is the device's REACHABLE one (addressing.com_endpoint: remote_ip, then local_ip), never an
    inventory ipv4_* interface."""
    s = settings()
    for d in mender_client(s).devices():
        if d.get("id") != device_id:
            continue
        try:
            return com_endpoint(d, COM_PORT)
        except NoReachableAddress as e:
            raise HTTPException(status_code=400, detail=str(e))
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
    V-UCM fans RequestUpdate to every board's UCM (each installs its role package via
    Mender standalone + holds PROVISIONAL), then runs the aggregate barrier. Mutating
    → X-GS-Key gated. Poll /campaign/{id}/status: INSTALLING → CONFIRMING →
    AWAITING_COMMIT (then the operator commits)."""
    target = _coordinator_endpoint(device_id)
    try:
        r = com_client.check_for_campaign(target, req.campaign_id, req.version,
                                          scope=req.scope)
        return {"device": device_id, "target": target, **r}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"campaign start ({target}): {e}")


class CampaignDecide(BaseModel):
    campaign_id: str


@router.post("/{device_id}/commit", dependencies=[Depends(require_key)])
def commit(device_id: str, req: CampaignDecide) -> dict:
    """L4-C step 7: the operator audited the new software (all boards PROVISIONAL,
    status AWAITING_COMMIT) and COMMITS — V-UCM fans Confirm to every board → ACTIVE.
    Mutating → X-GS-Key gated."""
    target = _coordinator_endpoint(device_id)
    try:
        r = com_client.campaign_decide(target, req.campaign_id, rollback=False)
        return {"device": device_id, "target": target, "decision": "commit", **r}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"campaign commit ({target}): {e}")


@router.post("/{device_id}/rollback", dependencies=[Depends(require_key)])
def rollback(device_id: str, req: CampaignDecide) -> dict:
    """L4-C step 7: the operator REJECTS the new software — V-UCM fans Cancel to
    every board → ROLLBACK. Mutating → X-GS-Key gated."""
    target = _coordinator_endpoint(device_id)
    try:
        r = com_client.campaign_decide(target, req.campaign_id, rollback=True)
        return {"device": device_id, "target": target, "decision": "rollback", **r}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"campaign rollback ({target}): {e}")

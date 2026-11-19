"""UCM — the ECU-lifecycle plane (GS as the ara::com OTA client).

GS drives + observes the on-device UCM agent over com gRPC, the ECU-lifecycle view
that runs IN PARALLEL with the Mender transport plane. The OTA-client flow:
  1. Mender deployment lands the bits on the rig (the transport plane).
  2. GS calls UCM RequestUpdate over com (this router) → the rig's UCM FSM runs the
     AUTOSAR lifecycle (verify → SM session → stop → install → activate → restart →
     PHM-verify → ACTIVE/ROLLBACK).
  3. GS polls UcmProgress to follow the FSM state (the observability in Layer 3).

A rig is addressed by its com endpoint (<ip>:7700). <ip> is the device's REACHABLE address
(gs_api.addressing: remote_ip, then local_ip) so the caller names a device id, not an ip.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import com_client
from ..addressing import COM_PORT, NoReachableAddress, com_endpoint
from ..auth import require_key
from ..clients import mender_client
from ..settings import settings

router = APIRouter(prefix="/api/ucm", tags=["ucm"])


def _rig_endpoint(device_id: str) -> str:
    """Resolve a Mender device id → its com gRPC endpoint (<ip>:7700).
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


@router.get("/{device_id}/progress")
def progress(device_id: str) -> dict:
    """The latest UCM FSM lifecycle sample from the rig (the ECU plane)."""
    target = _rig_endpoint(device_id)
    try:
        return {"device": device_id, "target": target,
                "progress": com_client.get_progress(target)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"ucm progress ({target}): {e}")


class UcmUpdate(BaseModel):
    name: str                  # the app/FC name (partial) or "theia" (full)
    version: str
    kind: int = 0              # 0=SOFTWARE 1=CONFIG
    scope: int = 1             # 0=FULL 1=PARTIAL (an app FC)
    artifact_path: str = ""    # "" = the staged current release
    signature: str = ""


@router.post("/{device_id}/request-update", dependencies=[Depends(require_key)])
def request_update(device_id: str, req: UcmUpdate) -> dict:
    """Drive a UCM update on the rig (GS as the OTA client): hand UCM the manifest,
    kicking its AUTOSAR lifecycle. Mutating → X-GS-Key gated. Poll
    /ucm/{id}/progress to watch the FSM."""
    target = _rig_endpoint(device_id)
    try:
        r = com_client.request_update(
            target, req.name, req.version, kind=req.kind, scope=req.scope,
            artifact_path=req.artifact_path, signature=req.signature)
        return {"device": device_id, "target": target, **r}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"ucm request-update ({target}): {e}")

"""Planes — the Theia VENDORING catalog (our two MinIO distribution planes).

This is what distinguishes a Theia ground-station from a vanilla Mender UI: Mender
knows what's DEPLOYED; the planes know what's PUBLISHED + deployable.

  runtime plane (theia-runtime/<ver>-<distro>/)  — the platform: supervisor +
                                                   services .debs, per ABI. colony
                                                   factory-installs these (NOT OTA).
  app plane     (theia-apps/user-software/<fleet>/<app>/<ver>/)  — the user FC apps,
                                                   the day-2 Mender OTA delivery unit.

The bridge action — `POST /api/planes/apps/{...}/publish` — takes an app bundle from
the app plane (its .mender artifact), uploads it to the Mender GW, and (optionally)
creates a deployment to the app's fleet. That's the operator's one-click
"vendor this app version to the fleet".
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import require_key
from ..clients import mender_client, plane_client, resolve_fleet
from ..settings import settings

router = APIRouter(prefix="/api/planes", tags=["planes"])


def _app_requires_runtime(s, fleet: str, app: str, version: str) -> str:
    """The runtime an app version pins (its app-plane index.json requires_runtime).
    Empty = unpinned (arch-only). Read from the catalog (already walked)."""
    try:
        for a in plane_client(s).apps_catalog():
            if (a.get("fleet") == fleet and a.get("app") == app
                    and str(a.get("version")) == str(version)):
                return a.get("requires_runtime", "") or ""
    except Exception:  # noqa: BLE001
        pass
    return ""


def _device_base_version(m, device_id: str) -> str | None:
    """A device's installed base runtime — the colony base-state mirror tag
    `base_version` (the compatibility key)."""
    for d in m.devices():
        if d.get("id") == device_id:
            for a in d.get("attributes", []) or []:
                if a["name"] == "base_version":
                    v = a["value"]
                    return v[0] if isinstance(v, list) and v else v
    return None


@router.get("/runtime")
def runtime_plane() -> dict:
    """The runtime vendoring catalog — every published platform release (per ABI)."""
    s = settings()
    try:
        return {"plane": "runtime", "releases": plane_client(s).runtime_catalog()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"runtime plane: {e}")


@router.get("/apps")
def apps_plane() -> dict:
    """The app vendoring catalog — every published app version, keyed fleet/app/ver."""
    s = settings()
    try:
        cat = plane_client(s).apps_catalog()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"app plane: {e}")
    # group by fleet → app → versions for the UI's vendoring tree
    tree: dict[str, dict[str, list[dict]]] = {}
    for a in cat:
        if "_error" in a:
            continue
        fleet = a.get("fleet", "?")
        app = a.get("app", "?")
        tree.setdefault(fleet, {}).setdefault(app, []).append({
            "version": a.get("version"), "artifact": a.get("artifact"),
            # the pinned runtime dependency (no backward compat) — the deploy gate
            # + the Releases dependency graph read this.
            "requires_runtime": a.get("requires_runtime", ""),
            "key": a.get("_key"), "files": a.get("files", []),
        })
    return {"plane": "apps", "apps": cat, "tree": tree}


class PublishRequest(BaseModel):
    fleet: str
    app: str
    version: str
    deploy: bool = False           # also create a Mender deployment to the fleet
    deployment_name: str | None = None


@router.post("/apps/publish", dependencies=[Depends(require_key)])
def publish_app(req: PublishRequest) -> dict:
    """Vendor an app version: pull its .mender from the app plane → upload to the
    Mender GW → (optionally) deploy to the app's fleet group. The one-click bridge
    from "published in the catalog" to "rolling out to the fleet"."""
    s = settings()
    pc = plane_client(s)
    m = mender_client(s)
    key = f"user-software/{req.fleet}/{req.app}/{req.version}"
    artifact_name = f"{req.app}-{req.version}"
    mender_obj = f"{key}/{artifact_name}.mender"
    try:
        blob = pc.fetch("apps", mender_obj)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=404,
                            detail=f"app artifact not in plane ({mender_obj}): {e}")
    # upload the .mender to the Mender GW
    with tempfile.NamedTemporaryFile(suffix=".mender", delete=False) as tf:
        tf.write(blob)
        tmp = Path(tf.name)
    try:
        aid = m.upload_artifact(tmp, f"{req.app} {req.version} (ground-station)")
    except Exception as e:  # noqa: BLE001
        # uploading the same artifact twice is fine — surface but don't fail hard
        aid = None
        upload_note = str(e)
    else:
        upload_note = "uploaded"
    finally:
        tmp.unlink(missing_ok=True)

    result = {"artifact_name": artifact_name, "artifact_id": aid,
              "upload": upload_note, "fleet": req.fleet}
    if req.deploy:
        try:
            # the fleet IS the device_type — resolve device-by-device (no groups)
            devices = resolve_fleet(m, req.fleet)
            if not devices:
                raise HTTPException(
                    status_code=400,
                    detail=f"no devices in fleet '{req.fleet}' (device_type)")
            # ── runtime-compat gate (no backward compat) ─────────────────────
            # An app deploys ONLY onto a device whose installed base_version (the
            # colony mirror tag) == the app's requires_runtime. Mismatches are
            # blocked with the reason — "update the base first".
            need = _app_requires_runtime(s, req.fleet, req.app, req.version)
            blocked = []
            if need:
                # devices is a list of device IDs (resolve_fleet → list[str])
                inv = {did: _device_base_version(m, did) for did in devices}
                ok_devices = [did for did in devices if inv.get(did) == need]
                blocked = [{"device": did, "base_version": inv.get(did)}
                           for did in devices if inv.get(did) != need]
                devices = ok_devices
            if not devices:
                raise HTTPException(
                    status_code=409,
                    detail=f"runtime-incompatible: {req.app} {req.version} needs "
                           f"base '{need}', but no targeted device runs it. Update "
                           f"the base (colony) first. Blocked: {blocked}")
            name = req.deployment_name or f"{artifact_name}-{req.fleet}"
            dep_id = m.create_deployment(name, artifact_name, devices)
            result["deployment"] = {"id": dep_id, "name": name,
                                    "devices": len(devices)}
            if need:
                result["requires_runtime"] = need
                if blocked:
                    result["blocked"] = blocked
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"deploy: {e}")
    return result

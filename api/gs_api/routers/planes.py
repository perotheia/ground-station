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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..clients import mender_client, plane_client
from ..settings import settings

router = APIRouter(prefix="/api/planes", tags=["planes"])


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
            "key": a.get("_key"), "files": a.get("files", []),
        })
    return {"plane": "apps", "apps": cat, "tree": tree}


class PublishRequest(BaseModel):
    fleet: str
    app: str
    version: str
    deploy: bool = False           # also create a Mender deployment to the fleet
    deployment_name: str | None = None


@router.post("/apps/publish")
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
            devices = m.device_ids_in_group(req.fleet)
            if not devices:
                raise HTTPException(
                    status_code=400,
                    detail=f"no devices in fleet group '{req.fleet}'")
            name = req.deployment_name or f"{artifact_name}-{req.fleet}"
            dep_id = m.create_deployment(name, artifact_name, devices)
            result["deployment"] = {"id": dep_id, "name": name,
                                    "devices": len(devices)}
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"deploy: {e}")
    return result

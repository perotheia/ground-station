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
from ..colony_client import colony_client
from ..settings import settings

router = APIRouter(prefix="/api/planes", tags=["planes"])


def _deployed_versions(s) -> set[str]:
    """The set of release versions that have been DEPLOYED — i.e. LOCKED (UF: a
    deployed release is immutable; re-iterate = a new version). Union of every
    Mender deployment's artifact_name (app + runtime-as-artifact) and every colony
    deployment's runtime_version (base). A failing source just contributes nothing
    (lock is advisory — never blocks a read)."""
    import json
    locked: set[str] = set()
    try:
        m = mender_client(s)
        st, data, _ = m._req("GET", f"{m.dep}?per_page=200")  # noqa: SLF001
        if st == 200:
            for d in json.loads(data or b"[]"):
                an = d.get("artifact_name") or d.get("artifacts")
                if isinstance(an, list):
                    locked.update(str(x) for x in an)
                elif an:
                    locked.add(str(an))
    except Exception:  # noqa: BLE001
        pass
    try:
        for d in colony_client(s).deployments():
            rv = d.get("runtime_version")
            if rv:
                locked.add(str(rv))
    except Exception:  # noqa: BLE001
        pass
    return locked


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
    """The runtime vendoring catalog — every published platform release (per ABI),
    each marked `locked` once it's been deployed (UF immutability)."""
    s = settings()
    try:
        rel = plane_client(s).runtime_catalog()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"runtime plane: {e}")
    locked = _deployed_versions(s)
    pc = plane_client(s)
    for r in rel:
        if "_error" in r:
            continue
        key = r.get("key") or r.get("version")
        r["locked"] = key in locked or str(r.get("version")) in locked
        r["pinned"] = pc.runtime_is_pinned(key) if key else False
    return {"plane": "runtime", "releases": rel}


@router.get("/apps")
def apps_plane() -> dict:
    """The app vendoring catalog — every published app version, keyed fleet/app/ver."""
    s = settings()
    try:
        cat = plane_client(s).apps_catalog()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"app plane: {e}")
    # group by fleet → app → versions for the UI's vendoring tree
    pc = plane_client(s)
    locked = _deployed_versions(s)
    tree: dict[str, dict[str, list[dict]]] = {}
    for a in cat:
        if "_error" in a:
            continue
        fleet = a.get("fleet", "?")
        app = a.get("app", "?")
        ver = a.get("version")
        tree.setdefault(fleet, {}).setdefault(app, []).append({
            "version": ver, "artifact": a.get("artifact"),
            # the pinned runtime dependency (no backward compat) — the deploy gate
            # + the Releases dependency graph read this.
            "requires_runtime": a.get("requires_runtime", ""),
            # ARITY: how many machines this app spans (from the manifest
            # machines.json, recorded in app.json by release-app). roles = the
            # machine NAMES. arity 1 = single rig; 2 = central+compute split.
            # GS shows app/N; the per-role Distribution model uses the roles.
            "arity": a.get("arity", len(a.get("roles", []) or []) or 1),
            "roles": a.get("roles", []) or [],
            # operator pin (guards deletion) — a .pinned marker in the plane.
            "pinned": pc.is_pinned(fleet, app, str(ver)),
            # UF lock: a deployed artifact is immutable. The delete/overwrite ACT
            # disables when locked (re-iterate = a new version, not a clobber).
            "locked": str(a.get("artifact") or "") in locked or str(ver) in locked,
            "key": a.get("_key"), "files": a.get("files", []),
        })
    return {"plane": "apps", "apps": cat, "tree": tree}


@router.get("/roles")
def roles_plane() -> dict:
    """The ROLE artifact catalog — `theia release-role` <role>.mender bundles,
    grouped fleet → version → roles, each marked `locked` when deployed."""
    s = settings()
    try:
        cat = plane_client(s).roles_catalog()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"roles plane: {e}")
    locked = _deployed_versions(s)
    tree: dict[str, dict[str, list[dict]]] = {}
    for r in cat:
        if "_error" in r:
            continue
        fleet, ver = r.get("fleet", "?"), r.get("version", "?")
        tree.setdefault(fleet, {}).setdefault(ver, []).append({
            "role": r.get("role"), "key": r.get("key"), "size": r.get("size"),
            "locked": str(ver) in locked,
        })
    return {"plane": "roles", "roles": cat, "tree": tree}


# ── Distributions (UF concept): a bundle {app(s) + ABI-agnostic runtime} ─────
class DistributionApp(BaseModel):
    fleet: str
    app: str
    version: str


class DistributionRequest(BaseModel):
    name: str
    version: str
    runtime_version: str             # ABI-AGNOSTIC (e.g. "0.2.4"); resolved per rig
    apps: list[DistributionApp] = []


@router.get("/distributions")
def distributions_plane() -> dict:
    """Every Distribution bundle (name/version → runtime_version + apps)."""
    s = settings()
    try:
        return {"plane": "distributions", "distributions": plane_client(s).distributions_catalog()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"distributions plane: {e}")


@router.post("/distributions", dependencies=[Depends(require_key)])
def create_distribution(req: DistributionRequest) -> dict:
    """Create/overwrite a Distribution bundle in S3 (stateless — the plane is the
    source of truth)."""
    s = settings()
    try:
        key = plane_client(s).save_distribution(req.name, req.version, {
            "runtime_version": req.runtime_version,
            "apps": [a.model_dump() for a in req.apps],
        })
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"save distribution: {e}")
    return {"name": req.name, "version": req.version, "key": key}


class DistributionRef(BaseModel):
    name: str
    version: str


@router.delete("/distributions", dependencies=[Depends(require_key)])
def delete_distribution(req: DistributionRef) -> dict:
    s = settings()
    try:
        n = plane_client(s).delete_distribution(req.name, req.version)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"delete distribution: {e}")
    return {"name": req.name, "version": req.version, "deleted_objects": n}


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


# ── Releases ACT — pin / delete app-plane distributions ──────────────────────
class AppRef(BaseModel):
    fleet: str
    app: str
    version: str


class AppPinRequest(AppRef):
    pinned: bool


@router.post("/apps/pin", dependencies=[Depends(require_key)])
def pin_app(req: AppPinRequest) -> dict:
    """Pin/unpin an app distribution (a `.pinned` marker object in the plane). A
    pinned distribution is guarded from deletion — unpin first."""
    s = settings()
    try:
        plane_client(s).set_pin(req.fleet, req.app, req.version, req.pinned)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"pin: {e}")
    return {"fleet": req.fleet, "app": req.app, "version": req.version, "pinned": req.pinned}


@router.delete("/apps", dependencies=[Depends(require_key)])
def delete_app(req: AppRef) -> dict:
    """Delete an app distribution from the S3 app plane. GUARDED — a pinned
    distribution must be unpinned first (the destructive op is two-step)."""
    s = settings()
    pc = plane_client(s)
    if pc.is_pinned(req.fleet, req.app, req.version):
        raise HTTPException(status_code=409,
                            detail="distribution is pinned — unpin before deleting")
    try:
        n = pc.delete_app(req.fleet, req.app, req.version)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"delete: {e}")
    return {"fleet": req.fleet, "app": req.app, "version": req.version, "deleted_objects": n}


# ── Releases ACT — pin / delete RUNTIME-plane distributions (same model) ─────
class RuntimeRef(BaseModel):
    key: str                      # the runtime version dir, e.g. 0.2.4-bookworm-arm64


class RuntimePinRequest(RuntimeRef):
    pinned: bool


@router.post("/runtime/pin", dependencies=[Depends(require_key)])
def pin_runtime(req: RuntimePinRequest) -> dict:
    """Pin/unpin a runtime release (a `.pinned` marker object in the runtime
    plane). A pinned runtime is guarded from deletion. STATELESS — the pin lives
    in S3 next to the release, not in GS."""
    s = settings()
    try:
        plane_client(s).set_runtime_pin(req.key, req.pinned)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"runtime pin: {e}")
    return {"key": req.key, "pinned": req.pinned}


@router.delete("/runtime", dependencies=[Depends(require_key)])
def delete_runtime(req: RuntimeRef) -> dict:
    """Delete a runtime release from the S3 runtime plane. GUARDED — pinned (or
    deployed/locked) runtimes must be unpinned first. Two-step destructive op."""
    s = settings()
    pc = plane_client(s)
    if pc.runtime_is_pinned(req.key):
        raise HTTPException(status_code=409,
                            detail="runtime is pinned — unpin before deleting")
    try:
        n = pc.delete_runtime(req.key)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"delete runtime: {e}")
    return {"key": req.key, "deleted_objects": n}

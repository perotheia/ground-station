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
from ..clients import mender_client, plane_client, resolve_fleet
from ..colony_client import colony_client
from .devices import _flatten  # shared device flatten helper
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

    # Normalize the two planes timestamps to a comparable epoch: colony rows carry
    # a float epoch (created/created_ts), Mender rows an ISO-8601 string. A naive
    # string sort interleaves them wrong, so map both to seconds-since-epoch.
    def _epoch(d) -> float:
        c = d.get("created") or d.get("created_ts") or 0
        if isinstance(c, (int, float)):
            return float(c)
        try:
            from datetime import datetime
            return datetime.fromisoformat(str(c).replace("Z", "+00:00")).timestamp()
        except Exception:  # noqa: BLE001
            return 0.0
    rows.sort(key=_epoch, reverse=True)
    out: dict = {"deployments": rows, "count": len(rows)}
    if errors:
        out["errors"] = errors
    return out


@router.get("/rollouts")
def list_rollouts() -> dict:
    """Every named rollout entity on the package plane (theia-rollouts), newest
    first is not tracked (no timestamps) — returned in bucket order."""
    return {"plane": "rollouts", "rollouts": plane_client(settings()).rollouts_catalog()}


def _rollout_sw_compare(s, doc: dict) -> list[dict]:
    """Per-machine SW patch-compare for a rollout: read each board's installed
    current_version from per/etcd (via com PerView on the target's aggregator) and
    diff it against the rollout's to_version. Returns [{machine, current, target,
    at_target, campaign}] — which boards are already at the rollout's version and
    which are behind. Best-effort: an unreachable com yields []."""
    m = mender_client(s)
    tgt = doc.get("target", {}) or {}
    group, fleet = tgt.get("group"), tgt.get("fleet")
    devices = (resolve_fleet(m, fleet) if fleet
               else (m.device_ids_in_group(group) if group else []))
    # com on ANY target board aggregates the whole cluster's SW state — pick the
    # first reachable endpoint.
    by_id = {d.get("id"): d for d in m.devices()}
    endpoint = None
    for did in devices:
        endpoint = _com_endpoint(by_id.get(did))
        if endpoint:
            break
    if not endpoint:
        return []
    to_version = str(doc.get("to_version", ""))
    try:
        rows = com_client.sw_state(endpoint, timeout=6.0)
    except Exception:  # noqa: BLE001 — com not reachable
        return []
    out = []
    for r in rows:
        cur = r.get("current_version", "")
        out.append({"machine": r.get("machine", ""), "current": cur,
                    "target": to_version,
                    "at_target": bool(cur) and cur == to_version,
                    "in_flight_target": r.get("target_version", ""),
                    "campaign": r.get("campaign_id", ""),
                    "state": r.get("state_name", "")})
    return out


@router.get("/rollouts/{name}")
def get_rollout(name: str) -> dict:
    """A single named rollout entity (the live plan + status), plus a per-machine
    SW patch-compare (`sw`): each targeted board's installed current_version vs the
    rollout's to_version, so the operator sees which boards are already at target
    and which are behind. Best-effort — `sw` is [] when com is unreachable."""
    s = settings()
    doc = plane_client(s).fetch_rollout(name)
    if not doc:
        raise HTTPException(status_code=404, detail=f"no rollout '{name}'")
    doc["sw"] = _rollout_sw_compare(s, doc)
    return doc


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
    ip: str | None = None            # explicit target IP (preauth device w/ no
                                     # local_ip → the Deploy prompt supplies it)
    device_id: str | None = None     # the Mender device, to record local_ip on
    scope: dict | None = None        # cleanup SCOPE {app, runtime, mender} (bools);
                                     # None → colony defaults (app+runtime).


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
    # Per-device IP: if the operator supplied an IP (a preauth device with no
    # local_ip), record it as the device's local_ip tag (we DON'T assume it stays
    # reachable) and use it as the colony host override. Otherwise colony resolves
    # the host from the registry (legacy) — see project-preauth-device-workflow.
    if req.ip and req.device_id:
        try:
            mender_client(s).set_tags(req.device_id, {"local_ip": req.ip})
        except Exception:  # noqa: BLE001
            pass            # tag is best-effort; the deploy still uses the IP
    # cleanup scope → ansible clean_app/clean_runtime/clean_mender flags.
    extra = None
    if req.kind == "cleanup" and req.scope is not None:
        extra = {f"clean_{k}": bool(v) for k, v in req.scope.items()
                 if k in ("app", "runtime", "mender")}
    dep = col.create(req.rig, req.kind, req.schedule, host=req.ip, extra=extra)
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
    _scope = req.scope or {"app": True, "runtime": True}
    if req.kind == "cleanup" and ok and _scope.get("runtime", True):
        # the device was deprovisioned — drop the colony base-state tags so the
        # Fleet UI stops showing a stale base_version. Keep the operator tags
        # (name / local_ip / remote_ip / device_type); the PUT replaces ALL
        # tags, so re-set the keepers. (artifact_name is device-reported
        # inventory — it self-corrects to 'unprovisioned' on the device's next
        # mender-update inventory submit; we can't clear it from here.)
        try:
            m = mender_client(s)
            dev = _mender_device_for_rig(s, m, req.rig)
            if dev:
                # read the RAW tags (scope=tags); drop ONLY the base-state keys
                # colony mirrored, keep every other operator tag verbatim
                # (local_ip / remote_ip / name / device_type / group / ...).
                drop = {"base_version", "base_authority", "base_kind",
                        "base_deployed_at", "base_status"}
                keep = {}
                for a in dev.get("attributes", []) or []:
                    if a.get("scope") != "tags" or a.get("name") in drop:
                        continue
                    v = a.get("value")
                    keep[a["name"]] = v[0] if isinstance(v, list) and v else v
                m.set_tags(dev["id"], keep)
        except Exception:  # noqa: BLE001
            pass            # tag-clear is best-effort; cleanup already ran
    elif req.mirror and ok:
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
    machine = rig
    machine_instance = "0"
    try:
        rinfo = next((r for r in colony_client(s).rigs() if r.get("name") == rig), {})
        ver = rinfo.get("runtime_version") or "unknown"
        machine = rinfo.get("machine") or rig
        machine_instance = str(rinfo.get("machine_instance", "0"))
    except Exception:  # noqa: BLE001
        pass
    try:
        m.set_tags(dev["id"], {
            "base_version": ver,
            "base_authority": "colony",
            "base_kind": dep.get("kind", "orchestrate"),
            "base_deployed_at": str(int(time.time())),
            "base_status": "success",
            # the cluster identity — lets the Fleet tri-state match a Mender device
            # to a ListMachines entry by INSTANCE (robust to the m<N> name fallback).
            "machine": machine,
            "machine_instance": machine_instance,
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


# ── Phased rollouts (P6): a STATEFUL, NAMED entity on S3 (theia-rollouts) ───────
# A rollout is a phased UPGRADE (or downgrade) of an APP group from its installed
# SWP version to a new one. It is a named artifact — s3://theia-rollouts/<name>/
# index.json — so it survives a GS reload, is server-observable, and advance/abort
# key on the NAME (re-read the plan from S3). Rollouts are APP-PLANE ONLY: they
# reference an app SWP artifact (authority="app"); the base/runtime plane is
# re-provisioned by colony, never rolled.
def _semver_tuple(v: str) -> tuple:
    """<ver>[-<abi>] → numeric semver tuple (abi stripped). Non-numeric → (0,)."""
    core = (v or "").split("-", 1)[0]
    try:
        return tuple(int(x) for x in core.split("."))
    except ValueError:
        return (0,)


def _direction(frm: str, to: str) -> str:
    """upgrade | downgrade | same, from comparing the two SWP semvers."""
    a, b = _semver_tuple(frm), _semver_tuple(to)
    if b > a:
        return "upgrade"
    if b < a:
        return "downgrade"
    return "same"


def _swp_entry(s, app: str, version: str) -> dict | None:
    """The published SWP catalog row for <app>-<version> (abi-tolerant match), or
    None. Used to (a) prove the target is an APP artifact and (b) read its abi."""
    want = _semver_tuple(version)
    for row in plane_client(s).swp_catalog():
        if row.get("_error"):
            continue
        if row.get("app") != app:
            continue
        if _semver_tuple(row.get("version", "")) == want:
            return row
    return None


def _device_base_version(m, device_id: str) -> str | None:
    """A device's installed base runtime — the colony base-state mirror tag
    `base_version` (the runtime-compat key). Mirrors planes.py._device_base_version."""
    for d in m.devices():
        if d.get("id") == device_id:
            for a in d.get("attributes", []) or []:
                if a.get("name") == "base_version":
                    v = a.get("value")
                    return v[0] if isinstance(v, list) and v else v
    return None


class RolloutRequest(BaseModel):
    name: str                              # the rollout's IDENTITY (S3 key)
    app: str                               # the app SWP being rolled (app plane)
    to_version: str                        # the SWP version to roll the group TO
    from_version: str | None = None        # the installed version (for direction)
    artifact_name: str | None = None       # Mender artifact; default <app>-<to>
    group: str | None = None
    fleet: str | None = None
    direction: str | None = None           # auto (upgrade|downgrade) if omitted
    phases: int = 2                        # split the target into N sub-groups
    when: str = "now"                     # "now" | "scheduled" (create paused)


def _rollout_targets(m, req) -> list[str]:
    if req.fleet:
        return resolve_fleet(m, req.fleet)
    if req.group:
        return m.device_ids_in_group(req.group)
    return []


def _chunk(seq: list, n: int) -> list[list]:
    """Split into n near-equal contiguous chunks (sub-groups). n>=1."""
    n = max(1, min(n, len(seq) or 1))
    k, r = divmod(len(seq), n)
    out, i = [], 0
    for j in range(n):
        size = k + (1 if j < r else 0)
        out.append(seq[i:i + size]); i += size
    return [c for c in out if c]


@router.post("/rollouts", dependencies=[Depends(require_key)])
def create_rollout(req: RolloutRequest) -> dict:
    """Create a NAMED, STATEFUL rollout (UF Rollout, trimmed for a lab fleet):
    split the target group/fleet into N sequential sub-groups, deploy phase 1 NOW,
    and PERSIST the whole plan + status to s3://theia-rollouts/<name>/index.json.
    Subsequent phases launch via POST /rollouts/advance keyed on the name (the plan
    is re-read from S3 — it survives a GS reload). The operator gates progression
    (no percent thresholds / auto-halt; design §P6). `when=scheduled` builds the
    entity WITHOUT launching phase 1. APP PLANE ONLY — the target must be a
    published app SWP (authority="app"); base/runtime is re-provisioned, not rolled."""
    s = settings()
    m = mender_client(s)
    target = req.fleet or req.group
    if not target:
        raise HTTPException(status_code=400, detail="need a fleet or group to target")
    if not req.name:
        raise HTTPException(status_code=400, detail="a rollout needs a name")
    # Enforce app-plane: the target version must be a published app SWP. This is
    # what makes a rollout an APP upgrade/downgrade and never a base/runtime roll.
    swp = _swp_entry(s, req.app, req.to_version)
    if not swp:
        raise HTTPException(
            status_code=400,
            detail=(f"no published app SWP {req.app}-{req.to_version} on the "
                    "package plane — rollouts target apps only (release it first)"))
    if plane_client(s).fetch_rollout(req.name):
        raise HTTPException(status_code=409,
                            detail=f"rollout '{req.name}' already exists")
    artifact_name = req.artifact_name or f"{req.app}-{req.to_version}"
    direction = req.direction or _direction(req.from_version or "", req.to_version)
    try:
        devices = _rollout_targets(m, req)
        if not devices:
            raise HTTPException(status_code=400,
                                detail=f"no devices match '{target}'")
        # ── runtime-compat gate (no backward compat) ─────────────────────────
        # A rollout swaps the APP plane on a FIXED runtime. An app deploys ONLY
        # onto a device whose installed base_version == the app SWP's
        # requires_runtime; a mismatch means "update the base (colony) first". A
        # MAJOR SWP (major=true / migration set) is ALWAYS pinned, so this gate is
        # what enforces the migration's runtime dependency.
        need = (swp.get("requires_runtime") or "").strip()
        blocked = []
        if need:
            inv = {did: _device_base_version(m, did) for did in devices}
            ok_devices = [did for did in devices if inv.get(did) == need]
            blocked = [{"device": did, "base_version": inv.get(did)}
                       for did in devices if inv.get(did) != need]
            devices = ok_devices
        if not devices:
            raise HTTPException(
                status_code=409,
                detail=(f"runtime-incompatible: {req.app} {req.to_version} needs "
                        f"base '{need}', but no targeted device runs it. Update the "
                        f"base (colony) first. Blocked: {blocked}"))
        chunks = _chunk(devices, req.phases)
        plan = [{"phase": i + 1, "devices": c, "count": len(c),
                 "name": f"{req.name}-p{i + 1}", "deployment_id": None,
                 "status": "queued"}
                for i, c in enumerate(chunks)]
        launched = None
        if req.when != "scheduled" and plan:
            plan[0]["deployment_id"] = m.create_deployment(
                plan[0]["name"], artifact_name, plan[0]["devices"])
            plan[0]["status"] = "deploying"
            launched = plan[0]["deployment_id"]
        status = "in_progress" if launched else "scheduled"
        doc = {"app": req.app, "authority": "app",
               "from_version": req.from_version or "",
               "to_version": req.to_version, "direction": direction,
               "artifact_name": artifact_name,
               "target": {"group": req.group, "fleet": req.fleet},
               "abi": swp.get("abi", ""),
               "requires_runtime": need,
               "migration": swp.get("migration", ""),
               "major": bool(swp.get("major", False)),
               "blocked": blocked,
               "phases": plan, "total_devices": len(devices),
               "status": status, "current_phase": 1 if launched else 0}
        # Persist the entity — advance/abort re-read + re-save this same key.
        plane_client(s).save_rollout(req.name, doc)
        return {"name": req.name, **doc, "launched": launched}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"create rollout: {e}")


class AdvanceRequest(BaseModel):
    name: str                       # the rollout's name (S3 key) — the state lives there


@router.post("/rollouts/advance", dependencies=[Depends(require_key)])
def advance_rollout(req: AdvanceRequest) -> dict:
    """Launch the NEXT phase of a NAMED rollout — the operator-gated step. The
    plan lives in S3 (not the client): we re-read s3://theia-rollouts/<name>/, find
    the first phase still `queued`, create its Mender deployment, mark it
    `deploying`, and re-save. When the last phase launches the rollout status
    becomes `completed`. Idempotent-ish: a rollout with no queued phase 400s."""
    s = settings()
    m = mender_client(s)
    pc = plane_client(s)
    doc = pc.fetch_rollout(req.name)
    if not doc:
        raise HTTPException(status_code=404, detail=f"no rollout '{req.name}'")
    if doc.get("status") == "aborted":
        raise HTTPException(status_code=409, detail="rollout is aborted")
    phases = doc.get("phases", [])
    nxt = next((p for p in phases if p.get("status") == "queued"), None)
    if not nxt:
        raise HTTPException(status_code=400, detail="no queued phase to advance")
    if not nxt.get("devices"):
        raise HTTPException(status_code=400, detail="phase has no devices")
    try:
        dep_id = m.create_deployment(nxt["name"], doc["artifact_name"], nxt["devices"])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"advance rollout: {e}")
    nxt["deployment_id"] = dep_id
    nxt["status"] = "deploying"
    doc["current_phase"] = nxt["phase"]
    remaining = [p for p in phases if p.get("status") == "queued"]
    doc["status"] = "completed" if not remaining else "in_progress"
    pc.save_rollout(req.name, doc)
    return {"name": req.name, "deployment_id": dep_id, "phase": nxt["phase"],
            "count": len(nxt["devices"]), "status": doc["status"]}


@router.post("/rollouts/{name}/abort", dependencies=[Depends(require_key)])
def abort_rollout(name: str) -> dict:
    """Abort a NAMED rollout: abort every phase that has an in-flight Mender
    deployment, mark the entity `aborted`, and re-save. Queued phases just stop."""
    s = settings()
    m = mender_client(s)
    pc = plane_client(s)
    doc = pc.fetch_rollout(name)
    if not doc:
        raise HTTPException(status_code=404, detail=f"no rollout '{name}'")
    aborted = []
    for p in doc.get("phases", []):
        did = p.get("deployment_id")
        if did and p.get("status") in ("deploying", "queued"):
            try:
                m.abort_deployment(did)
                aborted.append(did)
            except Exception:  # noqa: BLE001 — best-effort; already-finished 4xxs
                pass
        if p.get("status") in ("queued", "deploying"):
            p["status"] = "aborted"
    doc["status"] = "aborted"
    pc.save_rollout(name, doc)
    return {"name": name, "aborted_deployments": aborted, "status": "aborted"}


@router.delete("/rollouts/{name}", dependencies=[Depends(require_key)])
def delete_rollout(name: str) -> dict:
    """Delete a rollout entity from S3 (does NOT touch already-launched Mender
    deployments — abort those first if in-flight)."""
    n = plane_client(settings()).delete_rollout(name)
    if not n:
        raise HTTPException(status_code=404, detail=f"no rollout '{name}'")
    return {"name": name, "deleted": n}


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


# ── Distribution one-click deploy (UF concept) ───────────────────────────────
# A Distribution = {app(s) + ABI-agnostic runtime version}. Deploying it to a
# rig: (1) resolve the runtime version to the rig's arch/os build, (2) base-deploy
# that runtime via colony, (3) app-deploy each app via Mender. One click, two
# authorities, the requires_runtime gate satisfied by construction.

def _rig_abi(dev: dict) -> str:
    """Infer a rig's runtime ABI suffix (e.g. 'bookworm-arm64', 'focal-arm64')
    from its inventory attrs (os + kernel/arch). Best-effort string match."""
    attrs = dev.get("attributes", {}) or {}
    osr = (attrs.get("os") or "").lower()
    kern = (attrs.get("kernel") or "").lower()
    arch = "arm64" if ("aarch64" in kern or "arm64" in kern or "arm64" in osr) else            ("amd64" if ("x86_64" in kern or "amd64" in kern) else "")
    # distro family
    if "focal" in osr or "20.04" in osr: distro = "focal"
    elif "bookworm" in osr or "debian gnu/linux 12" in osr: distro = "bookworm"
    elif "trixie" in osr or "debian gnu/linux 13" in osr: distro = "bookworm"  # trixie≈bookworm-arm64 build for now
    elif "ubuntu" in osr and ("24" in osr): distro = "ubuntu24"
    else: distro = ""
    return f"{distro}-{arch}".strip("-")


def _resolve_runtime(s, version: str, abi: str) -> str | None:
    """Find the runtime-plane build whose key is <version>-<abi> (or just version
    if no abi). Returns the key, or None if unavailable."""
    try:
        keys = [r.get("key") or r.get("version") for r in plane_client(s).runtime_catalog()
                if "_error" not in r]
    except Exception:  # noqa: BLE001
        keys = []
    want = f"{version}-{abi}" if abi else version
    if want in keys:
        return want
    # fallback: any key starting with the version + matching the abi suffix
    for k in keys:
        if k and k.startswith(version + "-") and (not abi or k.endswith(abi)):
            return k
    return None


class RoleAssignment(BaseModel):
    role: str                        # the distribution role (central / compute)
    device_id: str                   # the machine assigned to this role
    ip: str | None = None            # deploy IP if the device has no reachable_ip


class DistDeployRequest(BaseModel):
    name: str                        # distribution name
    version: str                     # distribution version
    assignments: list[RoleAssignment] = []   # role → machine (one per role)


@router.post("/distribution", dependencies=[Depends(require_key)])
def deploy_distribution(req: DistDeployRequest) -> dict:
    """Deploy a Distribution: for EACH role, validate the assigned machine's abi
    matches the role, then deploy that role's runtime_build (colony) + app_build
    (Mender) to its machine. One logical deploy, N coordinated per-role actions.
    Compatibility is enforced (machine.abi == role.abi) — the strict ER."""
    s = settings()
    m = mender_client(s)
    dist = next((d for d in plane_client(s).distributions_catalog()
                 if d.get("name") == req.name and str(d.get("version")) == str(req.version)), None)
    if not dist:
        raise HTTPException(status_code=404, detail=f"distribution {req.name}:{req.version} not found")
    roles = {r["role"]: r for r in dist.get("roles", [])}
    if not roles:
        raise HTTPException(status_code=400, detail="distribution has no roles")
    if len(req.assignments) != len(roles):
        raise HTTPException(status_code=400,
            detail=f"need a machine for each of {len(roles)} role(s): {sorted(roles)}")
    # devices by id (for abi + ip)
    devs = {d.get("id"): _flatten(d) for d in m.devices()}
    result = {"distribution": f"{req.name}:{req.version}", "steps": []}
    # validate ALL assignments first (fail before any deploy) — abi must match.
    for a in req.assignments:
        role = roles.get(a.role)
        if not role:
            raise HTTPException(status_code=400, detail=f"unknown role {a.role}")
        dev = devs.get(a.device_id)
        if not dev:
            raise HTTPException(status_code=404, detail=f"device {a.device_id} not found")
        m_abi = _rig_abi(dev)
        if role.get("abi") and m_abi and m_abi != role["abi"]:
            raise HTTPException(status_code=409,
                detail=f"role {a.role} needs abi {role['abi']}, but {dev.get('name')} is {m_abi}")
    # all valid → deploy per role. The ORCHESTRATION role (master|zonal) is the
    # runtime slice colony pulls from S3 — distinct from the Distribution role
    # NAME. Model: exactly ONE master (the etcd/coordinator = assignment index 0),
    # the rest zonal workers. machine_instance = the assignment index (master=0,
    # workers=1,2,…) → the supervisor's per-board TIPC instance shift.
    for _i, a in enumerate(req.assignments):
        role = roles[a.role]
        dev = devs[a.device_id]
        rig = (dev.get("attributes", {}) or {}).get("machine") or dev.get("name")
        ip = a.ip or dev.get("reachable_ip")
        orch_role = "master" if _i == 0 else "zonal"
        step = {"role": a.role, "orch_role": orch_role, "device": a.device_id,
                "rig": rig}
        # 1) base: the role's runtime_build via colony (registry-free: host from
        #    Mender, role picks the S3 manifest slice, machine_instance = index).
        try:
            dep = colony_client(s).create(
                rig, "orchestrate", host=ip,
                extra={"role": orch_role, "machine_instance": _i,
                       "runtime_version": role["runtime_build"]})
            if ip:
                try: m.set_tags(a.device_id, {"local_ip": ip, "base_version": role["runtime_build"]})
                except Exception: pass  # noqa: BLE001
            step["base"] = role["runtime_build"]; step["colony_id"] = dep.get("id")
        except Exception as e:  # noqa: BLE001
            step["base_error"] = str(e)
        # 2) SWP: the role's swp_build via Mender (to this one device)
        swp_build = role.get("swp_build") or role.get("app_build")
        if swp_build:
            try:
                depid = m.create_deployment(f"{req.name}-{a.role}-{swp_build}",
                                            swp_build, [a.device_id])
                step["swp"] = swp_build; step["app"] = swp_build; step["mender_id"] = depid
            except Exception as e:  # noqa: BLE001
                step["app_error"] = str(e)
        result["steps"].append(step)
    return result

class ClearActionsRequest(BaseModel):
    rig: str | None = None           # colony rig; None/"" = GLOBAL (all rigs)
    before: float | None = None      # epoch; app rows older are UI-hidden
                                     # (Mender owns its records — not deletable)

@router.post("/clear", dependencies=[Depends(require_key)])
def clear_actions(req: ClearActionsRequest) -> dict:
    """Clear FINISHED base actions from colony's journal + return a
    `cleared_before` epoch the UI uses to hide app (Mender) rows (Mender owns
    those records — the app side is a UI-only hide). GLOBAL by default (rig
    None/empty prunes EVERY rig): Action History is one shared surface, and a
    per-target clear orphaned history when a target was deleted. Pass a rig to
    scope the colony prune to it."""
    import time
    s = settings()
    pruned = 0
    try:
        # rig None/"" → prune all rigs (the colony prune treats a None rig as all).
        r = colony_client(s).prune(req.rig or None, finished_only=True)
        pruned = r.get("pruned", 0)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"clear (colony): {e}")
    return {"rig": req.rig, "global": not req.rig, "colony_pruned": pruned,
            "cleared_before": req.before or time.time()}


class CancelActionRequest(BaseModel):
    id: str                          # the deployment id (mender or colony)
    authority: str                   # "app" (Mender) | "base" (colony)

@router.post("/cancel", dependencies=[Depends(require_key)])
def cancel_action(req: CancelActionRequest) -> dict:
    """Cancel ONE in-flight action from the Action History — routed to the
    right authority: app→Mender abort_deployment (PUT status=aborted),
    base→colony abort (POST /deployments/{id}/abort). A pending SWP waiting on
    its runtime (the theia-swp gate) is cancelled this way; a finished action is
    a no-op. Returns the authority + id acted on."""
    s = settings()
    try:
        if req.authority == "base":
            colony_client(s).abort(req.id)
        else:                        # app / Mender (default)
            mender_client(s).abort_deployment(req.id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502,
                            detail=f"cancel ({req.authority}): {e}")
    return {"cancelled": req.id, "authority": req.authority}


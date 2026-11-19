"""Mender artifacts as the operator sees them (spec hil-app-ota G2a + the G3 runtime rule).

GET /api/artifacts lists every artifact uploaded to the Mender server, one summary row each:
name, app, version, abi, device types, signed, size, created, the theia kind (theia-swp /
theia-app / theia-release, from the update type) and the runtime the artifact needs.

The runtime requirement (G3). `theia release-swp` puts `requires_runtime` in the PAYLOAD
(requires_runtime.txt) and in the S3 plane index, but writes no Mender provides/depends/meta-data
beyond the device type: the Mender server cannot read it. So the requirement is, in order:
  1. a `requires_runtime` key in the artifact's Mender metadata (an update's meta_data, or an
     artifact provides/depends key ending in requires_runtime) — for a future release-swp;
  2. else the naming convention <app>-<ver>-<abi> (release-swp names every cross-built SWP so):
     the abi the device's base runtime must match. base_version = <ver>-<abi> (0.3.1-trixie-arm64).
An artifact whose name carries no abi (a host build, base-0.1.1-unsigned) declares nothing.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException

from ..clients import mender_client
from ..settings import settings

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])

THEIA_KINDS = ("theia-swp", "theia-app", "theia-release")
APP_KINDS = ("theia-swp", "theia-app")          # an application SWP: the runtime gate applies

# <app>-<semver>[-<rest>]; rest is the abi when it looks like one (trixie-arm64, amd64, ...)
_NAME_RE = re.compile(r"^(?P<app>.+?)-(?P<ver>\d+\.\d+(?:\.\d+)?)(?:-(?P<rest>.+))?$")
_ABI_RE = re.compile(r"^(?:[a-z][a-z0-9]*-)?(?:arm64|amd64|armv7|armhf|aarch64|x86_64|riscv64)$")


def parse_name(name: str) -> dict:
    """<app>-<ver>[-<abi>] -> {app, version, abi}; parts that are not there are None."""
    m = _NAME_RE.match(name or "")
    if not m:
        return {"app": None, "version": None, "abi": None}
    rest = m.group("rest")
    return {"app": m.group("app"), "version": m.group("ver"),
            "abi": rest if rest and _ABI_RE.match(rest) else None}


def split_runtime(v: str | None) -> tuple[str, str | None]:
    """A runtime key <ver>[-<abi>] (0.3.1-trixie-arm64) -> ("0.3.1", "trixie-arm64")."""
    v = (v or "").strip()
    ver, _, rest = v.partition("-")
    return ver, (rest if rest and _ABI_RE.match(rest) else None)


def _major_minor(ver: str) -> str:
    return ".".join(ver.split(".")[:2])


def _meta_requires_runtime(a: dict) -> str | None:
    """`requires_runtime` from the artifact's Mender metadata, if a release ever writes it there."""
    for u in a.get("updates") or []:
        md = u.get("meta_data") or {}
        if isinstance(md, dict) and md.get("requires_runtime"):
            return str(md["requires_runtime"])
    for key in ("artifact_provides", "artifact_depends"):
        for k, v in (a.get(key) or {}).items():
            if k.split(".")[-1] == "requires_runtime" and v:
                return str(v[0] if isinstance(v, list) else v)
    return None


def summarize(a: dict) -> dict:
    """One Mender artifact (the management API answer) -> the operator's row."""
    name = a.get("name") or ""
    types = [((u.get("type_info") or {}).get("type")) for u in a.get("updates") or []]
    types = [t for t in types if t]
    kind = next((t for t in types if t in THEIA_KINDS), None)
    parsed = parse_name(name)
    meta_rt = _meta_requires_runtime(a)
    requirement = None
    if meta_rt:
        ver, abi = split_runtime(meta_rt)
        requirement = {"runtime": meta_rt, "version": ver or None, "abi": abi or parsed["abi"],
                       "source": "metadata"}
    elif parsed["abi"]:
        requirement = {"runtime": None, "version": None, "abi": parsed["abi"], "source": "name"}
    return {
        "id": a.get("id"), "name": name, "app": parsed["app"], "version": parsed["version"],
        "abi": parsed["abi"], "kind": kind, "types": types,
        "device_types": list(a.get("device_types_compatible") or []),
        "signed": bool(a.get("signed")), "size": a.get("size"),
        "created": a.get("modified") or a.get("created"),
        "description": a.get("description") or "",
        "requires_runtime": meta_rt,
        # the G3 gate's input; only an app SWP is gated
        "requirement": requirement if kind in APP_KINDS else None,
    }


def list_summaries(m) -> list[dict]:
    rows = [summarize(a) for a in m.list_artifacts() or []]
    rows.sort(key=lambda r: str(r.get("created") or ""), reverse=True)
    return rows


def runtime_refusal(art: dict, device_name: str, base_version: str | None) -> str | None:
    """Why `device_name` (base runtime `base_version`) cannot take `art`, or None if it can."""
    req = art.get("requirement")
    if not req:
        return None
    need = req.get("runtime") or req.get("abi")
    if not base_version:
        return (f"{device_name} has no base runtime recorded (base_version is empty): "
                f"{art['name']} needs {need}; deploy a runtime first (Fleet, Deploy runtime)")
    ver, abi = split_runtime(base_version)
    if req.get("abi") and abi != req["abi"]:
        return (f"{device_name} runs base {base_version}, {art['name']} needs the {req['abi']} runtime: "
                f"abi mismatch; deploy a {req['abi']} runtime first (Fleet, Deploy runtime)")
    if req.get("version") and _major_minor(ver) != _major_minor(req["version"]):
        return (f"{device_name} runs base {base_version}, {art['name']} needs runtime {req['runtime']} "
                f"(major.minor {_major_minor(req['version'])}); deploy that runtime first")
    return None


@router.get("")
def artifacts() -> dict:
    """The Mender artifacts, newest first (what a rollout can deploy)."""
    try:
        return {"artifacts": list_summaries(mender_client(settings()))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"mender artifacts: {e}")

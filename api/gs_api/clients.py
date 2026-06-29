"""Upstream clients — the Mender Management API + the MinIO distribution planes.

The Mender client is REUSED verbatim from fleet/fleet.py (the OTA spine — one
implementation, CLI and API share it). The plane client is a thin S3 lister over
MinIO that reads the index.json each plane writes (theia release --runtime / -app).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from .settings import Settings

# ---- import the existing Mender client from fleet/fleet.py (no copy) ----------
# fleet.py lives at ground-station/fleet/fleet.py; load it as a module so the API
# and the CLI share ONE Mender Management API implementation.
_FLEET = Path(__file__).resolve().parents[2] / "fleet" / "fleet.py"


def _load_fleet():
    spec = importlib.util.spec_from_file_location("gs_fleet", _FLEET)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["gs_fleet"] = mod
    spec.loader.exec_module(mod)
    return mod


fleet = _load_fleet()
Mender = fleet.Mender  # the Management API client (urllib-only)


def mender_client(s: Settings):
    """A configured Mender Management API client (from fleet.py)."""
    return Mender(s.mender_server, s.mender_token,
                  insecure=s.mender_insecure, flavor=s.mender_flavor)


def _attr(dev: dict, name: str):
    for a in dev.get("attributes", []) or []:
        if a["name"] == name:
            v = a["value"]
            return v[0] if isinstance(v, list) and v else v
    return None


def _attr_raw(dev: dict, name: str):
    """The attribute value WITHOUT collapsing a multi-value list to its first
    element (unlike _attr). device_type often repeats across inventory scopes
    (e.g. ['theia-rig', 'theia-gateway']); fleet matching must see EVERY value,
    or a board that gained a second device_type is invisible to the new fleet."""
    for a in dev.get("attributes", []) or []:
        if a["name"] == name:
            return a["value"]
    return None


def resolve_fleet(m, fleet: str) -> list[str]:
    """A FLEET is a hardware-capability class == the Mender device_type. We deploy
    device-by-device (no Mender groups): resolve the fleet to the ids of every
    enrolled device whose device_type matches. The deployment API takes a device
    list, so this is the targeting primitive for both first-install + increments."""
    out = []
    for d in m.devices():
        dt = _attr_raw(d, "device_type")   # raw — keep ALL device_type values
        # device_type can repeat across scopes; match if any value equals the fleet
        vals = dt if isinstance(dt, list) else [dt]
        if fleet in [str(x) for x in (vals or [])] or dt == fleet:
            out.append(d["id"])
    return out


# ---- MinIO distribution planes (S3) -------------------------------------------
# boto3 is the only heavy dep; isolate it so the rest of the API imports without it.
class PlaneClient:
    """Reads the two distribution planes (theia-runtime, theia-apps) from MinIO.

    Each plane is keyed by an index.json that `theia release` writes:
      runtime:  theia-runtime/<ver>[-<distro>]/index.json   {version, distro, debs[]}
      apps:     theia-apps/user-software/<fleet>/<app>/<ver>/index.json
                                                            {fleet, app, version, files[]}
    The catalog = walk the bucket for index.json objects and parse them. This is
    the VENDORING view (what's publishable), distinct from Mender's deployed view.
    """

    def __init__(self, s: Settings):
        import boto3  # noqa: PLC0415 — isolate the heavy import
        from botocore.config import Config
        self._s3 = boto3.client(
            "s3", endpoint_url=s.s3_endpoint,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            config=Config(signature_version="s3v4"), region_name="us-east-1")
        self._runtime = s.s3_runtime_bucket
        self._apps = s.s3_apps_bucket
        self._roles = s.s3_roles_bucket

    def _indexes(self, bucket: str) -> list[dict]:
        out: list[dict] = []
        try:
            paginator = self._s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket):
                for obj in page.get("Contents", []) or []:
                    key = obj["Key"]
                    if key.endswith("index.json"):
                        body = self._s3.get_object(Bucket=bucket, Key=key)["Body"].read()
                        try:
                            d = json.loads(body)
                            d["_key"] = key
                            out.append(d)
                        except json.JSONDecodeError:
                            continue
        except Exception as e:  # noqa: BLE001 — surface a partial catalog, not a 500
            out.append({"_error": str(e), "_bucket": bucket})
        return out

    def runtime_catalog(self) -> list[dict]:
        """Every published runtime release: {version, distro, key, debs[]}."""
        return [i for i in self._indexes(self._runtime) if i.get("plane") == "runtime"
                or "debs" in i or "_error" in i]

    def apps_catalog(self) -> list[dict]:
        """Every published app: {fleet, app, version, artifact, files[]}."""
        return [i for i in self._indexes(self._apps) if i.get("plane") == "app"
                or "app" in i or "_error" in i]

    def roles_catalog(self) -> list[dict]:
        """Every published ROLE artifact. The roles plane has NO index.json — it's
        a tree of `<fleet>/<version>/<role>.mender` (theia release-role). Walk for
        the .mender objects and derive {fleet, version, role, key, size}."""
        out: list[dict] = []
        try:
            paginator = self._s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._roles):
                for obj in page.get("Contents", []) or []:
                    key = obj["Key"]
                    if not key.endswith(".mender"):
                        continue
                    parts = key.split("/")
                    # <fleet>/<version>/<role>.mender
                    fleet = parts[0] if len(parts) >= 3 else ""
                    version = parts[1] if len(parts) >= 3 else ""
                    role = parts[-1][:-len(".mender")]
                    out.append({"plane": "role", "fleet": fleet, "version": version,
                                "role": role, "key": key, "size": obj.get("Size")})
        except Exception as e:  # noqa: BLE001
            out.append({"_error": str(e), "_bucket": self._roles})
        return out

    def presign(self, bucket_kind: str, key: str, expires: int = 3600) -> str:
        bucket = self._runtime if bucket_kind == "runtime" else self._apps
        return self._s3.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=expires)

    def fetch(self, bucket_kind: str, key: str) -> bytes:
        bucket = self._runtime if bucket_kind == "runtime" else self._apps
        return self._s3.get_object(Bucket=bucket, Key=key)["Body"].read()

    # ── plane management (Releases ACT: pin/delete) ──────────────────────────
    def app_prefix(self, fleet: str, app: str, version: str) -> str:
        return f"user-software/{fleet}/{app}/{version}/"

    def is_pinned(self, fleet: str, app: str, version: str) -> bool:
        try:
            self._s3.head_object(Bucket=self._apps,
                                 Key=self.app_prefix(fleet, app, version) + ".pinned")
            return True
        except Exception:  # noqa: BLE001
            return False

    def set_pin(self, fleet: str, app: str, version: str, pinned: bool) -> None:
        key = self.app_prefix(fleet, app, version) + ".pinned"
        if pinned:
            self._s3.put_object(Bucket=self._apps, Key=key, Body=b"1")
        else:
            self._s3.delete_object(Bucket=self._apps, Key=key)

    def delete_app(self, fleet: str, app: str, version: str) -> int:
        """Delete every object under the app version's prefix. Returns the count."""
        pfx = self.app_prefix(fleet, app, version)
        paginator = self._s3.get_paginator("list_objects_v2")
        keys = [o["Key"] for page in paginator.paginate(Bucket=self._apps, Prefix=pfx)
                for o in page.get("Contents", [])]
        for k in keys:
            self._s3.delete_object(Bucket=self._apps, Key=k)
        return len(keys)


def plane_client(s: Settings) -> PlaneClient:
    return PlaneClient(s)

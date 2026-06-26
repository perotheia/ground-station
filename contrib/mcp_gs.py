#!/usr/bin/env python3
"""mcp_gs.py — an MCP server exposing the GS web API as agent tools.

So an agent can drive fleet ops directly: see what's enrolled, what's published,
roll an app version out to a fleet, and watch the deployment — all through the GS
WEB API (never Mender/MinIO directly; the backend holds the credentials). The
mutating tools present X-GS-Key.

This is the agent face of the same surface the web UI and tools/gs.py use. Keep it
thin — it's a 1:1 mapping to /api endpoints, so it never drifts from the backend.

Run (stdio transport, for an agent/MCP client):
  GS_API=http://10.0.0.99:8090 GS_API_KEY=... python contrib/mcp_gs.py

Deps: fastmcp (pip install fastmcp). The HTTP client is urllib (no requests dep).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from fastmcp import FastMCP

GS_API = os.environ.get("GS_API", "http://localhost:8088").rstrip("/")
GS_API_KEY = os.environ.get("GS_API_KEY", "")

mcp = FastMCP("theia-ground-station")


def _req(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{GS_API}/api{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if GS_API_KEY:
        req.add_header("X-GS-Key", GS_API_KEY)
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except json.JSONDecodeError:
            pass
        return {"error": f"{e.code}: {detail}"}
    except urllib.error.URLError as e:
        return {"error": f"cannot reach {GS_API}: {e.reason}"}


@mcp.tool()
def list_devices() -> dict:
    """List enrolled rigs in the fleet: id, fleet (hardware class), running
    artifact, group, and Theia health/SM-state/UCM-version if reported."""
    return _req("GET", "/devices")


@mcp.tool()
def list_apps() -> dict:
    """The app vendoring catalog — every published app version, grouped by
    fleet → app → versions (what can be deployed day-2)."""
    return _req("GET", "/planes/apps")


@mcp.tool()
def list_runtime() -> dict:
    """The runtime vendoring catalog — published platform releases (per ABI/distro),
    what colony factory-installs."""
    return _req("GET", "/planes/runtime")


@mcp.tool()
def list_deployments() -> dict:
    """The OTA rollout history (Mender deployments)."""
    return _req("GET", "/deployments")


@mcp.tool()
def deployment_status(deployment_id: str) -> dict:
    """Status + per-device statistics for one deployment."""
    return _req("GET", f"/deployments/{deployment_id}")


@mcp.tool()
def deploy_app(fleet: str, app: str, version: str, deploy: bool = True) -> dict:
    """Publish an app version to the Mender GW and (if deploy) roll it out to the
    fleet (= device_type). Returns the artifact + deployment id. Mutating — needs
    GS_API_KEY. Set deploy=false to upload to the GW without rolling out."""
    return _req("POST", "/planes/apps/publish",
                {"fleet": fleet, "app": app, "version": version, "deploy": deploy})


if __name__ == "__main__":
    mcp.run()

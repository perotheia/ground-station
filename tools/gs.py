#!/usr/bin/env python3
"""gs.py — scriptable client of the GS WEB API (the scriptable face of the UI).

This is the CI / dev / operator-script entry point. It calls the SAME /api the web
UI calls — it does NOT talk to Mender or MinIO directly (that's the backend's job,
which holds the credentials). A mutating call presents X-GS-Key (the deploy
capability), never the Mender PAT.

This is NOT colony. colony does the factory runtime+services install. The USER
installs + updates software, via the web UI or this script (or the MCP in contrib/).

Usage:
  gs.py [--api URL] [--key KEY] <command> [args]

  devices                          list enrolled rigs (fleet/artifact/health)
  apps                             the app vendoring catalog (fleet/app/versions)
  runtime                          the runtime vendoring catalog
  deployments                      rollout history
  status <dep_id>                  one deployment's status + statistics
  deploy-app <fleet> <app> <ver>   publish + deploy an app version to a fleet
        [--no-deploy]                 just upload to the GW, don't deploy
        [--wait]                      poll until the deployment finishes

Env: GS_API (default http://localhost:8088), GS_API_KEY (the X-GS-Key).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


class GS:
    def __init__(self, base: str, key: str = ""):
        self.base = base.rstrip("/")
        self.key = key

    def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{self.base}/api{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.key:
            req.add_header("X-GS-Key", self.key)
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            try:
                detail = json.loads(detail).get("detail", detail)
            except json.JSONDecodeError:
                pass
            raise SystemExit(f"gs: {method} {path} → {e.code}: {detail}")
        except urllib.error.URLError as e:
            raise SystemExit(f"gs: cannot reach {self.base} ({e.reason})")

    def devices(self):
        return self._req("GET", "/devices")

    def apps(self):
        return self._req("GET", "/planes/apps")

    def runtime(self):
        return self._req("GET", "/planes/runtime")

    def deployments(self):
        return self._req("GET", "/deployments")

    def deployment(self, dep_id):
        return self._req("GET", f"/deployments/{dep_id}")

    def publish(self, fleet, app, version, deploy):
        return self._req("POST", "/planes/apps/publish",
                         {"fleet": fleet, "app": app, "version": version, "deploy": deploy})


def _client(args) -> GS:
    base = args.api or os.environ.get("GS_API", "http://localhost:8088")
    key = args.key or os.environ.get("GS_API_KEY", "")
    return GS(base, key)


def cmd_devices(g, _a):
    d = g.devices()
    print(f"# {d['count']} device(s)  fleets={d['fleets']}")
    for dev in d["devices"]:
        print(f"  {str(dev['id'])[:12]}  fleet={dev['fleet']}  "
              f"artifact={dev['artifact']}  health={dev.get('health') or '-'}")


def cmd_apps(g, _a):
    tree = g.apps().get("tree", {})
    for fleet, apps in tree.items():
        print(f"# fleet {fleet}")
        for app, vers in apps.items():
            print(f"  {app}: {', '.join(v['version'] for v in vers)}")


def cmd_runtime(g, _a):
    for r in g.runtime().get("releases", []):
        if "_error" in r:
            continue
        print(f"  {r.get('version')}  distro={r.get('distro')}  key={r.get('_key')}")


def cmd_deployments(g, _a):
    for d in g.deployments().get("deployments", []):
        print(f"  {d.get('id', '')[:12]}  {d.get('name')}  "
              f"{d.get('status')}  artifact={d.get('artifact_name')}")


def cmd_status(g, a):
    d = g.deployment(a.dep_id)
    dep = d.get("deployment", {})
    print(f"{dep.get('name')}: {dep.get('status')}  artifact={dep.get('artifact_name')}")
    print(f"  statistics: {json.dumps(d.get('statistics', {}))}")


def cmd_deploy_app(g, a):
    deploy = not a.no_deploy
    r = g.publish(a.fleet, a.app, a.version, deploy)
    print(f"[gs] {r.get('upload')}  artifact={r.get('artifact_name')}")
    dep = r.get("deployment")
    if dep:
        print(f"[gs] deployment {dep['id'][:12]} → {dep['devices']} device(s) in '{a.fleet}'")
        if a.wait:
            _wait(g, dep["id"])
    elif deploy:
        print("[gs] no deployment created (no matching devices?)")


def _wait(g, dep_id, timeout=600):
    print(f"[gs] waiting on deployment {dep_id[:12]}…")
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        d = g.deployment(dep_id)
        st = d.get("deployment", {}).get("status")
        if st != last:
            print(f"  status: {st}  {json.dumps(d.get('statistics', {}).get('status', {}))}")
            last = st
        if st in ("finished", "aborted"):
            return
        time.sleep(5)
    print("[gs] wait timed out")


def main() -> int:
    p = argparse.ArgumentParser(prog="gs", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", help="GS API base URL ($GS_API)")
    p.add_argument("--key", help="X-GS-Key ($GS_API_KEY)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("devices").set_defaults(fn=cmd_devices)
    sub.add_parser("apps").set_defaults(fn=cmd_apps)
    sub.add_parser("runtime").set_defaults(fn=cmd_runtime)
    sub.add_parser("deployments").set_defaults(fn=cmd_deployments)
    sp = sub.add_parser("status"); sp.add_argument("dep_id"); sp.set_defaults(fn=cmd_status)
    sp = sub.add_parser("deploy-app")
    sp.add_argument("fleet"); sp.add_argument("app"); sp.add_argument("version")
    sp.add_argument("--no-deploy", action="store_true", help="upload only")
    sp.add_argument("--wait", action="store_true", help="poll until finished")
    sp.set_defaults(fn=cmd_deploy_app)
    args = p.parse_args()
    g = _client(args)
    args.fn(g, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())

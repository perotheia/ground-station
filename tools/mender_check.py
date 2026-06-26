#!/usr/bin/env python3
"""mender_check.py — verify a GS action landed, by talking to Mender DIRECTLY.

GS drives Mender; this is the INDEPENDENT auditor. It bypasses GS and asks the
Mender Management API (with the PAT) the questions "did the artifact get uploaded?",
"was the deployment created?", "did it finish?" — so a CI gate / operator can
confirm the GS path worked without trusting GS's own report. It reuses fleet.py's
Mender client (the same Management API surface), so there's one client, not two.

Usage:
  mender_check.py [--server URL] [--token PAT] [--insecure] <command> [args]

  artifact <name>            assert an artifact named <name> exists (exit 0/1)
  deployment <name>          show + assert the latest deployment named <name>
  finished <name>            exit 0 only if the latest <name> deployment is finished
  devices [--fleet TYPE]     list devices (optionally a device_type)

Env: MENDER_SERVER, MENDER_TOKEN (a PAT), MENDER_INSECURE.
Exit code is the assertion result (0 ok / 1 fail) so it drops into a CI gate.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

# reuse the Mender client from fleet/fleet.py (one Management API implementation)
_FLEET = Path(__file__).resolve().parents[1] / "fleet" / "fleet.py"
_spec = importlib.util.spec_from_file_location("gs_fleet", _FLEET)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
Mender = _mod.Mender


def _client(a):
    server = a.server or os.environ.get("MENDER_SERVER", "https://localhost")
    token = a.token or os.environ.get("MENDER_TOKEN", "")
    if not token:
        sys.exit("mender_check: no token (--token or $MENDER_TOKEN, a Mender PAT)")
    insecure = a.insecure or os.environ.get("MENDER_INSECURE", "1") not in ("0", "false", "")
    return Mender(server, token, insecure=insecure, flavor=a.api_flavor)


def _deployments(m, name=None):
    st, data, _ = m._req("GET", f"{m.dep}?per_page=100")  # noqa: SLF001
    if st != 200:
        sys.exit(f"mender_check: deployments [{st}]")
    deps = json.loads(data or b"[]")
    if name:
        deps = [d for d in deps if d.get("name") == name or d.get("artifact_name") == name]
    return deps


def cmd_artifact(m, a):
    arts = m.list_artifacts()
    hit = [x for x in arts if x.get("name") == a.name]
    if hit:
        print(f"OK  artifact '{a.name}' present "
              f"(compatible={hit[0].get('device_types_compatible')}, size={hit[0].get('size')})")
        return 0
    print(f"FAIL  artifact '{a.name}' NOT on the GW", file=sys.stderr)
    return 1


def cmd_deployment(m, a):
    deps = _deployments(m, a.name)
    if not deps:
        print(f"FAIL  no deployment '{a.name}'", file=sys.stderr)
        return 1
    d = deps[0]
    stats = m.deployment_statistics(d["id"])
    print(f"OK  deployment '{d.get('name')}' status={d.get('status')} "
          f"artifact={d.get('artifact_name')} stats={json.dumps(stats)}")
    return 0


def cmd_finished(m, a):
    deps = _deployments(m, a.name)
    if not deps:
        print(f"FAIL  no deployment '{a.name}'", file=sys.stderr)
        return 1
    st = deps[0].get("status")
    if st == "finished":
        print(f"OK  '{a.name}' finished")
        return 0
    print(f"FAIL  '{a.name}' status={st} (not finished)", file=sys.stderr)
    return 1


def cmd_devices(m, a):
    for d in m.devices():
        attrs = {x["name"]: x["value"] for x in d.get("attributes", [])}
        dt = attrs.get("device_type")
        dtv = dt[0] if isinstance(dt, list) and dt else dt
        if a.fleet and dtv != a.fleet:
            continue
        print(f"  {d['id'][:12]}  device_type={dtv}  artifact={attrs.get('artifact_name')}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="mender_check", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--server"); p.add_argument("--token")
    p.add_argument("--insecure", action="store_true")
    p.add_argument("--api-flavor", default="oss", choices=["oss", "hosted"])
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("artifact"); sp.add_argument("name"); sp.set_defaults(fn=cmd_artifact)
    sp = sub.add_parser("deployment"); sp.add_argument("name"); sp.set_defaults(fn=cmd_deployment)
    sp = sub.add_parser("finished"); sp.add_argument("name"); sp.set_defaults(fn=cmd_finished)
    sp = sub.add_parser("devices"); sp.add_argument("--fleet"); sp.set_defaults(fn=cmd_devices)
    a = p.parse_args()
    return a.fn(_client(a), a)


if __name__ == "__main__":
    sys.exit(main())

"""Fakes for the two upstreams (Mender, colony-api) built from the recorded GS answers in
/bigdata/work/architect/ui-test/api/*.json, and an app factory that wires them in."""
from __future__ import annotations

import copy
import json
import os
import threading
import time
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("GS_API_KEY", "test-key")
os.environ.pop("TS_API_TOKEN", None)
os.environ.pop("TS_API_TOKEN_FILE", None)

FIXTURES = Path(os.environ.get("GS_UI_FIXTURES", "/bigdata/work/architect/ui-test/api"))
KEY = {"X-GS-Key": "test-key"}
GS_PUBKEY = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQDtestkey colony@gs"

_TAG_KEYS = {"name", "remote_ip", "local_ip", "base_version", "base_authority", "role", "rig_class",
             "machine", "machine_instance", "pinned", "description"}
_IDENTITY_KEYS = {"device_id", "mac", "status", "created_ts", "updated_ts"}

PROVISION_LOG = """\
PLAY [provision] ***************************************************************

TASK [Gathering Facts] *********************************************************
ok: [{rig}]

TASK [Include rig classification (debian | opkg)] *****************************
included: /colony/ansible/tasks/rig-class.yml for {rig}

TASK [Detect apt / opkg on the rig] ********************************************
changed: [{rig}]

TASK [Set rig_class] ***********************************************************
ok: [{rig}]

TASK [apt os_packages] *********************************************************
{apt}

TASK [Join the fleet tailnet] **************************************************
changed: [{rig}]

TASK [COLONY_VPN_IP {rig}] *****************************************************
ok: [{rig}] => {{
    "msg": "COLONY_VPN_IP {rig} {vpn}"
}}

TASK [Summary] *****************************************************************
ok: [{rig}] => {{
    "msg": "rig_class={cls}; skipped by design: {skips}"
}}

PLAY RECAP *********************************************************************
{rig}                 : ok=8    changed=2    unreachable=0    failed=0    skipped=1
"""

FAILED_LOG = """\
PLAY [provision] ***************************************************************

TASK [Gathering Facts] *********************************************************
ok: [{rig}]

TASK [Install the Mender client] ***********************************************
fatal: [{rig}]: FAILED! => {{"changed": false, "msg": "No package matching 'mender-client4' is available"}}

PLAY RECAP *********************************************************************
{rig}                 : ok=1    changed=0    unreachable=0    failed=1    skipped=0
"""


def provision_log(rig, vpn="100.64.0.9", cls="debian", skips="nothing"):
    apt = f"changed: [{rig}]" if cls == "debian" else f"skipping: [{rig}]"
    return PROVISION_LOG.format(rig=rig, vpn=vpn, cls=cls, skips=skips, apt=apt)


def _load(name):
    return json.loads((FIXTURES / name).read_text())


def _raw_device(flat: dict) -> dict:
    """A GS flattened device (the recorded /api/devices answer) back into Mender's inventory shape."""
    attrs = []
    for k, v in (flat.get("attributes") or {}).items():
        scope = "tags" if k in _TAG_KEYS else ("identity" if k in _IDENTITY_KEYS else "inventory")
        if isinstance(v, str) and ", " in v and (k.startswith(("ipv4", "ipv6", "mac_")) or k == "network_interfaces"):
            v = v.split(", ")
        attrs.append({"name": k, "value": v, "scope": scope})
    return {"id": flat["id"], "updated_ts": flat.get("updated_ts"), "attributes": attrs}


class FakeMender:
    """Mender Management API: deviceauth (auth_devices / accept) + inventory (devices / tags)."""

    def __init__(self, devices: list[dict] | None = None, auth: list[dict] | None = None):
        self.inv = {d["id"]: d for d in (devices or [])}
        self.auth = auth or []
        self.accepted: list[tuple[str, str]] = []
        self.tag_writes: list[tuple[str, dict]] = []
        self.dep = "/api/management/v1/deployments/deployments"
        self.art = "/api/management/v1/deployments/artifacts"

    @classmethod
    def from_fixtures(cls):
        flat = _load("devices_status_any.json")["devices"]
        devices = [_raw_device(f) for f in flat]
        auth = []
        for f in flat:
            idd = {"device_id": (f.get("attributes") or {}).get("device_id")}
            auth.append({"id": f["id"], "identity_data": idd, "status": f["auth_status"],
                         "auth_sets": [{"id": f["auth_set_id"], "identity_data": idd, "status": f["auth_status"]}]})
        return cls(devices, auth)

    def add_pending(self, device_id: str | None, dev_id: str | None = None, mac: str | None = None) -> dict:
        idd = {"device_id": device_id} if device_id else {"mac": mac}
        d = {"id": dev_id or str(uuid.uuid4()), "identity_data": idd, "status": "pending",
             "auth_sets": [{"id": str(uuid.uuid4()), "identity_data": idd, "status": "pending"}]}
        self.auth.append(d)
        return d

    # deviceauth
    def auth_devices(self):
        return copy.deepcopy(self.auth)

    def accept_device(self, device_id, auth_set_id):
        for d in self.auth:
            if d["id"] == device_id:
                for a in d["auth_sets"]:
                    if a["id"] == auth_set_id:
                        a["status"] = "accepted"
                        d["status"] = "accepted"
                        self.accepted.append((device_id, auth_set_id))
                        self.inv.setdefault(device_id, {"id": device_id, "attributes": [
                            {"name": "device_id", "value": d["identity_data"].get("device_id"),
                             "scope": "identity"}]})
                        return True
        raise RuntimeError("accept [404]")

    def find_by_identity(self, ident):
        return next((d for d in self.auth if d["identity_data"].get("device_id") == ident), None)

    # inventory
    def devices(self, group=None):
        return copy.deepcopy(list(self.inv.values()))

    def get_tags(self, device_id):
        d = self.inv.get(device_id) or {}
        return {a["name"]: a["value"] for a in d.get("attributes", []) if a.get("scope") == "tags"}

    def set_tags(self, device_id, tags, *, replace=False):
        if device_id not in self.inv:
            raise RuntimeError("set_tags [404]")
        self.tag_writes.append((device_id, dict(tags)))
        d = self.inv[device_id]
        keep = [a for a in d["attributes"] if a.get("scope") != "tags" or (not replace and a["name"] not in tags)]
        d["attributes"] = keep + [{"name": k, "value": v, "scope": "tags"} for k, v in tags.items()]
        return True

    # deployments (for the /rollout ECU view)
    def deployment_status(self, dep_id):
        return {"id": dep_id, "status": "inprogress", "artifacts": []}

    def deployment_statistics(self, dep_id):
        return {"success": 0}

    def _req(self, method, path, **kw):
        if path.startswith(f"{self.dep}/") and "/devices" in path:
            return 200, json.dumps([{"id": i} for i in self.inv]).encode(), {}
        return 404, b"{}", {}


class FakeColony:
    """colony-api: the journal (survives a GS restart: it is the colony process), /probe, /pubkey, create."""

    def __init__(self, journal: list[dict] | None = None, logs: dict | None = None):
        self.journal = {d["id"]: d for d in (journal or [])}
        self.logs = dict(logs or {})
        self.created: list[dict] = []
        self.probes: list[tuple] = []
        self.probe_result: tuple[int, dict] = (200, {"hostname": "board", "sudo": "ok"})
        self.probe_gate: threading.Event | None = None
        self.create_status = 200          # 409: the newer colony refuses a second active run
        self.create_conflict_id = None
        self.lock = threading.Lock()

    @classmethod
    def from_fixtures(cls):
        rows = [dict(d) for d in _load("deployments.json")["deployments"] if d.get("authority") == "base"]
        for r in rows:
            r.pop("authority", None)
        logs = {r["id"]: provision_log(r["rig"], r.get("vpn_ip") or "100.64.0.1",
                                       "opkg" if r["rig"] == "sk-tda4vm" else "debian",
                                       "apt os_packages + etcd-server, Mender client" if r["rig"] == "sk-tda4vm"
                                       else "nothing")
                for r in rows if r.get("kind") == "provision"}
        return cls(rows, logs)

    def _err(self, st, body):
        from gs_api.colony_client import ColonyError
        return ColonyError("fake", st, json.dumps(body).encode())

    def deployments(self):
        with self.lock:
            return [copy.deepcopy(d) for d in sorted(self.journal.values(), key=lambda d: d["created"],
                                                     reverse=True)]

    def deployment(self, did):
        with self.lock:
            if did not in self.journal:
                raise self._err(404, {"detail": "no such deployment"})
            return copy.deepcopy(self.journal[did])

    def log(self, did):
        with self.lock:
            if did not in self.journal:
                raise self._err(404, {"detail": "no such deployment"})
            return self.logs.get(did, "")

    def rigs(self):
        return []

    def create(self, rig, kind="orchestrate", schedule=None, name=None, host=None, extra=None, secrets=None):
        with self.lock:
            if self.create_status == 409:
                raise self._err(409, {"detail": f"rig {rig} has an active run {self.create_conflict_id}",
                                      "run_id": self.create_conflict_id})
            did = uuid.uuid4().hex
            rec = {"id": did, "name": name or f"{kind}-{rig}", "rig": rig, "host": host, "extra": extra or {},
                   "kind": kind, "artifact_name": f"{kind}:{rig}", "status": "inprogress", "schedule": schedule,
                   "created": time.time(), "started": time.time(), "finished": None,
                   "statistics": {"status": {"success": 0, "failure": 0, "pending": 1, "noartifact": 0}}}
            self.journal[did] = rec
            self.created.append({"rig": rig, "kind": kind, "host": host, "extra": extra, "name": name,
                                 "secrets": bool(secrets)})
            return copy.deepcopy(rec)

    def finish(self, did, ok=True, log=None, **fields):
        """The play ends (what colony's worker does)."""
        with self.lock:
            rec = self.journal[did]
            rec["status"] = "finished"
            rec["finished"] = time.time()
            rec["statistics"]["status"] = {"success": 1 if ok else 0, "failure": 0 if ok else 1, "pending": 0}
            rec.update(fields)
            if log is not None:
                self.logs[did] = log

    def probe(self, host, user=None, timeout=30.0):
        self.probes.append((host, user))
        if self.probe_gate is not None:
            self.probe_gate.wait(10)
        st, body = self.probe_result
        if st != 200:
            raise self._err(st, body)
        return {"host": host, **body}

    def pubkey(self):
        return GS_PUBKEY


@pytest.fixture
def mender():
    return FakeMender.from_fixtures()


@pytest.fixture
def colony():
    return FakeColony.from_fixtures()


@pytest.fixture
def com_calls(monkeypatch):
    """com_client stand-ins: record the target every com call is made to."""
    from gs_api import com_client
    calls: list[tuple[str, str]] = []

    def rec(kind):
        def f(target, *a, **kw):
            calls.append((kind, target))
            if kind == "com_state":
                return {"state": "mtls" if target.startswith("100.") else "down", "detail": target}
            return {"ok": True, "valid": True, "state": 0}
        return f
    for fn in ("com_state", "get_progress", "campaign_status", "request_update", "check_for_campaign",
               "campaign_decide", "list_machines", "sw_state"):
        monkeypatch.setattr(com_client, fn, rec(fn))
    return calls


@pytest.fixture
def wire(monkeypatch, mender, colony, com_calls):
    """Route every gs-api module to the fakes; shorten the follower timings; return an app factory."""
    from gs_api import clients, colony_client
    from gs_api.routers import campaign, deployments, devices, rigs, ucm
    for mod in (clients, deployments, devices, ucm, campaign):
        if hasattr(mod, "mender_client"):
            monkeypatch.setattr(mod, "mender_client", lambda s, _m=mender: _m)
    for mod in (colony_client, deployments, devices):
        if hasattr(mod, "colony_client"):
            monkeypatch.setattr(mod, "colony_client", lambda s, _c=colony: _c)
    monkeypatch.setattr(rigs, "POLL", 0.02)
    monkeypatch.setattr(rigs, "ACCEPT_POLL", 0.02)
    monkeypatch.setattr(rigs, "ACCEPT_TIMEOUT", 0.5)
    monkeypatch.setattr(rigs, "TAG_RETRIES", 2)
    rigs.reset_state()

    def make_client():
        from fastapi.testclient import TestClient
        from gs_api.app import create_app
        return TestClient(create_app())
    yield make_client
    rigs.reset_state()


def wait_for(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.02)
    raise AssertionError("condition not reached")

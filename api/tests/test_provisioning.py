"""Regression tests for the operator's provisioning path (spec ui-provisioning, part G)."""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import pytest

from conftest import GS_PUBKEY, KEY, FakeMender, new_pubkey, provision_log, wait_for

AI64_ID = "94b200f0-edf7-432d-84d8-83d0ea49a750"
AI64_VPN = "100.91.130.60"


def _add(client, **body):
    req = {"name": "rig-new", "address": "10.0.0.50", "user": "debian", "role": "master"}
    req.update(body)
    return client.post("/api/rigs", json=req, headers=KEY)


# ── ONE fleet list ───────────────────────────────────────────────────────────
def test_fleet_one_list(wire, com_calls):
    c = wire()
    r = c.get("/api/fleet", headers=KEY)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert isinstance(rows, list)
    names = [x["name"] for x in rows]
    assert len(names) == len(set(names)), f"a device appears twice: {names}"

    ai = next(x for x in rows if x["name"] == "ai64-proto")
    assert ai["source"] == "mender+colony"
    assert ai["mender_id"] == AI64_ID and ai["auth_status"] == "accepted"
    assert ai["vpn_ip"] == AI64_VPN and ai["artifact"] == "base-0.1.0-trixie-arm64"
    assert ai["last_run"] and ai["last_run"]["status"] == "success"
    assert ai["com"]["state"] == "mtls"

    sk = next(x for x in rows if x["name"] == "sk-tda4vm")
    assert sk["source"] == "colony"
    assert sk["mender_id"] is None and sk["auth_status"] is None
    assert sk["rig_class"] == "opkg"                  # from the provision log (an older colony-api)
    assert sk["vpn_ip"] == "100.105.15.20" and sk["role"] == "master"
    assert sk["base_version"] == "0.3.0-trixie-arm64"
    assert sk["last_run"]["kind"] == "provision"
    for x in rows:
        assert set(x) >= {"name", "source", "mender_id", "auth_status", "rig_class", "role", "address", "vpn_ip",
                          "base_version", "last_run", "com", "artifact"}
        assert x["com"]["state"] in ("mtls", "plaintext", "down", "unknown")
    # the com probe goes to the reachable address, never the AI-64's inventory 192.168.64.2
    targets = [t for k, t in com_calls if k == "com_state"]
    assert f"{AI64_VPN}:7700" in targets and not any(t.startswith("192.168.64.") for t in targets)


def test_fleet_rig_class_from_new_colony_field(wire, colony):
    sk = next(d for d in colony.journal.values() if d["rig"] == "sk-tda4vm")
    sk["rig_class"] = "opkg"
    colony.logs.pop(sk["id"])                         # the field alone is enough
    rows = wire().get("/api/fleet?com=false", headers=KEY).json()
    assert next(x for x in rows if x["name"] == "sk-tda4vm")["rig_class"] == "opkg"


def test_fleet_survives_a_mender_outage(wire, mender, monkeypatch):
    monkeypatch.setattr(mender, "auth_devices", lambda: (_ for _ in ()).throw(RuntimeError("mender down")))
    r = wire().get("/api/fleet?com=false", headers=KEY)
    assert r.status_code == 200
    assert {x["name"] for x in r.json()} >= {"sk-tda4vm", "ai64-proto"}  # the colony rows stay
    assert "mender down" in r.headers.get("X-GS-Fleet-Errors", "")


# ── the second click ─────────────────────────────────────────────────────────
def test_second_click_409(wire, colony):
    c = wire()
    r1 = _add(c)
    assert r1.status_code == 202, r1.text
    run_id = r1.json()["run_id"]
    assert r1.json()["rig"] == "rig-new" and r1.json()["kind"] == "provision"
    r2 = _add(c)
    assert r2.status_code == 409
    assert r2.json()["run_id"] == run_id and r2.json()["detail"]
    assert len(colony.created) == 1                    # one run only
    # Deploy runtime on the same rig while the provision runs: the same rule
    r3 = c.post("/api/rigs/rig-new/deploy", json={"runtime_version": "0.3.0-trixie-arm64"}, headers=KEY)
    assert r3.status_code == 409 and r3.json()["run_id"] == run_id


def test_second_click_during_preflight_409(wire, colony):
    c = wire()
    colony.probe_gate = threading.Event()
    first = {}
    t = threading.Thread(target=lambda: first.setdefault("r", _add(c)))
    t.start()
    wait_for(lambda: colony.probes)                    # the first click is in its preflight
    r2 = _add(c)
    colony.probe_gate.set()
    t.join(10)
    assert r2.status_code == 409 and r2.json()["run_id"] is None
    assert first["r"].status_code == 202
    assert len(colony.created) == 1


def test_colony_409_is_passed_on(wire, colony):
    """colony-api refuses a second active run itself (defence in depth): the GS answers 409 + its run id."""
    colony.create_status, colony.create_conflict_id = 409, "f" * 32
    r = _add(wire())
    assert r.status_code == 409 and r.json()["run_id"] == "f" * 32


# ── preflight ────────────────────────────────────────────────────────────────
HOSTKEY = ("probe 10.0.0.50 failed: @@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
           "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n@@@@@@@@@@@@@@@@@@@@@@")


@pytest.mark.parametrize("status,body,reason", [
    (502, {"detail": "probe 10.0.0.50 failed: ssh: connect to host 10.0.0.50 port 22: Connection timed out"},
     "unreachable"),
    (502, {"detail": "probe 10.0.0.50 failed: ssh: connect to host 10.0.0.50 port 22: No route to host"},
     "unreachable"),
    (502, {"detail": "probe 10.0.0.50 failed: ssh: connect to host 10.0.0.50 port 22: Connection refused"},
     "unreachable"),
    (502, {"detail": "probe nope.local failed: ssh: Could not resolve hostname nope.local: Name or service not "
                     "known"}, "unreachable"),
    (502, {"detail": "probe 10.0.0.50: Command '['ssh']' timed out after 20 seconds"}, "unreachable"),
    (502, {"detail": "probe 10.0.0.50 failed: Warning: Permanently added '10.0.0.50' (ED25519) to the list of "
                     "known hosts.\r\nfoo@10.0.0.50: Permission denied (publickey,password)."}, "auth"),
    (502, {"detail": HOSTKEY}, "host-key-changed"),
    (502, {"detail": "probe 10.0.0.50 failed: Host key verification failed."}, "host-key-changed"),
    (200, {"hostname": "board", "sudo": "no"}, "no-sudo"),
])
def test_preflight_errors(wire, colony, status, body, reason):
    colony.probe_result = (status, body)
    r = _add(wire(), user="foo")
    assert r.status_code == 422, r.text
    j = r.json()
    assert j["reason"] == reason and isinstance(j["detail"], str) and j["detail"]
    if reason == "auth":
        assert j["pubkey"] == GS_PUBKEY and "foo@10.0.0.50" in j["detail"]
    assert colony.probes == [("10.0.0.50", "foo")]
    assert colony.created == []                        # nothing started


@pytest.mark.parametrize("sudo", ["ok", "root", None])
def test_preflight_passes(wire, colony, sudo):
    """sudo ok / root, or an older colony-api that does not report sudo: the provision starts."""
    colony.probe_result = (200, {"hostname": "board", **({"sudo": sudo} if sudo else {})})
    r = _add(wire(), runtime_version="0.3.0-trixie-arm64")
    assert r.status_code == 202, r.text
    (cr,) = colony.created
    assert cr["kind"] == "provision" and cr["host"] == "10.0.0.50"
    assert cr["extra"] == {"role": "master", "mender_device_id": "rig-new", "ansible_user": "debian",
                           "runtime_version": "0.3.0-trixie-arm64"}


# ── auto-accept ──────────────────────────────────────────────────────────────
def _final(c, run_id):
    return wait_for(lambda: (lambda j: j if j["status"] != "running" else None)(
        c.get(f"/api/runs/{run_id}", headers=KEY).json()))


def _set(d, i=-1):
    return d["auth_sets"][i]["id"]


def test_auto_accept_identity_only():
    """Only the set of the rig's identity (exactly) AND the rig's key; never another pending set."""
    from gs_api.routers import rigs
    key = new_pubkey()
    m = FakeMender()
    other = m.add_pending("other-rig", pubkey=key)        # the right key under another identity
    longer = m.add_pending("ai64-new-2", pubkey=key)      # a name that merely CONTAINS the rig name
    mac_only = m.add_pending(None, mac="24:76:25:a1:e7:ed", pubkey=key)
    target = m.add_pending("ai64-new", pubkey=key)
    ok, dev, why = rigs.auto_accept(m, "ai64-new", key, timeout=0, interval=0)
    assert ok and dev == target["id"] and why is None
    assert m.accepted == [(target["id"], _set(target))]
    for d in (other, longer, mac_only):
        assert next(x for x in m.auth if x["id"] == d["id"])["status"] == "pending"

    m2 = FakeMender()
    m2.add_pending("other-rig", pubkey=key)
    m2.add_pending("ai64-new-2", pubkey=key)
    ok, dev, why = rigs.auto_accept(m2, "ai64-new", key, timeout=0, interval=0)
    assert (ok, dev) == (False, None) and "accept manually" in why
    assert m2.accepted == []


def test_auto_accept_requires_key_match():
    """The identity matches, the key differs (someone claiming the name at the gateway): not accepted."""
    from gs_api.routers import rigs
    m = FakeMender()
    impostor = m.add_pending("ai64-new", pubkey=new_pubkey())
    ok, dev, why = rigs.auto_accept(m, "ai64-new", new_pubkey(), timeout=0, interval=0)
    assert not ok and dev is None and m.accepted == []
    assert "ANOTHER key was NOT accepted" in why
    assert next(x for x in m.auth if x["id"] == impostor["id"])["status"] == "pending"
    # no key reported by the provision: no auto-accept at all, even for the only matching identity
    ok, dev, why = rigs.auto_accept(m, "ai64-new", None, timeout=0, interval=0)
    assert not ok and m.accepted == [] and "accept manually" in why
    # a garbage key line is no key
    ok, _, why = rigs.auto_accept(m, "ai64-new", "-----BEGIN PUBLIC KEY-----\nnot a key\n", timeout=0, interval=0)
    assert not ok and m.accepted == []


def test_no_second_set_takeover(wire, colony, mender):
    """An ACCEPTED device + a second pending set with the same identity and another key: never accepted,
    neither when the rig reports its (accepted) key nor when the provision reported no key."""
    from gs_api.routers import rigs
    rig_key, thief_key = new_pubkey(), new_pubkey()
    m = FakeMender()
    d = m.add_pending("ai64-new", pubkey=rig_key)
    m.accept_device(d["id"], _set(d, 0))
    m.accepted.clear()
    m.add_pending("ai64-new", pubkey=thief_key)          # the second set, same identity
    ok, dev, why = rigs.auto_accept(m, "ai64-new", rig_key, timeout=0, interval=0)
    assert ok and dev == d["id"]                          # already accepted with the rig's own key
    assert m.accepted == []                               # the thief's set stays pending
    assert [a["status"] for a in m.auth[0]["auth_sets"]] == ["accepted", "pending"]
    # through the API: a re-provision of the accepted rig while the thief's set is pending
    c = wire()
    acc = mender.add_pending("ai64-two", pubkey=rig_key)
    mender.accept_device(acc["id"], _set(acc, 0))
    mender.accepted.clear()
    mender.add_pending("ai64-two", pubkey=thief_key)
    run_id = _add(c, name="ai64-two").json()["run_id"]
    colony.finish(run_id, log=provision_log("ai64-two", mender_key=rig_key))
    run = _final(c, run_id)
    assert run["accepted"] is True and mender.accepted == []
    # and a provision whose rig key matches NOTHING: the device is not "already accepted" for it
    mender.accepted.clear()
    run_id = _add(c, name="ai64-two").json()["run_id"]
    colony.finish(run_id, log=provision_log("ai64-two", mender_key=new_pubkey()))
    run = _final(c, run_id)
    assert run["accepted"] is False and "accept manually" in run["note"] and mender.accepted == []


def test_accept_with_matching_key(wire, colony, mender):
    """The pending set carries the key the rig reported (re-wrapped PEM, CRLF, other whitespace): accepted,
    and the tags are written."""
    from gs_api.routers import rigs
    key = new_pubkey()
    m = FakeMender()
    wrapped = "\r\n".join("  " + ln + " " for ln in key.strip().splitlines()) + "\r\n\r\n"
    d = m.add_pending("ai64-new", pubkey=wrapped)
    ok, dev, _ = rigs.auto_accept(m, "ai64-new", key, timeout=0, interval=0)
    assert ok and m.accepted == [(d["id"], _set(d))]

    c = wire()
    r = _add(c, name="ai64-new", address="10.0.0.144")
    run_id = r.json()["run_id"]
    pend = mender.add_pending("ai64-new", pubkey=key)
    stranger = mender.add_pending("someone-else", pubkey=key)
    colony.finish(run_id, log=provision_log("ai64-new", vpn="100.64.0.77", mender_key=key), vpn_ip="100.64.0.77")
    run = _final(c, run_id)
    assert run["status"] == "success" and run["accepted"] is True
    assert mender.accepted == [(pend["id"], _set(pend))]
    assert next(x for x in mender.auth if x["id"] == stranger["id"])["status"] == "pending"
    tags = mender.get_tags(pend["id"])
    assert tags["name"] == "ai64-new" and tags["role"] == "master" and tags["rig_class"] == "debian"
    assert tags["local_ip"] == "10.0.0.144" and tags["remote_ip"] == "100.64.0.77"
    assert tags["base_authority"] == "colony"


def test_mender_pubkey_from_a_newer_colony_field(wire, colony, mender):
    """colony part C may carry the key as a record field: used without the log line."""
    key = new_pubkey()
    c = wire()
    run_id = _add(c, name="ai64-f").json()["run_id"]
    pend = mender.add_pending("ai64-f", pubkey=key)
    colony.finish(run_id, log=provision_log("ai64-f"), mender_pubkey=key)
    assert _final(c, run_id)["accepted"] is True and mender.accepted == [(pend["id"], _set(pend))]


def test_opkg_rig_is_not_accepted(wire, colony, mender):
    c = wire()
    run_id = _add(c, name="sk-new", user="root").json()["run_id"]
    mender.add_pending("sk-new")                       # even if a set with that identity shows up
    colony.finish(run_id, log=provision_log("sk-new", cls="opkg", skips="Mender client"), rig_class="opkg",
                  skipped_by_design=["Mender client"])
    run = wait_for(lambda: (lambda j: j if j["status"] != "running" else None)(
        c.get(f"/api/runs/{run_id}", headers=KEY).json()))
    assert run["status"] == "success" and run["accepted"] == "not-a-mender-rig"
    assert run["rig_class"] == "opkg" and run["skipped_by_design"] == ["Mender client"]
    assert mender.accepted == []


def test_no_pending_set_in_time_is_accepted_false(wire, colony, mender):
    c = wire()
    run_id = _add(c, name="quiet-rig").json()["run_id"]
    colony.finish(run_id, log=provision_log("quiet-rig", mender_key=new_pubkey()))
    run = wait_for(lambda: (lambda j: j if j["status"] != "running" else None)(
        c.get(f"/api/runs/{run_id}", headers=KEY).json()))
    assert run["status"] == "success" and run["accepted"] is False and "quiet-rig" in run["note"]


# ── runs: live view, failure, restart ────────────────────────────────────────
def test_run_live_progress_and_failure(wire, colony):
    c = wire()
    run_id = _add(c).json()["run_id"]
    rec = colony.journal[run_id]
    rec["progress"] = {"tasks_done": 3, "current_task": "Join the fleet tailnet", "failed_task": None,
                       "failed_msg": None}
    rec["log_tail"] = "TASK [Gathering Facts] ***\nok: [rig-new]\nTASK [Join the fleet tailnet] ***\n"
    j = c.get(f"/api/runs/{run_id}", headers=KEY).json()
    assert j["status"] == "running"
    assert j["step"]["index"] == 4 and j["step"]["name"] == "Join the fleet tailnet"
    assert j["step"]["total_estimate"] >= 4
    assert j["tasks"] == [{"name": "Gathering Facts", "result": "ok"}]
    colony.finish(run_id, ok=False, log=FAILED_LOG_FOR("rig-new"))
    j = wait_for(lambda: (lambda x: x if x["status"] == "failure" else None)(
        c.get(f"/api/runs/{run_id}", headers=KEY).json()))
    assert j["error"] == {"task": "Install the Mender client",
                          "message": "No package matching 'mender-client4' is available"}
    assert j["tasks"][-1] == {"name": "Install the Mender client", "result": "failed"}
    assert j["accepted"] is False


def FAILED_LOG_FOR(rig):
    from conftest import FAILED_LOG
    return FAILED_LOG.format(rig=rig)


def test_runs_survive_restart(wire, colony, mender):
    from gs_api.routers import rigs
    c1 = wire()
    run_id = _add(c1, name="ai64-proto", address="10.0.0.144").json()["run_id"]
    running = _add(c1, name="rig-b").json()["run_id"]
    colony.journal[running]["progress"] = {"tasks_done": 1, "current_task": "Set rig_class"}
    colony.finish(run_id, log=provision_log("ai64-proto", vpn=AI64_VPN), vpn_ip=AI64_VPN,
                  pki={"serial": "5f3a", "not_after": "2027-02-17T00:00:00Z"})
    colony.journal[run_id]["finished"] = time.time() - 3600   # long done: nothing left to resume

    rigs.reset_state()                                 # gs-api restarts: its memory is gone
    c2 = wire()                                        # a fresh app instance, the same colony-api
    j = c2.get(f"/api/runs/{run_id}", headers=KEY).json()
    assert j["status"] == "success" and j["rig"] == "ai64-proto" and j["kind"] == "provision"
    assert j["vpn_ip"] == AI64_VPN and j["pki"] == {"serial": "5f3a", "not_after": "2027-02-17T00:00:00Z"}
    assert j["rig_class"] == "debian" and j["skipped_by_design"] == []
    assert [t["name"] for t in j["tasks"]][:2] == ["Gathering Facts",
                                                   "Include rig classification (debian | opkg)"]
    assert j["accepted"] is True                       # read back from Mender (the AI-64 is accepted)
    assert j["error"] is None and j["started"] and j["finished"]

    j = c2.get(f"/api/runs/{running}", headers=KEY).json()
    assert j["status"] == "running" and j["step"]["name"] == "Set rig_class"

    lst = c2.get("/api/runs?rig=ai64-proto", headers=KEY).json()
    assert lst[0]["run_id"] == run_id                  # newest first
    assert all(x["rig"] == "ai64-proto" for x in lst)
    created = [x["created"] for x in lst]
    assert created == sorted(created, reverse=True)
    # the second click is still refused after the restart: the active run is in colony's journal
    r = _add(c2, name="rig-b")
    assert r.status_code == 409 and r.json()["run_id"] == running


def test_restart_resumes_the_accept(wire, colony, mender):
    """A provision that finished while the GS was down: the new process accepts the device."""
    from gs_api.routers import rigs
    c1 = wire()
    run_id = _add(c1, name="late-rig").json()["run_id"]
    rigs.reset_state()
    key = new_pubkey()
    pend = mender.add_pending("late-rig", pubkey=key)
    colony.finish(run_id, log=provision_log("late-rig", mender_key=key))
    c2 = wire()
    j = wait_for(lambda: (lambda x: x if x["status"] != "running" else None)(
        c2.get(f"/api/runs/{run_id}", headers=KEY).json()))
    assert j["accepted"] is True and mender.accepted == [(pend["id"], _set(pend))]


def test_deploy_runtime_uses_reachable_address(wire, colony, mender):
    c = wire()
    r = c.post("/api/rigs/ai64-proto/deploy", json={"runtime_version": "0.3.0-trixie-arm64"}, headers=KEY)
    assert r.status_code == 202, r.text
    (cr,) = colony.created
    assert cr["kind"] == "orchestrate" and cr["host"] == AI64_VPN
    assert cr["extra"]["runtime_version"] == "0.3.0-trixie-arm64" and cr["extra"]["ansible_user"] == "debian"
    # a colony-only rig (the SK): its provision's tailnet address
    colony.created.clear()
    r = c.post("/api/rigs/sk-tda4vm/deploy", json={"runtime_version": "0.3.0-trixie-arm64"}, headers=KEY)
    assert r.status_code == 202 and colony.created[0]["host"] == "100.105.15.20"
    assert colony.created[0]["extra"]["ansible_user"] == "root"
    r = c.post("/api/rigs/no-such-rig/deploy", json={"runtime_version": "0.3.0"}, headers=KEY)
    assert r.status_code == 404


def test_base_endpoint_still_sync(wire, colony, monkeypatch):
    """The CLI's synchronous POST /api/deployments/base keeps working."""
    orig = colony.create

    def create_and_finish(*a, **kw):
        rec = orig(*a, **kw)
        colony.finish(rec["id"])
        return rec
    monkeypatch.setattr(colony, "create", create_and_finish)
    r = wire().post("/api/deployments/base", json={"rig": "ai64-proto", "kind": "orchestrate",
                                                   "device_id": AI64_ID, "runtime_version": "0.3.0"},
                    headers=KEY)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and r.json()["deployment"]["status"] == "finished"
    assert colony.created[0]["host"] == AI64_VPN


# ── the reachable address ────────────────────────────────────────────────────
def test_reachable_address_everywhere(wire, mender, com_calls):
    from gs_api import addressing
    from gs_api.routers import deployments
    c = wire()
    dev = mender.inv[AI64_ID]
    ipv4 = {a["name"]: a["value"] for a in dev["attributes"]}["ipv4_eth0"]
    assert ipv4[0].startswith("192.168.64.")          # the trap: the first inventory address is closed
    want = f"{AI64_VPN}:7700"

    assert c.get(f"/api/ucm/{AI64_ID}/progress", headers=KEY).json()["target"] == want
    assert c.get(f"/api/campaign/{AI64_ID}/status", headers=KEY).json()["target"] == want
    assert deployments._com_endpoint(dev) == want
    ecu = c.get("/api/deployments/some-dep/rollout", headers=KEY).json()["ecu"]
    assert [e["target"] for e in ecu] == [want]
    assert {t for _, t in com_calls} == {want}

    # remote_ip first, then local_ip, never an ipv4_* interface
    tags = lambda **t: {"id": "d", "attributes": [  # noqa: E731
        {"name": "ipv4_eth0", "value": ["192.168.64.2/24", "10.0.0.144/24"], "scope": "inventory"},
        *({"name": k, "value": v, "scope": "tags"} for k, v in t.items())]}
    assert addressing.reachable_ip(tags(remote_ip="100.1.2.3", local_ip="10.0.0.144")) == "100.1.2.3"
    assert addressing.reachable_ip(tags(local_ip="10.0.0.144")) == "10.0.0.144"
    assert addressing.reachable_ip(tags()) is None
    with pytest.raises(addressing.NoReachableAddress):
        addressing.com_endpoint(tags())

    # a device with only inventory addresses: an explicit error, not the first ipv4
    mender.inv["bare"] = tags()
    mender.inv["bare"]["id"] = "bare"
    r = c.get("/api/ucm/bare/progress", headers=KEY)
    assert r.status_code == 400 and "no reachable address" in r.json()["detail"]
    r = c.get("/api/campaign/bare/status", headers=KEY)
    assert r.status_code == 400
    assert deployments._com_endpoint(mender.inv["bare"]) is None


def test_no_endpoint_built_from_inventory_ipv4():
    """Source guard: the com-endpoint builders never read an ipv4_* inventory attribute again."""
    root = Path(__file__).resolve().parents[1] / "gs_api"
    for f in ("routers/ucm.py", "routers/campaign.py", "addressing.py", "routers/rigs.py"):
        code = "\n".join(ln for ln in (root / f).read_text().splitlines() if not ln.lstrip().startswith("#"))
        assert not re.search(r"""["']ipv4""", code), f"{f} reads an ipv4 inventory attribute"
    src = (root / "routers/deployments.py").read_text()
    body = src[src.index("def _com_endpoint"):src.index("# ── Distribution one-click")]
    assert "ipv4" not in body.split('"""')[2]


# ── validation ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("field,value", [
    ("name", "AI64"), ("name", "-ai64"), ("name", "ai64_proto"), ("name", "ai64.proto"), ("name", "ai 64"),
    ("name", "a" * 64), ("name", ""), ("name", "ai64-proto\n"),
    ("address", "999.1.1.1"), ("address", "host_name"), ("address", "10.0.0.5:22"), ("address", ""),
    ("address", "-bad.local"), ("address", "10.0.0.5; reboot"),
    ("user", "Debian"), ("user", "1user"), ("user", "a" * 33), ("user", "deb ian"),
    ("role", "worker"), ("runtime_version", "0.3.0 && x"),
])
def test_name_validation(wire, colony, field, value):
    r = _add(wire(), **{field: value})
    assert r.status_code == 422, (field, value, r.text)
    assert r.json()["reason"] == "invalid" and field in r.json()["detail"]
    assert colony.probes == [] and colony.created == []


@pytest.mark.parametrize("body", [
    {"name": "a"}, {"name": "0rig"}, {"name": "a" * 63}, {"name": "ai64-proto"},
    {"address": "rig.local"}, {"address": "10.0.0.5"}, {"user": "root"}, {"user": "_svc-1"},
    {"role": "zonal"},
])
def test_name_validation_accepts(wire, colony, body):
    r = _add(wire(), **body)
    assert r.status_code == 202, r.text


def test_deploy_name_validation(wire, colony):
    c = wire()
    r = c.post("/api/rigs/BAD_NAME/deploy", json={"runtime_version": "0.3.0"}, headers=KEY)
    assert r.status_code == 422 and r.json()["reason"] == "invalid"
    r = c.post("/api/rigs/ai64-proto/deploy", json={"runtime_version": "../../x"}, headers=KEY)
    assert r.status_code == 422
    assert colony.created == []


def test_routes_need_the_key(wire):
    c = wire()
    assert c.get("/api/fleet").status_code == 401
    assert c.post("/api/rigs", json={}).status_code == 401

"""Regression tests for the app OTA path through the GS (spec hil-app-ota, G2 + G3): the Mender
artifacts list, a rollout of one artifact to chosen devices, the runtime gate, the per-device outcome."""
from __future__ import annotations

import uuid

from conftest import KEY, make_artifact

AI64_ID = "94b200f0-edf7-432d-84d8-83d0ea49a750"

# a theia-swp commit-gate rollback as the AI-64 reported it (the live log of 2026-11-19), wc names
ROLLBACK_LOG = """\
2026-11-19 15:27:39.165 +0000 UTC info: Running mender-update 5.2.0
2026-11-19 15:27:39.165 +0000 UTC info: Deployment with ID d started.
2026-11-19 15:27:39.166 +0000 UTC info: Sending status update to server
2026-11-19 15:27:39.300 +0000 UTC info: Downloading GET https://s3.docker.mender.io/mender/x?X-Amz-Credential=AKIAFAKE%2F20261119&X-Amz-Signature=deadbeefcafe HTTP
2026-11-19 15:27:39.517 +0000 UTC info: Installing artifact...
2026-11-19 15:27:39.967 +0000 UTC info: Update Module output (stdout): theia-swp: runtime dependency satisfied (requires '0.3.1-trixie-arm64', installed '0.3.1')
2026-11-19 15:27:39.968 +0000 UTC info: Update Module output (stdout): theia-swp: no config migration (free-swap SWP)
2026-11-19 15:27:40.181 +0000 UTC info: Update Module output (stdout): theia-swp: current → releases/wc-0.1.1-trixie-arm64 (previous → releases/wc-0.1.0-trixie-arm64)
2026-11-19 15:27:45.744 +0000 UTC info: Update Module output (stdout): theia-swp: supervisor restarted (systemd) — adopting current/config
2026-11-19 15:28:16.861 +0000 UTC info: Update Module output (stderr): theia-swp: COMMIT FAILED — process wc_mavlink restarted 4 times within 10s on releases/wc-0.1.1-trixie-arm64 — Mender will roll back
2026-11-19 15:28:16.863 +0000 UTC error: Process returned non-zero exit status: ArtifactCommit: Process exited with status 1
2026-11-19 15:28:16.896 +0000 UTC info: Update Module output (stdout): theia-swp: ROLLBACK current → releases/wc-0.1.0-trixie-arm64
2026-11-19 15:28:16.978 +0000 UTC info: Update Module output (stdout): theia-swp: supervisor restarted (systemd) — adopting current/config
2026-11-19 15:28:17.067 +0000 UTC info: Sending status update to server
"""

UNSIGNED_LOG = """\
2026-11-19 15:26:39.304 +0000 UTC info: Running mender-update 5.2.0
2026-11-19 15:26:39.305 +0000 UTC info: Sending status update to server
2026-11-19 15:26:39.712 +0000 UTC error: Signature verification Error: expecting signed artifact, but no signature file found
2026-11-19 15:26:39.712 +0000 UTC error: Operation canceled: GET https://s3.docker.mender.io/mender/b6?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAFAKE%2F2026&X-Amz-Signature=0123abcd HTTP request cancelled
2026-11-19 15:26:39.72 +0000 UTC info: Sending status update to server
"""


def _wc(mender, base="0.3.1-trixie-arm64"):
    """The AI-64 on base 0.3.1 + the wc SWPs of the HIL loop on the Mender server."""
    mender.set_tags(AI64_ID, {"base_version": base})
    mender.artifacts += [
        make_artifact("wc-0.1.0-trixie-arm64"),
        make_artifact("wc-0.1.1-trixie-arm64"),
        make_artifact("wc-0.1.2-trixie-arm64", signed=False),
        make_artifact("wc-0.2.0-bookworm-arm64"),
    ]


def _second_rig(mender, colony, name="ai64-bench", base="0.3.1-trixie-arm64", status="accepted"):
    """Another enrolled AI-64 (Mender inventory + deviceauth)."""
    did = str(uuid.uuid4())
    attrs = [{"name": "device_id", "value": name, "scope": "identity"},
             {"name": "device_type", "value": "theia-rig", "scope": "inventory"},
             {"name": "name", "value": name, "scope": "tags"}]
    if base:
        attrs.append({"name": "base_version", "value": base, "scope": "tags"})
    mender.inv[did] = {"id": did, "attributes": attrs}
    idd = {"device_id": name}
    mender.auth.append({"id": did, "identity_data": idd, "status": status,
                        "auth_sets": [{"id": str(uuid.uuid4()), "identity_data": idd, "status": status}]})
    return did


def _roll(c, artifact, devices, **kw):
    return c.post("/api/deployments/app", json={"artifact_name": artifact, "devices": devices, **kw}, headers=KEY)


# ── G2a: the artifacts ───────────────────────────────────────────────────────
def test_artifacts_listed(wire, mender):
    _wc(mender)
    mender.artifacts += [
        make_artifact("wc-0.1.3-trixie-arm64", meta={"requires_runtime": "0.3.1-trixie-arm64"}),
        make_artifact("theia-runtime-0.3.1-trixie-arm64", kind="theia-release"),
        make_artifact("hello-1.0", kind="single-file", device_types=("raspberrypi4",)),
    ]
    r = wire().get("/api/artifacts", headers=KEY)
    assert r.status_code == 200, r.text
    rows = {a["name"]: a for a in r.json()["artifacts"]}
    assert {"base-0.1.0-trixie-arm64", "base-0.1.1-unsigned", "base-0.2.0-trixie-arm64",
            "wc-0.1.0-trixie-arm64", "wc-0.1.2-trixie-arm64"} <= set(rows)
    for a in rows.values():
        assert set(a) >= {"name", "version", "device_types", "signed", "size", "created", "kind",
                          "requires_runtime"}

    wc = rows["wc-0.1.0-trixie-arm64"]
    assert (wc["app"], wc["version"], wc["abi"]) == ("wc", "0.1.0", "trixie-arm64")
    assert wc["kind"] == "theia-swp" and wc["device_types"] == ["theia-rig"]
    assert wc["signed"] is True and wc["size"] == 7680 and wc["created"].startswith("2026-11-20")
    assert wc["requires_runtime"] is None              # release-swp writes no Mender metadata today
    assert wc["requirement"] == {"runtime": None, "version": None, "abi": "trixie-arm64", "source": "name"}

    assert rows["wc-0.1.2-trixie-arm64"]["signed"] is False
    unsigned = rows["base-0.1.1-unsigned"]                         # the live recording: no abi in the name
    assert unsigned["signed"] is False and unsigned["abi"] is None and unsigned["requirement"] is None
    assert rows["base-0.1.0-trixie-arm64"]["size"] == 7680 and rows["base-0.1.0-trixie-arm64"]["kind"] == "theia-swp"

    meta = rows["wc-0.1.3-trixie-arm64"]                           # a requires_runtime in the metadata wins
    assert meta["requires_runtime"] == "0.3.1-trixie-arm64"
    assert meta["requirement"]["source"] == "metadata" and meta["requirement"]["version"] == "0.3.1"
    rt = rows["theia-runtime-0.3.1-trixie-arm64"]
    assert rt["kind"] == "theia-release" and rt["requirement"] is None   # the runtime is not app-gated
    other = rows["hello-1.0"]
    assert other["kind"] is None and other["types"] == ["single-file"]


# ── G2c: a rollout to the chosen devices ─────────────────────────────────────
def test_rollout_creates_deployment_for_selected_devices(wire, mender, colony):
    _wc(mender)
    bench = _second_rig(mender, colony)
    c = wire()
    r = _roll(c, "wc-0.1.0-trixie-arm64", [AI64_ID])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["devices"] == [{"id": AI64_ID, "name": "ai64-proto"}] and body["signed"] is True
    assert mender.created_deployments[-1]["devices"] == [AI64_ID]          # ONLY the chosen device
    assert mender.created_deployments[-1]["artifact_name"] == "wc-0.1.0-trixie-arm64"
    assert body["id"] == mender.created_deployments[-1]["id"]

    # two devices, one named by its Fleet name; a duplicate is dropped
    r = _roll(c, "wc-0.1.0-trixie-arm64", [AI64_ID, "ai64-bench", AI64_ID], name="hil wc 0.1.0")
    assert r.status_code == 201, r.text
    assert mender.created_deployments[-1]["devices"] == [AI64_ID, bench]
    assert mender.created_deployments[-1]["name"] == "hil wc 0.1.0"

    # an unsigned artifact is allowed (the rig refuses it: H3); the answer says so
    r = _roll(c, "wc-0.1.2-trixie-arm64", [AI64_ID])
    assert r.status_code == 201 and r.json()["signed"] is False

    n = len(mender.created_deployments)
    assert _roll(c, "wc-9.9.9-trixie-arm64", [AI64_ID]).status_code == 404       # no such artifact
    assert _roll(c, "wc-0.1.0-trixie-arm64", []).status_code == 422               # no device
    r = _roll(c, "wc-0.1.0-trixie-arm64", ["sk-tda4vm"])                          # colony-only: no Mender
    assert r.status_code == 422 and "not a Mender device" in r.json()["detail"]
    _second_rig(mender, colony, name="ai64-new", status="pending")
    r = _roll(c, "wc-0.1.0-trixie-arm64", ["ai64-new"])
    assert r.status_code == 422 and "not accepted" in r.json()["detail"]
    mender.artifacts.append(make_artifact("rpi-1.0.0-bookworm-arm64", device_types=("raspberrypi4",)))
    r = _roll(c, "rpi-1.0.0-bookworm-arm64", [AI64_ID])
    assert r.status_code == 422 and "device type" in r.json()["detail"]
    assert _roll(c, "wc-0.1.0-trixie-arm64", [AI64_ID], name="bad/../name").status_code == 422
    assert len(mender.created_deployments) == n                                  # none reached Mender
    # mutating: the key is required
    r = c.post("/api/deployments/app", json={"artifact_name": "wc-0.1.0-trixie-arm64", "devices": [AI64_ID]})
    assert r.status_code == 401 and len(mender.created_deployments) == n


# ── G3: the runtime gate ─────────────────────────────────────────────────────
def test_rollout_refuses_runtime_mismatch(wire, mender, colony):
    _wc(mender)
    bench = _second_rig(mender, colony, base="0.3.1-bookworm-arm64")
    bare = _second_rig(mender, colony, name="ai64-bare", base=None)
    c = wire()
    # the bookworm build onto the trixie AI-64 (H4): refused before Mender, the reason named
    r = _roll(c, "wc-0.2.0-bookworm-arm64", [AI64_ID])
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "runtime mismatch" in detail and "ai64-proto" in detail and "0.3.1-trixie-arm64" in detail
    assert "bookworm-arm64" in detail
    # one bad target in the set refuses the whole rollout
    r = _roll(c, "wc-0.1.0-trixie-arm64", [AI64_ID, bench])
    assert r.status_code == 422 and "ai64-bench" in r.json()["detail"] and "ai64-proto" not in r.json()["detail"]
    # a device with no base runtime recorded
    r = _roll(c, "wc-0.1.0-trixie-arm64", [bare])
    assert r.status_code == 422 and "no base runtime" in r.json()["detail"]
    # requires_runtime in the metadata: major.minor + abi (a runtime PATCH is fine)
    mender.artifacts += [make_artifact("wc-0.3.0-trixie-arm64", meta={"requires_runtime": "0.4.0-trixie-arm64"}),
                         make_artifact("wc-0.3.1-trixie-arm64", meta={"requires_runtime": "0.3.0-trixie-arm64"})]
    r = _roll(c, "wc-0.3.0-trixie-arm64", [AI64_ID])
    assert r.status_code == 422 and "0.4.0-trixie-arm64" in r.json()["detail"]
    assert not mender.created_deployments                                        # nothing reached Mender
    assert _roll(c, "wc-0.3.1-trixie-arm64", [AI64_ID]).status_code == 201
    # the match goes through: the trixie build onto the trixie rig
    assert _roll(c, "wc-0.1.0-trixie-arm64", [AI64_ID]).status_code == 201
    # an artifact without an abi (base-0.1.1-unsigned) declares no requirement
    assert _roll(c, "base-0.1.1-unsigned", [bench]).status_code == 201


# ── G2c: the outcome ─────────────────────────────────────────────────────────
def test_rollout_outcome_rolled_back(wire, mender):
    _wc(mender)
    c = wire()
    dep = _roll(c, "wc-0.1.1-trixie-arm64", [AI64_ID]).json()["id"]

    def outcome():
        r = c.get(f"/api/deployments/{dep}/outcome", headers=KEY)
        assert r.status_code == 200, r.text
        return r.json()

    o = outcome()                         # the device has not checked in yet: pending, from the target list
    assert o["outcome"] == "in_progress" and o["final"] is False
    assert o["devices"] == [{"id": AI64_ID, "name": "ai64-proto", "status": "pending", "outcome": "in_progress",
                             "rolled_back_to": None, "reason": [], "started": None, "finished": None}]
    mender.report(dep, AI64_ID, "installing")
    o = outcome()
    assert o["devices"][0]["status"] == "installing" and o["final"] is False
    mender.report(dep, AI64_ID, "failure", log=ROLLBACK_LOG)
    o = outcome()
    d = o["devices"][0]
    assert o["outcome"] == "rolled_back" and o["final"] is True and o["status"] == "finished"
    assert d["outcome"] == "rolled_back" and d["rolled_back_to"] == "wc-0.1.0-trixie-arm64"
    reason = "\n".join(d["reason"])
    assert "COMMIT FAILED" in reason and "wc_mavlink" in reason and "ROLLBACK current" in reason
    assert "Sending status update" not in reason and "UTC" not in reason
    assert len(d["reason"]) <= 4

    # an unsigned artifact the rig refuses: failure + the reason from the log, redacted
    dep2 = _roll(c, "wc-0.1.2-trixie-arm64", [AI64_ID]).json()["id"]
    mender.report(dep2, AI64_ID, "failure", log=UNSIGNED_LOG)
    o2 = c.get(f"/api/deployments/{dep2}/outcome", headers=KEY).json()
    d2 = o2["devices"][0]
    assert o2["outcome"] == "failure" and d2["rolled_back_to"] is None
    assert any("Signature verification" in ln for ln in d2["reason"])
    joined = "\n".join(d2["reason"])
    assert "0123abcd" not in joined and "AKIAFAKE" not in joined and "X-Amz-Signature=***" in joined

    # the commit (H1): success
    dep3 = _roll(c, "wc-0.1.0-trixie-arm64", [AI64_ID]).json()["id"]
    mender.report(dep3, AI64_ID, "success")
    o3 = c.get(f"/api/deployments/{dep3}/outcome", headers=KEY).json()
    assert o3["outcome"] == "success" and o3["final"] is True and o3["devices"][0]["reason"] == []
    # the device list the Rollouts table reads carries the same rollback
    rows = c.get(f"/api/deployments/{dep}/devices", headers=KEY).json()["devices"]
    assert rows[0]["rolled_back_to"] == "wc-0.1.0-trixie-arm64"

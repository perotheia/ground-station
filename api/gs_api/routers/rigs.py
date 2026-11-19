"""The operator's provisioning path (spec ui-provisioning, part G): Add device, Deploy runtime, the runs, ONE fleet.

  POST /api/rigs                  {name, address, user, role, runtime_version?} -> 202 {run_id, rig, kind}
  POST /api/rigs/{name}/deploy    {runtime_version}                             -> 202 {run_id}
  GET  /api/runs/{run_id}         the run, readable while it runs and after a GS restart
  GET  /api/runs?rig=<name>       the runs of a rig, newest first
  GET  /api/fleet                 ONE list: Mender devices AND base-plane-only (colony) rigs

ONE identity = the device name: the Mender identity (device_id), the certificate CN, the tailnet host, the colony
rig. A run IS a colony deployment (run_id = its id): its state lives in colony-api's journal, never in gs-api
memory. gs-api keeps only what it is DOING right now (a preflight in progress, a follower that accepts the
device in Mender after a successful provision); a follower is resumed from the journal after a restart.

After a successful provision the GS accepts the device's pending Mender auth set automatically, but ONLY the
set whose identity device_id == the rig name AND whose public key equals the key provision read from the rig
over ssh (COLONY_MENDER_PUBKEY; polling up to ACCEPT_TIMEOUT s; no key reported = no auto-accept, the run says
"accept manually"), then writes the tags (name,
role, rig_class, local_ip, remote_ip = the tailnet address, base fields). An opkg rig has no Mender client:
no accept, accepted = "not-a-mender-rig".
"""
from __future__ import annotations

import ipaddress
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import addressing, clients, com_client, runlog, vpn
from .. import colony_client as colony_mod
from ..auth import require_key
from ..settings import settings
from .devices import _flatten

router = APIRouter(tags=["rigs"])

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}\Z")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}\Z")
HOST_RE = re.compile(r"^(?=.{1,253}\Z)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                     r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\Z")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,79}\Z")
ROLES = ("master", "zonal")
UI_TAG = "gs-ui"                     # colony record name `gs-ui:<kind>:<rig>`: a run started here (followed)

# timing (module constants so the tests can shorten them)
POLL = 3.0                           # follower: colony poll period while the play runs
ACCEPT_TIMEOUT = 120.0               # wait for the device's pending auth set after the provision
ACCEPT_POLL = 5.0
TAG_RETRIES = 6                      # the inventory record appears shortly after the accept
RESUME_WINDOW = 600.0                # after a restart, resume followers of runs finished this recently
COM_TTL = 10.0                       # fleet: com state cache
TOTAL_ESTIMATE = {"provision": 110, "orchestrate": 60, "cleanup": 25}

_lock = threading.Lock()
_starting: set[str] = set()          # rigs whose POST is between the 409 check and the colony create
_follow: dict[str, dict] = {}        # run_id -> {rig, kind, active, accepted, mirrored, note}
_finished_logs: dict[str, str] = {}  # run_id -> log of a FINISHED run (never changes)
_com_cache: dict[str, tuple[float, dict]] = {}
_estimate: dict[str, tuple[str, int]] = {}   # kind -> (run_id it was counted from, task count)
_gen = [0]                           # bumped by reset_state: a follower of an older generation stops


def reset_state() -> None:
    """Forget everything gs-api holds in memory (a restart). The tests use it."""
    with _lock:
        _gen[0] += 1
        _starting.clear()
        _follow.clear()
        _finished_logs.clear()
        _com_cache.clear()
        _estimate.clear()


def _colony():
    return colony_mod.colony_client(settings())


def _mender():
    return clients.mender_client(settings())


def _refuse(status: int, detail: str, **more) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail, **more})


# ── validation ──────────────────────────────────────────────────────────────
def check_name(name: str) -> str | None:
    if not isinstance(name, str) or not NAME_RE.match(name):
        return ("name: lower-case letters, digits and '-', starting with a letter or digit, at most 63 "
                "(it becomes the Mender identity, the certificate CN and the tailnet host)")
    return None


def check_address(address: str) -> str | None:
    if not isinstance(address, str) or not address:
        return "address: an IPv4 address or a host name"
    if re.fullmatch(r"[0-9.]+", address):
        try:
            ipaddress.IPv4Address(address)
            return None
        except ValueError:
            return "address: not a valid IPv4 address"
    return None if HOST_RE.match(address) else "address: an IPv4 address or a host name"


def check_user(user: str) -> str | None:
    if not isinstance(user, str) or not USER_RE.match(user):
        return "user: a POSIX login name ([a-z_][a-z0-9_-], at most 32)"
    return None


def check_version(v: str | None, required: bool = False) -> str | None:
    if v is None or v == "":
        return "runtime_version: required" if required else None
    return None if isinstance(v, str) and VERSION_RE.match(v) else "runtime_version: not a valid version"


# ── preflight: one ssh through colony /probe ─────────────────────────────────
_UNREACHABLE = ("timed out", "no route to host", "connection refused", "could not resolve",
                "name or service not known", "network is unreachable", "temporary failure in name resolution",
                "connection closed", "connection reset", "operation timed out", "host is down")


def classify_probe_error(text: str) -> str:
    """ssh's error text (as colony /probe returns it) -> unreachable | auth | host-key-changed | ssh-failed.
    Only a recognised network failure is `unreachable`; anything else is `ssh-failed` with ssh's own words (a
    login banner once hid "Permission denied" and the board was reported unreachable)."""
    t = (text or "").lower()
    if "remote host identification has changed" in t or "host key verification failed" in t:
        return "host-key-changed"
    if "permission denied" in t or "too many authentication failures" in t:
        return "auth"
    if any(k in t for k in _UNREACHABLE):
        return "unreachable"
    return "ssh-failed"


def preflight(col, address: str, user: str) -> dict | None:
    """None when the rig answers ssh as <user> with the fleet key and can sudo; else
    {reason, detail[, pubkey]}. `sudo` is checked only when colony's /probe reports it (newer colony-api)."""
    try:
        info = col.probe(address, user, timeout=30.0)
    except colony_mod.ColonyError as e:
        if e.status == 422:
            return {"reason": "invalid", "detail": f"colony refused the address or user: {e.detail}"}
        if e.status in (401, 403, 404, 500):
            raise HTTPException(status_code=502, detail=f"colony-api preflight [{e.status}]: {e.detail}")
        text = e.detail
        reason = classify_probe_error(text)
    except Exception as e:  # noqa: BLE001: colony itself unreachable
        raise HTTPException(status_code=502, detail=f"colony-api unreachable for the preflight: {e}")
    else:
        sudo = info.get("sudo")
        if sudo == "no":
            return {"reason": "no-sudo",
                    "detail": f"{user}@{address} cannot run sudo without a password: give {user} passwordless "
                              f"sudo (e.g. /etc/sudoers.d/{user}: '{user} ALL=(ALL) NOPASSWD:ALL') or use root"}
        return None
    out = {"reason": reason, "ssh": text[:400]}
    if reason == "auth":
        try:
            pub = col.pubkey()
        except Exception:  # noqa: BLE001
            pub = ""
        out["pubkey"] = pub
        out["detail"] = (f"ssh as {user}@{address} was refused (Permission denied): add the GS key to "
                         f"{user}@{address}:~/.ssh/authorized_keys")
    elif reason == "host-key-changed":
        out["detail"] = (f"the ssh host key of {address} CHANGED since colony first met it (a re-imaged board, "
                         "or another machine at this address). If the board was re-imaged, remove its old "
                         "key from colony's known_hosts and try again")
    elif reason == "unreachable":
        out["detail"] = f"{address} is unreachable over ssh: {text.strip()[-200:]}"
    else:
        out["detail"] = f"ssh as {user}@{address} failed: {text.strip()[-300:]}"
    return out


# ── the runs (colony deployments) ────────────────────────────────────────────
def _active(col, rig: str) -> str | None:
    """The run id of the rig's pending / running run (colony's journal, or a follower still accepting)."""
    with _lock:
        for rid, f in _follow.items():
            if f["rig"] == rig and f["active"] and f.get("phase") == "post":
                return rid
    for d in col.deployments():
        if d.get("rig") == rig and d.get("status") in ("pending", "scheduled", "inprogress"):
            return d.get("id")
    return None


def _log(col, rec: dict) -> str:
    """The run's log: the whole play log when colony serves it (live with a newer colony-api), else the
    record's log_tail. A finished run's log is cached (it never changes)."""
    rid = rec.get("id")
    if rid in _finished_logs:
        return _finished_logs[rid]
    try:
        log = col.log(rid) or ""
    except Exception:  # noqa: BLE001
        log = ""
    if not log:
        log = rec.get("log_tail") or ""
    if rec.get("status") == "finished" and log:
        _finished_logs[rid] = log
    return log


def _rig_class(col, rec: dict) -> tuple[str | None, list]:
    cls, skips = rec.get("rig_class"), rec.get("skipped_by_design")
    if cls is None or skips is None:
        lcls, lskips = runlog.rig_class(_log(col, rec) if rec.get("status") == "finished"
                                        else rec.get("log_tail") or "")
        cls = cls or lcls
        skips = skips if skips is not None else lskips
    return cls, list(skips or [])


def _total_estimate(col, kind: str, deps: list | None = None) -> int:
    """How many tasks a run of this kind has: counted from the newest successful run of the kind."""
    try:
        deps = deps if deps is not None else col.deployments()
        ok = next((d for d in sorted(deps, key=lambda d: d.get("created") or 0, reverse=True)
                   if d.get("kind") == kind and runlog.colony_outcome(d) == "success"), None)
        if ok:
            hit = _estimate.get(kind)
            if hit and hit[0] == ok.get("id"):
                return hit[1]
            n = len(runlog.parse_tasks(_log(col, ok))["tasks"])
            if n:
                _estimate[kind] = (ok.get("id"), n)
                return n
    except Exception:  # noqa: BLE001
        pass
    return TOTAL_ESTIMATE.get(kind, 60)


def _accepted_from_mender(rig: str) -> bool | None:
    try:
        for d in _mender().auth_devices():
            if (d.get("identity_data") or {}).get("device_id") == rig:
                return d.get("status") == "accepted"
        return False
    except Exception:  # noqa: BLE001
        return None


def _status(rec: dict) -> str:
    st = runlog.colony_outcome(rec)
    f = _follow.get(rec.get("id"))
    if st == "success" and f and f["active"]:
        return "running"                 # the GS half (Mender accept + tags) is still going
    return st


def run_summary(rec: dict) -> dict:
    """A run without its log (the list): the fields colony's record carries."""
    prog = rec.get("progress") or {}
    status = _status(rec)
    err = None
    if status == "failure" and (prog.get("failed_task") or prog.get("failed_msg")):
        err = {"task": prog.get("failed_task"), "message": prog.get("failed_msg") or ""}
    return {"run_id": rec.get("id"), "rig": rec.get("rig"), "kind": rec.get("kind"), "status": status,
            "created": rec.get("created"), "started": rec.get("started"), "finished": rec.get("finished"),
            "error": err, "vpn_ip": rec.get("vpn_ip"), "pki": rec.get("pki") or None,
            "rig_class": rec.get("rig_class"), "skipped_by_design": rec.get("skipped_by_design")}


def run_view(col, rec: dict) -> dict:
    """The contract's run: status, step, tasks, error, vpn_ip, pki, rig_class, skipped_by_design (+ accepted
    for a provision). Uses colony's live `progress` when present, the play log otherwise."""
    log = _log(col, rec)
    parsed = runlog.parse_tasks(log)
    prog = rec.get("progress") or {}
    status = _status(rec)
    f = _follow.get(rec.get("id")) or {}
    tasks = parsed["tasks"]
    current = prog.get("current_task") or parsed["current"]
    if status == "running" and runlog.colony_outcome(rec) == "success":
        current = "accept the device in Mender + write its tags"
    done = prog.get("tasks_done")
    if not isinstance(done, int):
        done = len(tasks)
    step = {"index": done + (1 if current and status in ("running", "pending") else 0),
            "total_estimate": max(_total_estimate(col, rec.get("kind") or ""), done),
            "name": current if status in ("running", "pending") else None}
    error = None
    if status == "failure":
        if prog.get("failed_task") or prog.get("failed_msg"):
            error = {"task": prog.get("failed_task"), "message": prog.get("failed_msg") or ""}
        elif parsed["error"]:
            error = parsed["error"]
        else:
            stats = (rec.get("statistics") or {}).get("status") or {}
            if stats.get("aborted"):
                msg = "aborted before it started"
            else:
                tail = [ln for ln in (log or "").strip().splitlines() if ln.strip()][-3:]
                msg = " / ".join(tail)[-400:] or f"the play ended without a result (rc={rec.get('rc')})"
            error = {"task": None, "message": msg}
    cls, skips = _rig_class(col, rec)
    out = {"run_id": rec.get("id"), "rig": rec.get("rig"), "kind": rec.get("kind"), "status": status,
           "created": rec.get("created"), "started": rec.get("started"), "finished": rec.get("finished"),
           "step": step, "tasks": tasks, "error": error,
           "vpn_ip": rec.get("vpn_ip") or runlog.vpn_ip(log),
           "pki": rec.get("pki") or None,
           "rig_class": cls, "skipped_by_design": skips,
           "address": rec.get("host"), "role": (rec.get("extra") or {}).get("role"),
           "log_tail": rec.get("log_tail") if rec.get("log_tail") is not None else (log or "")[-4096:]}
    if rec.get("kind") == "provision":
        if runlog.colony_outcome(rec) in ("failure", "pending", "running"):
            out["accepted"] = False if status == "failure" else None
        elif f:
            out["accepted"] = f.get("accepted")
        elif cls == "opkg":
            out["accepted"] = "not-a-mender-rig"
        else:
            out["accepted"] = _accepted_from_mender(rec.get("rig") or "")
        if f.get("note"):
            out["note"] = f["note"]
    elif f.get("mirrored") is not None:
        out["mirrored"] = f["mirrored"]
    return out


@router.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    col = _colony()
    try:
        rec = col.deployment(run_id)
    except colony_mod.ColonyError as e:
        if e.status == 404:
            raise HTTPException(status_code=404, detail=f"no run {run_id}")
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"colony-api: {e}")
    _ensure_follow(rec)
    return run_view(col, rec)


@router.get("/api/runs")
def list_runs(rig: str | None = None) -> list:
    """The runs of a rig (all runs without `rig`), newest first, as summaries (GET /api/runs/{id} for the
    tasks)."""
    col = _colony()
    try:
        deps = col.deployments()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"colony-api: {e}")
    rows = [d for d in deps if rig is None or d.get("rig") == rig]
    rows.sort(key=lambda d: d.get("created") or 0, reverse=True)
    for d in rows:
        _ensure_follow(d)
    return [run_summary(d) for d in rows]


# ── the follower: the GS half of a run (Mender accept + tags, the base-state mirror) ──
def auto_accept(m, name: str, pubkey_pem: str | None, timeout: float = None,
                interval: float = None) -> tuple[bool, str | None, str | None]:
    """Accept the rig's pending Mender auth set: ONLY a set whose identity device_id == `name` AND whose public
    key equals `pubkey_pem` (the key provision read from the rig over ssh; compared as DER). The identity alone
    is what the device CLAIMS (anyone reaching the gateway can claim it): never enough. A set with another key
    is never accepted, also not for a device already accepted (no key takeover). Polls until `timeout`.
    Returns (accepted, devauth id, reason when not accepted)."""
    timeout = ACCEPT_TIMEOUT if timeout is None else timeout
    interval = ACCEPT_POLL if interval is None else interval
    want = runlog.key_der(pubkey_pem)
    if want is None:
        return False, None, ("the provision reported no Mender public key (COLONY_MENDER_PUBKEY): the GS does "
                             "not accept by identity alone: accept manually after checking the device key")
    deadline = time.time() + timeout
    other_key = False
    while True:
        try:
            for d in m.auth_devices():
                idd = d.get("identity_data") or {}
                if not isinstance(idd, dict) or idd.get("device_id") != name:
                    continue
                for a in d.get("auth_sets") or []:
                    if (a.get("identity_data") or idd).get("device_id") != name:
                        continue
                    same = runlog.key_der(a.get("pubkey")) == want
                    if a.get("status") == "accepted" and same:
                        return True, d["id"], None                 # already accepted with the rig's own key
                    if a.get("status") == "pending":
                        if same:
                            m.accept_device(d["id"], a["id"])
                            return True, d["id"], None
                        other_key = True
        except Exception:  # noqa: BLE001: Mender hiccup: try again until the deadline
            pass
        if time.time() >= deadline:
            why = (f"no pending Mender auth set with identity device_id={name} and the key the rig reported "
                   f"within {int(timeout)} s")
            if other_key:
                why += ("; a pending set with this identity but ANOTHER key was NOT accepted (someone else may "
                        "be claiming the name)")
            return False, None, why + ": accept manually"
        time.sleep(interval)


def _write_tags(m, dev_id: str, tags: dict) -> bool:
    for _ in range(TAG_RETRIES):
        try:
            m.set_tags(dev_id, tags)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(ACCEPT_POLL)
    return False


def _after_provision(col, rec: dict, f: dict) -> None:
    rig = rec.get("rig") or ""
    cls, skips = _rig_class(col, rec)
    if cls == "opkg":
        f["accepted"] = "not-a-mender-rig"
        f["note"] = "opkg rig: base plane only, no Mender client (nothing to accept)"
        return
    m = _mender()
    key = rec.get("mender_pubkey") or runlog.mender_pubkey(_log(col, rec))
    ok, dev_id, why = auto_accept(m, rig, key)
    f["accepted"] = ok
    if not ok:
        f["note"] = why
        return
    extra = rec.get("extra") or {}
    vip = rec.get("vpn_ip") or runlog.vpn_ip(_log(col, rec))
    tags = {"name": rig, "role": extra.get("role") or "master", "rig_class": cls or "debian"}
    if rec.get("host"):
        tags["local_ip"] = rec["host"]
    if vip:
        tags["remote_ip"] = vip
    f["tags"] = _write_tags(m, dev_id, tags)
    from .deployments import _mirror_base_state
    f["mirrored"] = _mirror_base_state(settings(), rig, rec, dev_id)
    if not f["tags"]:
        f["note"] = "accepted, but the tags (name, role, addresses) were not written: Mender inventory refused"


def _after_orchestrate(col, rec: dict, f: dict) -> None:
    dev = _mender_device(rec.get("rig") or "")
    if dev:
        from .deployments import _mirror_base_state
        f["mirrored"] = _mirror_base_state(settings(), rec.get("rig") or "", rec, dev.get("id"))


def _run_follower(run_id: str) -> None:
    col = _colony()
    f = _follow[run_id]
    gen = f["gen"]
    try:
        deadline = time.time() + 3600
        rec: dict = {}
        while time.time() < deadline:
            if _gen[0] != gen:
                return                       # this process "restarted" (reset_state): the new one resumes
            try:
                rec = col.deployment(run_id)
            except colony_mod.ColonyError as e:
                if e.status == 404:          # pruned from the journal: nothing left to follow
                    return
            except Exception:  # noqa: BLE001: colony restarting: keep following
                pass
            if rec.get("status") == "finished":
                break
            time.sleep(POLL)
        if rec.get("status") != "finished":
            f["note"] = "the play did not finish within an hour: the GS stopped following it"
            return
        if f.get("vpn_minted") and rec.get("kind") == "provision" and not (
                rec.get("vpn_ip") or runlog.vpn_ip(_log(col, rec))):
            f["note"] = ("a VPN key was minted but colony reported no tailnet address (old colony-api image, or "
                         "the join was skipped)")
        if _gen[0] != gen:
            return
        if runlog.colony_outcome(rec) == "success":
            f["phase"] = "post"
            if rec.get("kind") == "provision":
                _after_provision(col, rec, f)
            elif rec.get("kind") == "orchestrate":
                _after_orchestrate(col, rec, f)
        elif rec.get("kind") == "provision":
            f["accepted"] = False
    except Exception as e:  # noqa: BLE001: never kill the process; the run state stays in colony
        f["note"] = f"GS follow-up failed: {e}"
        if f.get("kind") == "provision" and f.get("accepted") is None:
            f["accepted"] = False
    finally:
        f["active"] = False


def _start_follower(run_id: str, rig: str, kind: str, vpn_minted: bool = False) -> None:
    with _lock:
        if run_id in _follow:
            return
        _follow[run_id] = {"rig": rig, "kind": kind, "active": True, "phase": "play", "accepted": None,
                           "vpn_minted": vpn_minted, "gen": _gen[0]}
    threading.Thread(target=_run_follower, args=(run_id,), daemon=True, name=f"follow-{run_id[:8]}").start()


def _ensure_follow(rec: dict) -> None:
    """Resume the follower of a run this GS started (record name gs-ui:...) that is still running or finished
    within RESUME_WINDOW, when this process does not know it (after a restart). Idempotent."""
    rid = rec.get("id")
    if not rid or rid in _follow or not str(rec.get("name") or "").startswith(UI_TAG + ":"):
        return
    if rec.get("kind") not in ("provision", "orchestrate"):
        return
    fin = rec.get("finished")
    if rec.get("status") == "finished" and not (isinstance(fin, (int, float)) and time.time() - fin < RESUME_WINDOW):
        return
    _start_follower(rid, rec.get("rig") or "", rec.get("kind"))


def resume_followers() -> None:
    """At start-up: resume the followers of the runs this GS started (best effort)."""
    try:
        for d in _colony().deployments():
            _ensure_follow(d)
    except Exception:  # noqa: BLE001
        pass


# ── POST /api/rigs (Add device) and POST /api/rigs/{name}/deploy ─────────────
class AddRig(BaseModel):
    name: str
    address: str
    user: str = "debian"
    role: str = "master"
    runtime_version: str | None = None


class DeployRig(BaseModel):
    runtime_version: str


def _conflict(rig: str, run_id: str | None) -> JSONResponse:
    return _refuse(409, f"{rig}: a run is already pending or in progress" + (f" ({run_id})" if run_id else
                   " (its preflight is running)"), run_id=run_id)


def _colony_409(rig: str, e: "colony_mod.ColonyError") -> JSONResponse:
    b = e.body or {}
    rid = b.get("run_id") or b.get("id")
    if not rid and isinstance(b.get("detail"), dict):
        rid = b["detail"].get("run_id") or b["detail"].get("id")
    if not rid:
        m = re.search(r"\b([0-9a-f]{32})\b", e.detail or "")
        rid = m.group(1) if m else None
    return _conflict(rig, rid)


def default_runtime_version(col, name: str) -> str | None:
    """The runtime plane release a provision takes its OTA modules from when the operator named none: the
    version this rig last ran successfully (re-provision keeps it), else the newest published one. None when
    the plane is empty or unreadable (the provision then fails in colony with its own message).
    Regression (live T1, 2026-11-19): Add device sent no version and colony found no OTA modules."""
    try:
        for r in sorted(col.deployments(), key=lambda r: float(r.get("created") or 0), reverse=True):
            if r.get("rig") == name and ((r.get("statistics") or {}).get("status") or {}).get("success"):
                v = (r.get("extra") or {}).get("runtime_version") or r.get("runtime_version")
                if v and VERSION_RE.match(str(v)):
                    return str(v)
    except Exception:  # noqa: BLE001
        pass
    try:
        rel = [r for r in clients.plane_client(settings()).runtime_catalog() if "_error" not in r and r.get("version")]
    except Exception:  # noqa: BLE001
        return None
    def _k(v):
        return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", str(v)))
    rel.sort(key=lambda r: _k(r["version"]), reverse=True)
    return str(rel[0].get("key") or rel[0]["version"]) if rel else None


@router.post("/api/rigs", status_code=202, dependencies=[Depends(require_key)])
def add_rig(req: AddRig):
    """Add device = PREFLIGHT (one ssh as <user>@<address> with the fleet key, through colony /probe) then the
    colony provision, asynchronously. 422 {detail, reason[, pubkey]} on a failed preflight or a bad field;
    409 {detail, run_id} when the rig already has a pending / running run."""
    for err in (check_name(req.name), check_address(req.address), check_user(req.user),
                check_version(req.runtime_version)):
        if err:
            return _refuse(422, err, reason="invalid")
    if req.role not in ROLES:
        return _refuse(422, "role: master or zonal", reason="invalid")
    name = req.name
    with _lock:
        if name in _starting:
            return _conflict(name, None)
        _starting.add(name)
    try:
        col = _colony()
        try:
            rid = _active(col, name)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"colony-api: {e}")
        if rid:
            return _conflict(name, rid)
        bad = preflight(col, req.address, req.user)
        if bad:
            return _refuse(422, bad.pop("detail"), **bad)
        secrets, vpn_note = None, "not configured (TS_API_TOKEN_FILE): provision WITHOUT the VPN join"
        if vpn.enabled():
            try:
                secrets, vpn_note = {"vpn_authkey": vpn.mint_rig_key(name)}, "key minted"
            except Exception as e:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=f"fleet VPN: cannot mint the rig key: {e}")
        extra = {"role": req.role, "mender_device_id": name, "ansible_user": req.user}
        ver = req.runtime_version or default_runtime_version(col, name)
        if ver:
            extra["runtime_version"] = ver
        try:
            dep = col.create(name, "provision", host=req.address, extra=extra, secrets=secrets,
                             name=f"{UI_TAG}:provision:{name}")
        except colony_mod.ColonyError as e:
            if e.status == 409:
                return _colony_409(name, e)
            raise HTTPException(status_code=502, detail=f"colony provision '{name}': {e.detail or e}")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"colony provision '{name}': {e}")
        _start_follower(dep["id"], name, "provision", vpn_minted=secrets is not None)
        return {"run_id": dep["id"], "rig": name, "kind": "provision", "vpn": vpn_note}
    finally:
        with _lock:
            _starting.discard(name)


def _mender_device(rig: str) -> dict | None:
    """The Mender inventory device of a rig: identity device_id == rig, else the name tag == rig."""
    try:
        m = _mender()
        devs = m.devices()
    except Exception:  # noqa: BLE001
        return None
    for d in devs:
        attrs = {a.get("name"): a.get("value") for a in d.get("attributes", []) or []}
        if attrs.get("device_id") == rig:
            return d
    for d in devs:
        for a in d.get("attributes", []) or []:
            if a.get("name") == "name" and a.get("scope", "tags") == "tags" and a.get("value") == rig:
                return d
    return None


def _last(runs: list, kind: str | None = None, ok: bool = False) -> dict | None:
    for d in sorted(runs, key=lambda d: d.get("created") or 0, reverse=True):
        if kind and d.get("kind") != kind:
            continue
        if ok and runlog.colony_outcome(d) != "success":
            continue
        return d
    return None


@router.post("/api/rigs/{name}/deploy", status_code=202, dependencies=[Depends(require_key)])
def deploy_rig(name: str, req: DeployRig):
    """Deploy a runtime = colony orchestrate to the rig's REACHABLE address (the Mender device's remote_ip /
    local_ip; a colony-only rig: its last provision's tailnet address, else its address). 409 as Add device."""
    for err in (check_name(name), check_version(req.runtime_version, required=True)):
        if err:
            return _refuse(422, err, reason="invalid")
    with _lock:
        if name in _starting:
            return _conflict(name, None)
        _starting.add(name)
    try:
        col = _colony()
        try:
            runs = [d for d in col.deployments() if d.get("rig") == name]
            rid = _active(col, name)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"colony-api: {e}")
        if rid:
            return _conflict(name, rid)
        prov = _last(runs, "provision", ok=True) or _last(runs, "provision")
        pextra = (prov or {}).get("extra") or {}
        dev = _mender_device(name)
        if dev:
            host = addressing.reachable_ip(dev)
            tags = {a.get("name"): a.get("value") for a in dev.get("attributes", []) or []
                    if a.get("scope", "tags") == "tags"}
            role = tags.get("role") or pextra.get("role") or "master"
        else:
            host = (prov or {}).get("vpn_ip") or (prov or {}).get("host")
            role = pextra.get("role") or "master"
        if not dev and not prov:
            return _refuse(404, f"no rig '{name}' (neither a Mender device nor a colony provision)",
                           reason="unknown-rig")
        if not host:
            return _refuse(422, f"{name}: no reachable address (no remote_ip / local_ip tag): re-provision it "
                                "(Add device)", reason="no-address")
        extra = {"role": role, "runtime_version": req.runtime_version}
        if pextra.get("ansible_user"):
            extra["ansible_user"] = pextra["ansible_user"]
        try:
            dep = col.create(name, "orchestrate", host=host, extra=extra, name=f"{UI_TAG}:orchestrate:{name}")
        except colony_mod.ColonyError as e:
            if e.status == 409:
                return _colony_409(name, e)
            raise HTTPException(status_code=502, detail=f"colony orchestrate '{name}': {e.detail or e}")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"colony orchestrate '{name}': {e}")
        _start_follower(dep["id"], name, "orchestrate")
        return {"run_id": dep["id"], "rig": name, "kind": "orchestrate"}
    finally:
        with _lock:
            _starting.discard(name)


# ── GET /api/fleet: ONE list ─────────────────────────────────────────────────
def _com(target: str | None) -> dict:
    if not target:
        return {"state": "unknown", "detail": "no reachable address (no remote_ip / local_ip)"}
    now = time.time()
    hit = _com_cache.get(target)
    if hit and now - hit[0] < COM_TTL:
        return hit[1]
    try:
        st = com_client.com_state(target, timeout=4.0)
    except Exception as e:  # noqa: BLE001
        st = {"state": "unknown", "detail": com_client.short_error(e)}
    _com_cache[target] = (now, st)
    return st


def _last_run(runs: list) -> dict | None:
    d = _last(runs)
    if not d:
        return None
    return {"run_id": d.get("id"), "kind": d.get("kind"), "status": _status(d), "finished": d.get("finished")}


@router.get("/api/fleet")
def fleet(response: Response, com: bool = True) -> list:
    """Every device in ONE list: each Mender device (any auth status) once, merged with its colony runs by
    the ONE identity (device_id == rig name, or the name tag), and every colony rig with no Mender device
    (an opkg rig like the SK: source "colony", auth_status null). com = the live com state (mTLS /
    plaintext / down), cached COM_TTL s; com=false skips it."""
    s = settings()
    errors: dict[str, str] = {}
    col = colony_mod.colony_client(s)
    try:
        deps = col.deployments()
    except Exception as e:  # noqa: BLE001
        deps, errors["colony"] = [], str(e)
    for d in deps:
        _ensure_follow(d)
    by_rig: dict[str, list] = {}
    for d in deps:
        if d.get("rig"):
            by_rig.setdefault(d["rig"], []).append(d)
    auth, inv = [], {}
    try:
        m = clients.mender_client(s)
        auth = m.auth_devices()
        try:
            inv = {d.get("id"): d for d in m.devices()}
        except Exception as e:  # noqa: BLE001
            errors["mender_inventory"] = str(e)
    except Exception as e:  # noqa: BLE001
        errors["mender"] = str(e)

    rows: list[dict] = []
    seen: set[str] = set()
    for a in auth:
        dev = inv.get(a.get("id"))
        flat = _flatten(dev) if dev else {"attributes": {}}
        attrs = flat.get("attributes") or {}
        idd = a.get("identity_data") or {}
        ident = idd.get("device_id") if isinstance(idd, dict) else None
        name = attrs.get("name") or ident or flat.get("name") or a.get("id")
        rig = next((k for k in (ident, attrs.get("name")) if k and k in by_rig), ident or name)
        runs = by_rig.get(rig, [])
        seen.update(k for k in (ident, attrs.get("name"), rig) if k)
        prov = _last(runs, "provision", ok=True)
        pextra = (prov or {}).get("extra") or {}
        rows.append({
            "name": name, "source": "mender+colony", "mender_id": a.get("id"),
            "auth_status": a.get("status"),
            "rig_class": attrs.get("rig_class") or (prov or {}).get("rig_class") or "debian",
            "role": attrs.get("role") or pextra.get("role"),
            "address": attrs.get("local_ip") or (prov or {}).get("host"),
            "vpn_ip": attrs.get("remote_ip") or (prov or {}).get("vpn_ip"),
            "base_version": attrs.get("base_version") or (
                ((_last([d for d in runs if d.get("kind") in ("provision", "orchestrate")], ok=True) or {})
                 .get("extra") or {}).get("runtime_version")),
            "last_run": _last_run(runs),
            "_target": addressing.reachable_ip(dev) if dev else None,
            "artifact": flat.get("artifact"),
        })
    for rig, runs in by_rig.items():
        if rig in seen:
            continue
        prov = _last(runs, "provision", ok=True) or _last(runs, "provision")
        okrun = _last([d for d in runs if d.get("kind") in ("provision", "orchestrate")], ok=True)
        cls = (prov or {}).get("rig_class")
        if prov and cls is None and prov.get("status") == "finished":
            cls = _rig_class(col, prov)[0]
        pextra = (prov or {}).get("extra") or {}
        vip = (prov or {}).get("vpn_ip")
        addr = (prov or {}).get("host")
        rows.append({
            "name": rig, "source": "colony", "mender_id": None, "auth_status": None,
            "rig_class": cls, "role": pextra.get("role"), "address": addr, "vpn_ip": vip,
            "base_version": ((okrun or {}).get("extra") or {}).get("runtime_version"),
            "last_run": _last_run(runs), "_target": vip or addr, "artifact": None,
        })
    targets = [r.pop("_target") for r in rows]
    if com and rows:
        with ThreadPoolExecutor(max_workers=min(8, len(rows))) as ex:
            states = list(ex.map(_com, [f"{t}:{addressing.COM_PORT}" if t else None for t in targets]))
    else:
        states = [{"state": "unknown", "detail": "not probed (com=false)"}] * len(rows)
    for r, st in zip(rows, states):
        r["com"] = st
    rows.sort(key=lambda r: r["name"] or "")
    if errors:                      # a source that failed does not sink the list: said in a header
        txt = "; ".join(f"{k}: {v}" for k, v in errors.items())
        response.headers["X-GS-Fleet-Errors"] = " ".join(txt.encode("ascii", "replace").decode().split())[:1000]
    return rows

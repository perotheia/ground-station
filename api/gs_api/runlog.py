"""Read a colony run (an ansible play) for the operator: the task list, the failing task, the rig class.

Pure functions over the play's text log (colony GET /deployments/{id}/log, or the record's `log_tail` while it
runs) and the colony record. Nothing here talks to a service.
"""
from __future__ import annotations

import json
import re

_TASK_RE = re.compile(r"^(?:TASK|RUNNING HANDLER) \[(?P<name>.*)\]\s*\**\s*$")
_RESULT_RE = re.compile(r"^(?P<kind>ok|changed|skipping|failed|fatal|included|unreachable)\s*:")
_RECAP_RE = re.compile(r"^PLAY RECAP\b")
_RIG_CLASS_RE = re.compile(r"rig_class=(?P<cls>[a-z]+); skipped by design: (?P<skips>[^\"\n]*)")
_VPN_RE = re.compile(r"COLONY_VPN_IP \S+ (\d+\.\d+\.\d+\.\d+)")

_RANK = {"skipped": 0, "ok": 1, "changed": 2, "failed": 3}
_KIND = {"ok": "ok", "included": "ok", "changed": "changed", "skipping": "skipped",
         "failed": "failed", "fatal": "failed", "unreachable": "failed"}


def _message(line: str) -> str:
    """The readable part of a `fatal: [h]: FAILED! => {...}` line: the msg (or stderr), else the raw tail."""
    _, _, rest = line.partition("=>")
    rest = rest.strip()
    try:
        d = json.loads(rest)
        if isinstance(d, dict):
            for k in ("msg", "stderr", "module_stderr", "reason"):
                v = d.get(k)
                if v:
                    return (v if isinstance(v, str) else json.dumps(v)).strip()[:500]
    except ValueError:
        pass
    return (rest or line).strip()[:500]


def parse_tasks(log: str) -> dict:
    """The play's tasks in order, each with its result (ok | changed | skipped | failed: the worst over the
    hosts / loop items; an ignored failure counts as ok), the task still running (a TASK header with no
    result yet), the first failure {task, message} and whether the PLAY RECAP was reached."""
    tasks: list[dict] = []
    cur: dict | None = None
    error = None
    recap = False
    for raw in (log or "").splitlines():
        line = raw.rstrip()
        if _RECAP_RE.match(line):
            recap = True
            cur = None
            continue
        m = _TASK_RE.match(line)
        if m:
            cur = {"name": m.group("name").strip(), "result": None}
            tasks.append(cur)
            continue
        if cur is None:
            continue
        if line.strip() == "...ignoring":
            if cur.get("result") == "failed":
                cur["result"] = "ok"
                if error and error.get("task") == cur["name"]:
                    error = None
            continue
        r = _RESULT_RE.match(line)
        if not r:
            continue
        res = _KIND[r.group("kind")]
        if cur["result"] is None or _RANK[res] > _RANK[cur["result"]]:
            cur["result"] = res
        if res == "failed" and error is None:
            error = {"task": cur["name"], "message": _message(line)}
    done = [t for t in tasks if t["result"] is not None]
    running = tasks[-1]["name"] if tasks and tasks[-1]["result"] is None and not recap else None
    return {"tasks": done, "current": running, "error": error, "recap": recap}


def rig_class(log: str) -> tuple[str | None, list[str] | None]:
    """(rig_class, skipped_by_design) from provision's `rig_class=opkg; skipped by design: a, b` line."""
    m = None
    for m in _RIG_CLASS_RE.finditer(log or ""):
        pass
    if not m:
        return None, None
    skips = [s.strip() for s in m.group("skips").split(",") if s.strip() and s.strip() != "nothing"]
    return m.group("cls"), skips


def vpn_ip(log: str) -> str | None:
    m = _VPN_RE.search(log or "")
    return m.group(1) if m else None


def colony_outcome(rec: dict) -> str:
    """colony's Mender-shaped record -> pending | running | success | failure."""
    st = rec.get("status")
    if st in ("pending", "scheduled"):
        return "pending"
    if st == "inprogress":
        return "running"
    stats = (rec.get("statistics") or {}).get("status") or {}
    if (stats.get("success") or 0) > 0 and not (stats.get("failure") or 0) and not (stats.get("aborted") or 0):
        return "success"
    return "failure"

"""colony_client — gs-api's client to colony-api (the BASE deployment authority).

colony-api is Mender-Management-API-shaped on purpose, so this is a thin urllib
client (no requests dep — same discipline as fleet.py). gs-api fans
/api/deployments out to BOTH this and Mender, then merges + authority-tags.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request


class Colony:
    def __init__(self, base: str, key: str = "") -> None:
        self.base = base.rstrip("/")
        self.key = key

    def _req(self, method: str, path: str, body: dict | None = None,
             timeout: float = 10.0) -> tuple[int, bytes]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.key:
            req.add_header("X-Colony-Key", self.key)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def rigs(self) -> list:
        st, data = self._req("GET", "/rigs")
        if st != 200:
            raise RuntimeError(f"colony rigs [{st}]: {data.decode(errors='replace')[:200]}")
        return json.loads(data or b"{}").get("rigs", [])

    def deployments(self) -> list:
        st, data = self._req("GET", "/deployments")
        if st != 200:
            raise RuntimeError(f"colony deployments [{st}]: {data.decode(errors='replace')[:200]}")
        return json.loads(data or b"{}").get("deployments", [])

    def deployment(self, did: str) -> dict:
        st, data = self._req("GET", f"/deployments/{did}")
        if st != 200:
            raise RuntimeError(f"colony deployment [{st}]: {data.decode(errors='replace')[:200]}")
        return json.loads(data or b"{}")

    def create(self, rig: str, kind: str = "orchestrate",
               schedule: float | None = None, name: str | None = None,
               host: str | None = None) -> dict:
        st, data = self._req("POST", "/deployments",
                             {"rig": rig, "kind": kind,
                              "schedule": schedule, "name": name, "host": host})
        if st not in (200, 201):
            raise RuntimeError(f"colony create [{st}]: {data.decode(errors='replace')[:200]}")
        return json.loads(data or b"{}")

    def log(self, did: str) -> str:
        st, data = self._req("GET", f"/deployments/{did}/log")
        if st != 200:
            raise RuntimeError(f"colony log [{st}]: {data.decode(errors='replace')[:200]}")
        return json.loads(data or b"{}").get("log", "")


def colony_client(s) -> Colony:
    return Colony(s.colony_api, s.colony_api_key)

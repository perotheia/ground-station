"""The ONE rule for the address the GS uses to reach a rig (com :7700, ssh via colony).

Order: the device's `remote_ip` tag (the fleet-tailnet address recorded at provisioning), then its `local_ip`
tag (the address the GS enrolled it with), then NOTHING (an explicit error). Never an inventory interface
address (`ipv4_eth0`, `ipv4_usb0`, ...): those are the board's own NICs, e.g. the AI-64 reports
192.168.64.2 (a closed usb/eth interface) first, which the GS cannot reach. This was a repeat defect: every
com endpoint is built here.

Works on a raw Mender inventory device ({"attributes": [{name, value, scope}]}) and on the GS flattened record
({"attributes": {name: value}} or top-level remote_ip / local_ip).
"""
from __future__ import annotations

COM_PORT = 7700
ORDER = ("remote_ip", "local_ip")


class NoReachableAddress(LookupError):
    """The device has neither a remote_ip nor a local_ip tag."""


def _one(v) -> str | None:
    if isinstance(v, list):
        v = v[0] if v else None
    if v is None:
        return None
    v = str(v).strip().split(",")[0].strip().split("/")[0]      # one address, no CIDR suffix
    return v or None


def _tags(dev: dict) -> dict:
    """{name: value} of the device's tag attributes (scope=tags; an attribute with no scope counts)."""
    attrs = dev.get("attributes")
    if isinstance(attrs, dict):                                 # a flattened GS record
        out = dict(attrs)
        for k in ORDER:
            if dev.get(k) and k not in out:
                out[k] = dev[k]
        return out
    out = {}
    for a in attrs or []:
        if a.get("scope", "tags") == "tags":
            out[a.get("name")] = a.get("value")
    return out


def reachable_ip(dev: dict | None) -> str | None:
    """remote_ip, else local_ip, else None. Never an ipv4_* inventory value."""
    if not dev:
        return None
    tags = _tags(dev)
    for k in ORDER:
        ip = _one(tags.get(k))
        if ip:
            return ip
    return None


def com_endpoint(dev: dict | None, port: int = COM_PORT) -> str:
    """`<reachable address>:7700`; raises NoReachableAddress when the device has no remote_ip / local_ip."""
    ip = reachable_ip(dev)
    if not ip:
        name = (dev or {}).get("id") or "device"
        raise NoReachableAddress(f"{name}: no reachable address (no remote_ip / local_ip tag); "
                                 "re-provision it from the GS (Add device) to record one")
    return f"{ip}:{port}"

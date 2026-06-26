"""com_client — GS's gRPC client to a rig's com UcmView (the ara::com OTA edge).

GS is the OTA CLIENT: after a Mender deployment lands the bits, GS drives the
on-device UCM agent's lifecycle and watches its FSM. This reaches com's UcmView at
<rig>:7700 (the same endpoint rtdb uses), calling RequestUpdate + GetProgress.

The bridge stubs are compiled from gs_api/proto/ucm_view.proto AT IMPORT (via
grpcio-tools) so the repo carries no generated code and the proto stays the single
source. TLS mirrors com's opt-in (THEIA_COM_TLS_CA) — default insecure for dev.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

_PROTO = Path(__file__).resolve().parent / "proto" / "ucm_view.proto"
_pb = None
_pbg = None


def _ensure_stubs():
    """Compile ucm_view.proto → _pb2 / _pb2_grpc once, into a temp dir on sys.path."""
    global _pb, _pbg
    if _pb is not None:
        return
    from grpc_tools import protoc  # noqa: PLC0415
    outdir = Path(tempfile.gettempdir()) / "gs_ucm_stubs"
    outdir.mkdir(exist_ok=True)
    rc = protoc.main([
        "protoc",
        f"-I{_PROTO.parent}",
        f"--python_out={outdir}",
        f"--grpc_python_out={outdir}",
        str(_PROTO),
    ])
    if rc != 0:
        raise RuntimeError("protoc failed compiling ucm_view.proto")
    if str(outdir) not in sys.path:
        sys.path.insert(0, str(outdir))
    _pb = importlib.import_module("ucm_view_pb2")
    _pbg = importlib.import_module("ucm_view_pb2_grpc")


def _channel(target: str):
    import grpc  # noqa: PLC0415
    ca = os.environ.get("THEIA_COM_TLS_CA")
    if ca and Path(ca).is_file():
        creds = grpc.ssl_channel_credentials(Path(ca).read_bytes())
        return grpc.secure_channel(target, creds)
    return grpc.insecure_channel(target)


# UcmState ordinal → name (mirrors system_services_ucm.UcmState).
UCM_STATE = ["IDLE", "DOWNLOADED", "VALIDATED", "STAGED", "INSTALLING",
             "RESTARTING", "VERIFYING", "ACTIVE", "ROLLBACK"]


def request_update(target: str, name: str, version: str, *, kind: int = 0,
                   scope: int = 1, artifact_path: str = "", signature: str = "",
                   timeout: float = 10.0) -> dict:
    """Drive a UCM update on the rig at <target> (host:7700). scope default PARTIAL
    (an app FC); FULL for the whole platform. Returns {status, accepted}."""
    _ensure_stubs()
    with _channel(target) as ch:
        stub = _pbg.UcmViewStub(ch)
        req = _pb.UcmRequestUpdateCall(
            name=name, version=version, kind=kind, scope=scope,
            artifact_path=artifact_path, signature=signature)
        rep = stub.RequestUpdate(req, timeout=timeout)
        return {"status": rep.status, "accepted": rep.status == 0,
                "status_text": {0: "accepted", 1: "reject", 2: "not-ready"}.get(rep.status, "?")}


def get_progress(target: str, timeout: float = 8.0) -> dict:
    """The latest UcmProgress (the ECU-lifecycle plane) from the rig at <target>."""
    _ensure_stubs()
    with _channel(target) as ch:
        stub = _pbg.UcmViewStub(ch)
        p = stub.GetProgress(_pb.UcmProgressCall(), timeout=timeout)
        return {
            "ok": p.ok,
            "state": p.state,
            "state_name": UCM_STATE[p.state] if p.state < len(UCM_STATE) else str(p.state),
            "version": p.version,
            "kind": p.kind,
            "scope": p.scope,
            "detail": p.detail,
            "ts_ns": p.ts_ns,
        }

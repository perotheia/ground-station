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
_SV_PROTO = Path(__file__).resolve().parent / "proto" / "supervisor_view.proto"
_pb = None
_pbg = None
_svpb = None
_svpbg = None


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


def _ensure_sv_stubs():
    """Compile supervisor_view.proto → the ListMachines stub (the Connect com-half)."""
    global _svpb, _svpbg
    if _svpb is not None:
        return
    from grpc_tools import protoc  # noqa: PLC0415
    outdir = Path(tempfile.gettempdir()) / "gs_sv_stubs"
    outdir.mkdir(exist_ok=True)
    rc = protoc.main([
        "protoc",
        f"-I{_SV_PROTO.parent}",
        f"--python_out={outdir}",
        f"--grpc_python_out={outdir}",
        str(_SV_PROTO),
    ])
    if rc != 0:
        raise RuntimeError("protoc failed compiling supervisor_view.proto")
    if str(outdir) not in sys.path:
        sys.path.insert(0, str(outdir))
    _svpb = importlib.import_module("supervisor_view_pb2")
    _svpbg = importlib.import_module("supervisor_view_pb2_grpc")


def list_machines(target: str, timeout: float = 6.0) -> list[dict]:
    """The Observability cluster as com sees it (ListMachines on the aggregator at
    <target>:7700). Each {instance, name, present}. The Connect com-half is
    observational: a board joins by self-publishing over TIPC topology — GS just
    confirms it shows up `present=true`. Raises on an unreachable aggregator."""
    _ensure_sv_stubs()
    with _channel(target) as ch:
        stub = _svpbg.SupervisorViewStub(ch)
        rep = stub.ListMachines(_svpb.ListMachinesCall(), timeout=timeout)
        return [{"instance": m.instance, "name": m.name, "present": m.present,
                 # the LIVE base version reported by the supervisor (stateless) —
                 # empty until a runtime with release_version is deployed.
                 "release_version": (m.info.release_version
                                     if m.HasField("info") else "") or "",
                 "machine_name": (m.info.machine_name if m.HasField("info") else "") or ""}
                for m in rep.machines]


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
# SmState ordinal → name (mirrors system_services_sm.SmState).
SM_STATE = ["OFF", "STARTING", "RUNNING", "DEGRADED", "UPDATE", "SHUTDOWN"]
# CampaignState ordinal → name (mirrors system_services_vucm.CampaignState; the
# WIRE order: CONFIRMING appended =7, AWAITING_COMMIT =8 — last, not mid-list).
CAMPAIGN_STATE = ["IDLE", "PLANNING", "AUTHORIZING", "INSTALLING", "VALIDATING",
                  "DONE", "ROLLBACK", "CONFIRMING", "AWAITING_COMMIT"]


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


def check_for_campaign(target: str, campaign_id: str, version: str, *,
                       scope: int = 0, timeout: float = 10.0) -> dict:
    """L4-B: start a VEHICLE campaign on the coordinator board at <target> (host:7700).
    V-UCM fans the package to every board's UCM + holds CMP_CONFIRMING until ALL are
    PROVISIONAL, then fans the aggregate Confirm. Returns {accepted, state}."""
    _ensure_stubs()
    with _channel(target) as ch:
        stub = _pbg.VucmViewStub(ch)
        req = _pb.VucmCampaignCall(campaign_id=campaign_id, version=version, scope=scope)
        rep = stub.CheckForCampaign(req, timeout=timeout)
        return {"accepted": rep.accepted == 1, "state": rep.state,
                "state_name": CAMPAIGN_STATE[rep.state] if rep.state < len(CAMPAIGN_STATE)
                              else str(rep.state)}


def campaign_status(target: str, timeout: float = 8.0) -> dict:
    """L4-B: the current vehicle CampaignProgress (the aggregate-barrier state) from
    the coordinator board at <target>."""
    _ensure_stubs()
    with _channel(target) as ch:
        stub = _pbg.VucmViewStub(ch)
        s = stub.GetCampaignStatus(_pb.VucmStatusCall(), timeout=timeout)
        return {
            "valid": s.valid,
            "state": s.state,
            "state_name": CAMPAIGN_STATE[s.state] if s.state < len(CAMPAIGN_STATE)
                          else str(s.state),
            "campaign_id": s.campaign_id,
            "version": s.version,
            "detail": s.detail,
            "ts_ns": s.ts_ns,
        }


def campaign_decide(target: str, campaign_id: str, rollback: bool,
                    timeout: float = 10.0) -> dict:
    """L4-C operator commit/rollback (step 7): once the campaign is AWAITING_COMMIT,
    commit (rollback=False → V-UCM fans Confirm) or roll back (rollback=True → fans
    Cancel). Returns {accepted, state}."""
    _ensure_stubs()
    with _channel(target) as ch:
        stub = _pbg.VucmViewStub(ch)
        rep = stub.Decide(_pb.VucmDecisionCall(campaign_id=campaign_id, rollback=rollback),
                          timeout=timeout)
        return {"accepted": rep.accepted == 1, "state": rep.state,
                "state_name": CAMPAIGN_STATE[rep.state] if rep.state < len(CAMPAIGN_STATE)
                              else str(rep.state)}


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
            # the SM-session plane
            "sm_ok": p.sm_ok,
            "sm_state": p.sm_state,
            "sm_state_name": SM_STATE[p.sm_state] if p.sm_state < len(SM_STATE) else str(p.sm_state),
            "sm_ts_ns": p.sm_ts_ns,
        }

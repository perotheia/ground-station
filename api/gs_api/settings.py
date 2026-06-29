"""Settings — env-driven config for the GS backend (12-factor; no secrets in code).

The backend reaches TWO upstreams:
  MENDER_*  — the Mender GW Management API (the OTA control plane).
  S3_*      — the MinIO distribution planes (our runtime + app vendoring catalog).

All overridable by env so the same image runs in the compose (service DNS names),
against dalek's live GW, or in CI. Defaults match ground-station/docker-compose.yml.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # ---- Mender GW (Management API) ----
    mender_server: str = os.environ.get("MENDER_SERVER", "https://localhost")
    mender_token: str = os.environ.get("MENDER_TOKEN", "")
    # The local GW uses a self-signed cert; dev skips verification (the real
    # server pins a CA). Default insecure=true since the bundled GW is self-signed.
    mender_insecure: bool = os.environ.get("MENDER_INSECURE", "1") not in ("0", "false", "")
    mender_flavor: str = os.environ.get("MENDER_FLAVOR", "oss")

    # ---- MinIO distribution planes (S3) ----
    s3_endpoint: str = os.environ.get("S3_ENDPOINT", "http://gs-minio:9000")
    s3_access_key: str = os.environ.get("MINIO_USER", "theia")
    s3_secret_key: str = os.environ.get("MINIO_PASSWORD", "theiaminio")
    s3_runtime_bucket: str = os.environ.get("S3_RUNTIME_BUCKET", "theia-runtime")
    s3_apps_bucket: str = os.environ.get("S3_APPS_BUCKET", "theia-apps")
    s3_roles_bucket: str = os.environ.get("S3_ROLES_BUCKET", "theia-roles")

    # ---- colony-api (the BASE deployment authority) ----
    # gs-api fans /api/deployments out to BOTH Mender (app) and colony-api (base).
    colony_api: str = os.environ.get("COLONY_API", "http://colony-api:8081")
    colony_api_key: str = os.environ.get("COLONY_API_KEY", "")
    # The com aggregator that answers ListMachines — the whole cluster's
    # Observability presence + the base-state mirror's machine→device mapping.
    com_aggregator: str = os.environ.get("GS_COM_AGGREGATOR", "10.0.0.99:7700")

    # ---- service ----
    cors_origins: str = os.environ.get("GS_CORS_ORIGINS", "*")
    # API key gating the MUTATING routes (deploy / publish). When set, a caller
    # (colony's first-install task, a remote operator) must present X-GS-Key. When
    # empty, mutating routes are OPEN — fine for a localhost-only GS-host CLI, NOT
    # for a network-exposed GS. The read routes (devices, planes, status) are always
    # open (trusted network); only state-changing calls are gated.
    api_key: str = os.environ.get("GS_API_KEY", "")


def settings() -> Settings:
    return Settings()

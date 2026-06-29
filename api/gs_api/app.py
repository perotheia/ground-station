"""FastAPI app factory — the GS operator backend.

Mounts the routers (devices, deployments, planes) + a health probe. CORS is open
by default (dev); pin GS_CORS_ORIGINS in prod. The web UI is served separately
(gs-web / nginx) and proxies /api here.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .routers import campaign, deployments, devices, planes, ucm
from .settings import settings


def create_app() -> FastAPI:
    s = settings()
    app = FastAPI(
        title="Theia Ground Station",
        version=__version__,
        description="Fleet operator backend — Mender OTA control + the Theia "
                    "runtime/app vendoring planes.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in s.cors_origins.split(",")],
        allow_methods=["*"], allow_headers=["*"], allow_credentials=False,
    )

    @app.get("/api/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok", "service": "ground-station", "version": __version__}

    @app.get("/api/config", tags=["meta"])
    def config() -> dict:
        """Non-secret config the UI needs to render (server URL, bucket names)."""
        return {
            "mender_server": s.mender_server,
            "mender_flavor": s.mender_flavor,
            "runtime_bucket": s.s3_runtime_bucket,
            "swp_bucket": s.s3_swp_bucket,
            "token_set": bool(s.mender_token),
            "auth_required": bool(s.api_key),
        }

    app.include_router(devices.router)
    app.include_router(deployments.router)
    app.include_router(planes.router)
    app.include_router(ucm.router)
    app.include_router(campaign.router)
    return app


app = create_app()

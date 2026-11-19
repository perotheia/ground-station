"""FastAPI app factory — the GS operator backend.

Mounts the routers (devices, deployments, planes) + a health probe. CORS is open
by default (dev); pin GS_CORS_ORIGINS in prod. The web UI is served separately
(gs-web / nginx) and proxies /api here.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .routers import campaign, deployments, devices, planes, rigs, ucm
from .settings import settings


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # resume the followers of the provisioning runs this GS started (Mender auto-accept after a restart)
    import threading
    threading.Thread(target=rigs.resume_followers, daemon=True, name="resume-followers").start()
    yield


def create_app() -> FastAPI:
    s = settings()
    app = FastAPI(
        lifespan=_lifespan,
        title="Theia Ground Station",
        version=__version__,
        description="Fleet operator backend — Mender OTA control + the Theia "
                    "runtime/app vendoring planes.",
    )
    if not s.api_key:
        raise RuntimeError("GS_API_KEY is not set: the ground station controls fleet deployments and refuses "
                           "to start without an API key")

    @app.middleware("http")
    async def _gate(request, call_next):      # EVERY /api route except health needs the key (reads too)
        from fastapi.responses import JSONResponse
        from .auth import key_ok
        if request.url.path != "/api/health" and request.method != "OPTIONS" \
                and not key_ok(request.headers.get("x-gs-key")):
            return JSONResponse(status_code=401, content={"detail": "invalid or missing X-GS-Key"})
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in s.cors_origins.split(",") if o.strip()],
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
    app.include_router(rigs.router)          # Add device / Deploy runtime / runs / ONE fleet list
    from .routers import pki
    app.include_router(pki.router)
    return app


app = create_app()

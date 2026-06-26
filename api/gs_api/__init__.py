"""gs_api — the ground-station fleet operator backend.

A FastAPI service over the two things a Theia fleet operator drives:
  - the Mender GW Management API (devices, deployments, artifacts) — the OTA control
    plane, wrapped by the existing fleet.py Mender client.
  - our two distribution planes in MinIO (theia-runtime, theia-apps) — the vendoring
    catalog of what's PUBLISHABLE, vs Mender's record of what's DEPLOYED.

The web UI (Mender-like fleet view + vendoring + monitoring) talks only to this API.
"""

__version__ = "0.1.0"

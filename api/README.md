# ground-station — fleet operator surface

The operator side of a Theia fleet: a **web UI + API** over the Mender GW (OTA
control) and our two **distribution planes** in MinIO (the runtime + app vendoring
catalog). This is what an operator uses day-2 — see what's enrolled, what's
published, and roll an app version out to a fleet.

```
  browser ──▶ gs-web (nginx, the SPA)  ──/api─▶  gs-api (FastAPI)
                                                   ├─▶ Mender GW Management API
                                                   │     (devices, deployments, artifacts)
                                                   └─▶ MinIO planes
                                                         theia-runtime/<ver>-<distro>/
                                                         theia-apps/user-software/<fleet>/<app>/<ver>/
```

## The three views

- **Fleet** — every enrolled device (Mender inventory): hardware-class fleet,
  group, the running artifact, and Theia's own health / SM-state / UCM-version
  inventory once the rig reports it. Click a row for the full attribute set.
- **Deployments** — the OTA rollout history with live status + a per-status
  rollout bar (Mender deployment statistics).
- **Vendoring** — the Theia superpower over a vanilla Mender UI: the two
  distribution planes. The **app plane** lists published app versions per fleet
  with one-click **Upload to GW** / **Deploy to <fleet>** (pull the .mender from
  the plane → upload to Mender → create a deployment to the fleet group). The
  **runtime plane** lists published platform releases (ABI-qualified per distro) —
  what colony factory-installs.

## Run

The Mender GW must be up first (`mender/server/up.sh up`) — it owns the `mender`
docker network this stack joins.

```sh
cp .env.example .env          # fill MENDER_TOKEN (a Mender PAT)
docker compose up -d --build  # gs-minio + gs-api + gs-web
# UI on http://<host>:8088
```

`gs-api` holds the Mender token + MinIO creds; the browser never sees them (it
talks to `gs-web`, which proxies `/api` to `gs-api`). API docs at `/api`'s
OpenAPI: `http://<host>:8088/api/docs` (via the proxy) or `:8080/docs` direct.

## Extending (monitoring + goodies)

The backend is the home for the goodies. Add a router under `api/gs_api/routers/`,
include it in `app.py`, and add a view under `web/src/views/`. Natural next panels:
per-device Theia health/trace streams (com gRPC over the VPN), a fleet health
rollup, config/params drift, a campaign planner (phased rollout over the Mender
deployment API). The device inventory already carries `theia_health` /
`theia_sm_state` / `theia_ucm_version` slots for the rig to report into.

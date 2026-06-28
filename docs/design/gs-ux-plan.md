# Theia Ground Station — Operator UX Implementation Plan

> Companion to [gs-ux-design.md](gs-ux-design.md). Phased, each phase ends at a
> **▣ CHECKPOINT** the operator can verify before the next phase starts. The
> riskiest seam — **GS ↔ Mender** (API-registered devices reflecting in Mender
> UX) and **colony-as-Mender-shaped-API** — is front-loaded (P0–P2) so the
> hard integration is proven before any pixel work.
>
> Two design questions are already resolved by live probe (design §9): base-state
> mirror = Mender **tags** (PUT 200, verified on the rpi4); com Connect = static
> machine identity + `ListMachines`/`Connect…(host:port)`, not a per-device RPC.

---

## Guiding constraints

- **Scope locked:** no MFA, no user management, no big-fleet rollout thresholds.
- **gs-api is the only browser-facing service** — all creds (Mender PAT, MinIO,
  colony) stay server-side, as today.
- **Reuse what exists:** Devices.jsx, Deployments.jsx, Vendoring.jsx, Rollout.jsx
  already exist and already render Mender's status dicts. Extend, don't rewrite.
- **Don't break the proven OTA path** ([gateway reprovision](../../README.md)):
  every phase leaves `theia release-app → GS publish → Mender` working.

---

## Phase 0 — colony-api: a dockerized, Mender-shaped deployment service

**Goal:** colony becomes a service `gs-api` can drive like Mender. This is the
new piece; everything else builds on it.

1. New container `colony-api` (FastAPI, on the Mender docker network) wrapping the
   colony CLI. Mounts the workspace bundle (registry + `dist/manifest`) + an SSH
   key to reach rigs (mirror how `gs-api` mounts `fleet/`).
2. Endpoints, **Mender-Management-API-shaped** (design §6):
   - `GET /rigs` ← `deploy/registry/*.yml` (the deploy targets).
   - `POST /deployments {rig, kind: provision|orchestrate, schedule?}` → enqueue +
     run the Ansible play; return `{id, status}`.
   - `GET /deployments` and `GET /deployments/{id}` → run journal, status
     **vocabulary aligned to Mender** (`inprogress` / `pending` / `finished` +
     a `{success, failure, pending}` statistics dict from PLAY RECAP).
   - `GET /deployments/{id}/log` → tail the Ansible output.
3. A tiny scheduler (queue + timer) so `schedule: <ts>` fires the play later
   (matches Mender scheduled deployments).

**▣ CHECKPOINT P0** — `curl colony-api`:
- `GET /rigs` lists `central`, `compute`, …
- `POST /deployments {rig: central, kind: orchestrate}` runs the SAME play
  `colony orchestrate central` runs today, and the rpi4 ends up identically
  provisioned (verify: supervisor up, 14 services, S3-pulled runtime — the proven
  state). Journal shows `finished / success:1`.
- A scheduled deployment fires at its timestamp.
- **Risk gate:** if the play can't run cleanly from inside a container (SSH, bundle
  paths), resolve the run-model question (design §9.1) HERE before proceeding.

---

## Phase 1 — Connect Device: dual registration (the GS funnel)

**Goal:** one GS action onboards a board into BOTH Mender and Observability —
closing the "API-registered devices missing from Mender UX" gap.

1. `gs-api`: `POST /api/devices/connect {mac, fleet, group?}`:
   - **Mender-half:** find the PENDING device auth-set by MAC, ACCEPT it, set
     `device_type=fleet`. (Exactly the accept flow this session already scripts.)
   - **com-half:** ensure the board's com endpoint is in the aggregator's machine
     list (the `Connect…(host:port)` mechanism; static identity via `ListMachines`).
     If com identity is purely manifest-static, this may be a no-op beyond
     verifying the board appears in `ListMachines` — confirm + record.
   - return the unified device record (Mender id + com/Observability presence).
2. `gs-api`: `GET /api/devices` already merges Mender inventory; add a
   **`connected` tri-state**: `mender+com` (green), `com-only` (🔵 registered, not
   yet Mender-accepted → show Connect), `mender-only` (⚠ accepted but not seen by
   com — health gap).
3. `DELETE /api/devices/{id}` = decommission (Mender auth-set + com hub removal).

**▣ CHECKPOINT P1** — from a clean rpi4 (provides reset, pending in Mender):
- `POST /api/devices/connect {mac: dc:a6:32…, fleet: theia-gateway}` → device is
  accepted in Mender (shows in stock Mender UI) AND present in `ListMachines` /
  Observability.
- The Fleet list shows the correct tri-state before/after Connect.
- **This is the key GS→Mender proof:** a device GS connects is visible in Mender's
  own UX, not just via the API.

---

## Phase 2 — base-state mirror: Mender reflects colony deployments

**Goal:** after GS runs a base (colony) deployment, the board's base state shows
in Mender's device view — so Mender UX is never blind to what colony drove.

1. `gs-api`: after a successful colony-api orchestrate, `PUT` Mender device **tags**:
   `base_version`, `base_authority=colony`, `base_deployed_at`, `base_status`.
   (Mechanism verified: PUT `/inventory/devices/{id}/tags` → 200 on the live rpi4.)
2. `gs-api`: `GET /api/deployments` fans out to **colony-api + Mender**, merges,
   tags each row with an authority chip (`base` / `app`). One list, two sources.
3. Deployments.jsx renders the merged list (it already renders Mender rows +
   `RolloutBar`; colony rows use the same status dict → no new rendering).

**▣ CHECKPOINT P2:**
- Run a base deploy via colony-api → the rpi4's Mender device view shows
  `base_version: 0.2.1-bookworm-arm64`, `base_authority: colony` (in stock Mender
  UI's tags AND our Fleet panel).
- The Deployments panel shows BOTH the colony base deploy and a Mender app deploy
  in one list, each authority-tagged, statuses live.
- **End-to-end seam proven:** GS drives both; Mender reflects both.

---

## Phase 3 — Fleet panel (Targets, UF status model)

**Goal:** the device inventory as UF's Targets, extended.

1. Devices.jsx columns: `Device · Fleet · Group · Base(ver) · App(artifact) ·
   Health · SM · UCM · status-dot`. Status dots adopt UF colors
   (🟢 synced 🟡 pending 🔴 error 🔵 registered 🟣 overdue).
2. Connect button (top-right) → the P1 flow. Group assignment (named groups,
   Mender `/inventory/groups` — already 200).
3. Device side-panel = the **merged timeline**: colony base events (colony-api) +
   Mender app deployment states + Observability health, chronological.

**▣ CHECKPOINT P3:** the Fleet panel shows every lab board with correct dots,
base+app versions, group; clicking a board shows its full base+app+health
timeline; Connect works from the panel.

---

## Phase 4 — Deploy board (the UF 3-column heart)

**Goal:** the central deploy surface; **route-by-release-type** realizes
"colony for base, Mender for app".

1. New Deploy.jsx: 3 columns — **Targets | Releases | Action-history**. v1 uses
   **select-checkboxes + Deploy button** (drag-drop is later polish — design §9.4).
2. Releases column filterable by type (base | app | role). Dropping/deploying a
   *base* release → colony-api `POST /deployments`; an *app* release → Mender
   deployment. **Operator never picks the authority — the release type does.**
3. **Confirm Assignment** dialog (UF, trimmed): mode **Now | Scheduled**; show the
   **compatibility gate** (a `theia-gateway` app only deploys to `theia-gateway`
   devices — the device_type gate, surfaced as UF Target-Type compatibility).

**▣ CHECKPOINT P4:** from the Deploy board, select the rpi4 + `gateway-1.0` →
Confirm → it deploys via Mender (success, gateway runs). Select the rpi4 +
`runtime 0.2.1-bw` → Confirm → it deploys via colony-api (re-orchestrate). Both
from one board, routed correctly, both appear in Deployments.

---

## Phase 5 — Releases catalog (Distributions + Upload)

**Goal:** the deployable-unit catalog across all three planes, with UF locking.

1. Vendoring.jsx → **Releases**: runtime plane + app plane + roles plane, each as
   a versioned, typed list. Reuse the existing plane readers.
2. Show **lock state** (UF): a release that's been deployed is locked (immutable);
   re-iterate = new version. Surface "Deploy" + "Publish-then-Deploy" (the latter
   already in Vendoring.jsx `publish(...)`).
3. Publishing stays CLI (`theia release[-app]`); the UI is the catalog + deploy
   shortcut, not an uploader (scope: no in-browser artifact upload v1).

**▣ CHECKPOINT P5:** the Releases catalog lists the bookworm runtime, the
gateway-1.0 app, and any role artifacts, each with type + lock state + a working
Deploy shortcut.

---

## Phase 6 — Rollouts (phased-by-group, no thresholds)

**Goal:** UF Rollout, trimmed for a lab fleet.

1. Wire Rollout.jsx into the top nav. A rollout = name + release + **group** +
   action-type (Now/Scheduled) + split into N sequential sub-groups.
2. **Drop** UF's percent trigger/error thresholds + auto-halt (big-fleet). Keep:
   the rollout bar (already rendered from Mender stats) + a manual **Abort**.

**▣ CHECKPOINT P6:** a rollout to a 2-board group deploys sequentially, the
rollout bar shows progress, Abort halts it.

---

## Cross-cutting checkpoints (run at every phase)

- **The proven OTA path still works:** `theia release-app gateway → GS publish →
  Mender → gateway runs on the rpi4`. Never regress it.
- **No creds in the browser:** every new gs-api route keeps Mender/MinIO/colony
  access server-side.
- **Scope discipline:** if a phase tempts MFA / user-mgmt / query-DSL / thresholds
  — stop, it's explicitly out.

---

## Dependency order

```
P0 colony-api ──┬─► P2 base-mirror ──► P4 Deploy board ──► P6 Rollouts
                │                         ▲
P1 Connect ─────┴─► P3 Fleet ────────────┘
                                    P5 Releases ─► (feeds P4)
```

P0 + P1 are the integration spine (GS↔Mender↔colony) and gate everything. P3/P5
are mostly UI over already-working APIs. P4 is the payoff. P6 is optional polish.

---

## First concrete step (when implementation starts)

**P0, step 1:** scaffold `colony-api` as a FastAPI app next to `gs-api` in the
ground-station compose, with `GET /rigs` reading `deploy/registry/*.yml` and a
stub `POST /deployments` that shells `colony orchestrate <rig>` and journals it.
Prove CHECKPOINT P0 against the live rpi4 before any further build.

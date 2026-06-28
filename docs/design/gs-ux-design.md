# Theia Ground Station — Operator UX Design

> **Status:** design / backlog · **Scope:** day-2 fleet-operator surface for the
> Theia OTA stack (colony + Mender + S3 planes). Cross-checked against
> Kynetics **Update Factory** (UF) — whose view model maps almost 1:1 onto our
> colony/GS/Mender split. We reuse UF's layout and conceptual model.
>
> **Out of scope (explicit):** MFA / 2FA, user management, and large-fleet
> features (target-filter query language, rollout error-threshold auto-halt at
> 10 %/50 %/100 % scale). We are a small lab fleet (≤ tens of boards). Keep it
> simple; add those later if the fleet grows.

---

## 1. The core idea — one operator surface over **two deployment authorities**

The single most important design fact: a Theia board's software is delivered by
**two different mechanisms**, and the GS UI must present both coherently.

| Layer | What it delivers | Authority | Mechanism today |
|---|---|---|---|
| **Base** | runtime + services (supervisor, com, per, …) — the platform | **GS / colony** | `colony provision` + `colony orchestrate` (Ansible, S3-pull) |
| **App** | user software (gateway, odd-path, …) — overlays on the base | **Mender** | `theia release-app` → S3 app plane → Mender deployment (`theia-app` module) |

Update Factory has ONE authority (its cloud). We have two, by design (the
[deploy/fleet adapters are swappable, not the product](../../README.md)). The GS
UI **unifies them**: the operator sees one "Deployments" surface, one "Fleet",
one "Releases" catalog — even though base deployments route through colony and
app deployments route through Mender.

**The user's framing, verbatim, mapped:**
- *"Create new deployment in GS is colony provision+orchestrate; in Mender UX it's user software."* → the **New Deployment** action has a **type switch: Base (colony) | App (Mender)**.
- *"GS drives both deployment but Mender UX should reflect it also."* → every base deployment GS runs is **also recorded into Mender** as a deployment-like record, so the Mender UX (and our Deployments panel that reads Mender) is never blind to what colony did. See §5.
- *"Connect a device is GS responsibility because it directly maps into the Observability panel. But GS has to connect device also to Mender."* → **Connect Device** is a GS action that does TWO registrations atomically: (a) the com/Observability hub, (b) the Mender device (accept the pending auth set). See §4.
- *"Colony as a dockerized service with API exposed to GS, emulated after Mender API."* → colony grows a small REST service whose shape mirrors Mender's Management API (devices / deployments with active|scheduled|finished). GS talks to colony and Mender through the **same client shape**. See §6.

---

## 2. Conceptual model — Update Factory ↔ Theia

UF's glossary maps cleanly. We adopt UF's nouns where they fit and keep our
existing ones where they're load-bearing.

| Update Factory | Theia GS | Backing store | Notes |
|---|---|---|---|
| **Target** (Controller ID) | **Device** (Mender device; id = board MAC) | Mender deviceauth/inventory | one per board |
| **Target Type** | **Fleet** = `device_type` (`theia-gateway`, `theia-rig`) | Mender device_type | partitions devices; gates artifact compatibility |
| **Target Filter** (saved query) | **Group** (Mender group) | Mender groups | we keep it to *named groups*, NOT a query DSL (out-of-scope) |
| **Software Module** (typed artifact) | **Artifact** — a `.deb`, a `.mender`, a release tree | S3 plane object | typed: runtime / services / app / role |
| **Distribution** (locked bundle) | **Release** — a versioned, immutable deliverable | S3 plane + Mender artifact | runtime release (`theia-runtime/<ver>`), app artifact (`theia-app`), role (`.mender`). **Locked-on-assign** like UF. |
| **Deployment** (direct) | **Deployment** — base (colony) OR app (Mender) | Mender deployments / colony API | active \| scheduled \| finished |
| **Rollout** (groups+thresholds) | **Rollout** — a deployment over a Group, phased | Mender | we keep phased-by-group; we DROP UF's percent-threshold auto-halt (big-fleet) |
| **Action History** (per-device states) | **Device timeline** — Mender device-deployment states + our Observability events | Mender + com | the two streams merge per device |
| **Upload View** | **Publish** — `theia release` / `release-app` → S3 | S3 planes | already done by CLI; UI shows the catalog |

**Locking (UF "Distributions and Software Modules Locking") — we adopt it.** A
Release is immutable once first assigned. Re-iterating means a new version
(`gateway-1.0` → `gateway-1.1`), never mutating a deployed one. This is already
how our S3 app plane + Mender artifacts behave (Mender rejects re-upload of the
same name+depends with 409). The UI must *enforce the workflow*: create →
add artifacts → publish → (auto-lock on first deploy).

---

## 3. View map — what panels the GS UI has

UF's left-rail views, filtered to our scope (drop Usage, User-Management,
System-Config-as-MFA). We keep UF's panel **layout** (the 3-column drag-drop
board for Deployment) because it's well thought through.

```
┌─ Theia Ground Station ───────────────────────────────────────────────┐
│  Fleet   Deploy   Releases   Rollouts                    GW · authed  │   ← top nav (we have this)
├──────────────────────────────────────────────────────────────────────┤
```

### 3.1 **Fleet** (UF "Targets", our Devices.jsx — extend)
The device inventory. One row per board. Columns:
`Device · Fleet(device_type) · Group · Base(runtime ver) · App(artifact) · Health · SM-state · UCM · status-dot`

- **status dot** (adopt UF colors): 🟢 synchronized · 🟡 pending · 🔴 error · 🔵 registered (never deployed) · 🟣 overdue (missed poll).
- **Connect Device** button (top-right) — the GS-owned onboarding action (§4).
- Row → side panel: device detail = the **merged timeline** (base colony events + app Mender deployment states + Observability health), the board's current Base/App releases, and per-device actions (re-orchestrate base, redeploy app, decommission).
- Filter: by Fleet, by Group, by status-dot. (NO query DSL — chips only.)

### 3.2 **Deploy** (UF "Deployment View" — the 3-column board, NEW)
The heart. UF's drag-drop: **Releases → Devices/Group → Confirm**. Our version:

```
┌─ Targets ─────────┐  ┌─ Releases ──────────┐  ┌─ Action history ────────┐
│ [filter chips]    │  │ [type: base|app]    │  │  (selected target)      │
│ ☐ rig1-central    │  │ ▸ runtime 0.2.1-bw  │  │  active                 │
│ ☐ jason (compute) │  │ ▸ gateway-1.0  (app)│  │  gateway-1.0  ✓ success │
│ …                 │  │ ▸ central role …    │  │  0.2.1-bw     ✓ synced  │
└───────────────────┘  └─────────────────────┘  └─────────────────────────┘
        drag a Release onto a Target / Group  →  Confirm Assignment dialog
```

**Confirm Assignment dialog** (adopt UF's, trimmed):
- mode: **Now** (UF Forced) · **Scheduled** (date/time) · (we skip Soft/maintenance-window — no on-device user-consent client yet).
- The dialog **routes by release type**: a *base* release → colony `provision`/`orchestrate`; an *app* release → Mender deployment. The operator doesn't pick the authority — the **release type** picks it. This realizes the user's "create-deployment = colony for base, Mender for app".
- shows the **compatibility gate**: a `theia-gateway` app can only drop on `theia-gateway` devices (UF Target-Type compatibility = our device_type gate).

### 3.3 **Releases** (UF "Distributions" + "Upload", our Vendoring.jsx — rename/extend)
The catalog of deployable units, both planes:
- **Runtime plane** (`theia-runtime/<ver>`) — base releases (read-only catalog; published by `theia release`).
- **App plane** (`theia-apps/user-software/<fleet>/<app>/<ver>`) — app releases (published by `theia release-app`).
- **Roles** (`theia-roles/<fleet>/<ver>`) — full-board role `.mender`.
- Each release: type badge, version, artifacts list, **lock state**, "Deploy" shortcut. Publishing stays a CLI action (`theia release[-app]`); the UI surfaces the catalog + the **Publish-then-Deploy** one-click (already in Vendoring.jsx `publish(...)`).

### 3.4 **Rollouts** (UF "Rollout View", our Rollout.jsx — wire in)
Phased deployment to a **Group**. We keep: name, release, group, action-type
(Now/Scheduled), **split into N sub-groups in sequence**. We DROP: percent
trigger/error thresholds + auto-halt (big-fleet). For a lab fleet, "deploy to
group, watch the rollout bar, abort on red" is enough.

### 3.5 What we DROP from UF
Usage/billing, User-Management, MFA login, Target-Filter query DSL, DCU (we have
UCM + config-push already), Soft/maintenance-window client consent, CI Gradle
plugin (we have `theia release` in CI).

---

## 4. Connect Device — the GS onboarding action (dual registration)

> *"Connect a device is GS responsibility because it directly maps into the
> Observability panel. But GS has to connect device also to Mender."*

A board comes online and must be **registered in two places**. Today these are
separate manual steps (accept in Mender API + the board joins com). The GS
**Connect Device** action does both, atomically, and is the *only* sanctioned
onboarding path:

```
Connect Device(board) :=
  1. Observability/com   — register the board in the com aggregator hub so it
                           appears in the Observability panel (health, SM, UCM).
                           [GS-owned; maps to the user's "directly into Observability"]
  2. Mender              — find the board's PENDING device auth-set (by MAC),
                           ACCEPT it, and set its device_type (fleet). Now Mender
                           "sees" the board and it's deployable.
  3. (optional) Group    — assign to a named Group.
```

**Why this matters (the user's gap):** *"API registered devices in Mender
missing from Mender UX."* When GS accepts a device via the Mender **Management
API**, it DOES appear in Mender's own inventory — but a device that GS knows
about (via com/Observability) but has NOT yet been Mender-accepted is invisible
to Mender. The Connect action closes that: **GS is the funnel** — nothing is
"connected" until it's in BOTH. The Fleet panel shows a 🔵 *registered* dot for
boards seen by com but not yet Mender-accepted, with a **Connect** affordance.

**Decommission** is the inverse: remove from com hub + decommission the Mender
device auth-set.

---

## 5. Making Mender reflect colony (base) deployments

> *"GS drives both deployment but Mender UX should reflect it also."*

Mender natively records only its OWN deployments (app artifacts). When GS runs a
**base** deployment through colony, Mender is blind to it — yet the operator (and
any Mender-native view) should see "this board got runtime 0.2.1-bookworm-arm64
at 14:03, success". Two options, we pick **B**:

- **A. Mirror as a Mender deployment** — after a colony orchestrate, GS POSTs a
  synthetic Mender deployment record (artifact_name = `runtime-0.2.1-bw`,
  device = the board, status = finished/success). Pro: shows up in stock Mender
  UI verbatim. Con: pollutes Mender artifacts (needs a dummy artifact uploaded);
  Mender deployment records are immutable (can't delete in OSS — we saw 405s).
- **B. (chosen) Record into Mender device *inventory attributes*** — GS writes the
  board's base state to Mender inventory: `base_version`, `base_deployed_at`,
  `base_status`, `base_authority=colony`. These show in Mender's device view
  (inventory tab) AND in our Fleet/Deploy panels (which read the same inventory).
  No synthetic artifacts, no immutable-record pollution. The colony deployment
  *history* lives in colony's own API (§6); the *current state* mirrors into
  Mender inventory so Mender's UX is never blind.

So: **colony API = base deployment history** (active/scheduled/finished), **Mender
inventory = current base state per device**, **GS UI = the union**. The
Deployments panel queries BOTH colony and Mender and merges into one list, tagged
by authority (a small `base`/`app` chip).

---

## 6. Colony as a dockerized service emulating the Mender API

> *"Colony as a dockerized service with API exposed to GS … you emulate it after
> Mender API."*

Today colony is a CLI (`colony provision|orchestrate|cleanup`) invoked from a
controller shell. To let GS drive base deployments the same way it drives Mender,
colony grows a thin **REST service** (`colony-api`, a new container on the Mender
docker network alongside `gs-api`). Its surface **mirrors the Mender Management
API shape** so `gs-api` can use the *same client pattern* for both:

| Mender Mgmt API | colony-api (emulated) | Backing action |
|---|---|---|
| `GET /devices` | `GET /rigs` | the deploy registry (`deploy/registry/*.yml`) |
| `GET /deployments` (active\|scheduled\|finished) | `GET /deployments` | colony's own run journal |
| `POST /deployments` | `POST /deployments` `{rig, kind: provision\|orchestrate, schedule?}` | enqueue + run the Ansible play |
| `GET /deployments/{id}` | `GET /deployments/{id}` | play status + per-task log tail |
| `GET /deployments/{id}/statistics` | same shape | success/failure rollup (Ansible PLAY RECAP → the same status dict Mender uses) |

**Status vocabulary aligned with Mender** so the UI's `RolloutBar` / `StatusBadge`
(which already render Mender's `{success, failure, pending, …}` dict) work
unchanged for colony deployments: a running play = `inprogress`, a queued
schedule = `pending`/`scheduled`, PLAY RECAP `failed=0` = `success`.

**Scheduling** (UF "Scheduled" start-type, Mender phased deployments): colony-api
holds a small scheduler (a queue + a timer) so `POST /deployments {schedule: <ts>}`
fires the play at the time — matching Mender's scheduled-deployment semantics.

`gs-api` then exposes a unified `/api/deployments` that fans out to **both**
`colony-api` and Mender, merges, and tags each with its authority. The browser
sees one list.

---

## 7. Architecture (the whole surface)

```
                    browser (GS SPA — Fleet · Deploy · Releases · Rollouts)
                                   │  /api/*
                          ┌────────▼─────────┐
                          │      gs-api      │  unified facade; holds creds
                          │  (FastAPI)       │  the browser never sees tokens
                          └──┬─────┬─────┬───┘
                 base deploys│     │app  │observability + connect
                   + history │     │deploy
              ┌──────────────▼┐ ┌──▼──────────┐ ┌▼──────────────┐
              │  colony-api   │ │  Mender Mgmt│ │  com (aggregator│
              │ (NEW docker)  │ │  API + S3   │ │  hub) / per     │
              │ Ansible runs  │ │  planes     │ │  Observability  │
              └───────────────┘ └─────────────┘ └────────────────┘
                base authority    app authority    health/identity
```

- `gs-api` is the ONLY thing the browser talks to (keeps the Mender PAT, MinIO
  creds, and colony access server-side — same as today).
- **Connect Device** (§4) calls com (Observability register) + Mender (accept).
- **Deploy** routes by release type: base → colony-api, app → Mender.
- **Fleet/Deploy/Deployments** read the *union* of Mender + colony-api, plus the
  base-state mirror in Mender inventory (§5).

---

## 8. Panel parity checklist (UF → GS) — the cross-check (task #1)

| UF panel / concept | GS panel | Have it? | Action |
|---|---|---|---|
| Deployment View (3-col drag-drop) | **Deploy** | ✗ | build (§3.2) |
| Targets list + status colors | **Fleet** (Devices.jsx) | ~ | extend: status dots, Connect, group |
| Distributions (locked bundles) | **Releases** (Vendoring.jsx) | ~ | rename, add lock state + roles plane |
| Upload View | Publish (CLI + catalog) | ✓ | surface only; keep CLI publish |
| Rollout View (groups) | **Rollouts** (Rollout.jsx) | ~ | wire into nav; drop thresholds |
| Action History (per-device states) | device timeline | ~ | merge colony + Mender + Observability |
| Target Filter (query) | Group chips | partial | named groups only (no DSL) — scope cut |
| Confirm Assignment (Forced/Soft/Time) | Confirm dialog (Now/Scheduled) | ✗ | build, trimmed |
| Usage / User-Mgmt / MFA | — | — | **dropped (scope)** |
| DCU | UCM + config-push | ✓ | already ours |

---

## 9. Open design questions

1. **colony-api run model** — does the Ansible play run *inside* the colony-api
   container (needs SSH keys + the workspace bundle mounted), or does colony-api
   shell out to a colony runner on the host? (Leaning: container with the bundle
   volume + SSH agent, mirroring how `gs-api` mounts `fleet/`.) *Resolve in P0.*

2. **base-state mirror write path** — ✅ **RESOLVED (probed 2026-06-28).** Mender
   OSS `PUT /api/management/v1/inventory/devices/{id}/tags` accepts **operator-set
   attributes** (returned 200; `base_version` + `base_authority` landed on the
   live rpi4 and read back in scope `tags`). So §5's base-state mirror is
   **Mender tags**, not device-reported inventory attrs. GS writes
   `base_version` / `base_authority=colony` / `base_deployed_at` as tags after a
   colony run; they show in the stock Mender device view AND our panels. No
   synthetic artifacts needed. (`PUT [] ` clears them.)

3. **Connect → com** — ✅ **RESOLVED.** com identity is **static** (machine
   registry from `machine.json`), not a dynamic per-device enroll. The
   aggregator hub uses `ListMachines` (enumerate the cluster) + the supervisor-gui
   **`Connect…(host:port)`** hub-switch (theia d5893bc, mTLS b77ff1c). So the
   com-half of GS **Connect Device** = ensure the board's com endpoint is in the
   aggregator's machine list (the `Connect…(host:port)` mechanism) — NOT a
   register-device RPC. The board then self-reports via `ListMachines` and shows
   in Observability. Mender-half stays: accept the pending auth-set by MAC.

4. **Drag-drop vs select-and-act** — UF is drag-drop; for a lab fleet a
   select-checkboxes-then-Deploy-button may be faster to build and just as clear.
   Decide in P4 (the conceptual model is identical either way). *Lean: select +
   Deploy button for v1; drag-drop is polish.*

---

*Cross-checked against Update Factory docs (deployment-view, rollout-view,
distributions-view, glossary) 2026-06-28. Layout + conceptual model reused;
MFA/user-mgmt/big-fleet deliberately excluded.*

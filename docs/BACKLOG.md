# Ground Station — Backlog

Day-2 fleet-operator surface for the Theia OTA stack. Items are roughly ordered;
each links its design.

## Operator UX (Update-Factory-aligned)

- [ ] **GS Operator UX** — unify the two deployment authorities (colony=base,
  Mender=app) under one surface; Connect-Device dual-registration (Observability
  + Mender); colony-as-dockerized-API emulating Mender; reuse Update Factory's
  layout + conceptual model. **Design:** [design/gs-ux-design.md](design/gs-ux-design.md).
  Scope cuts: no MFA / user-management / big-fleet rollout thresholds.
  **Plan (phased, with checkpoints):** [design/gs-ux-plan.md](design/gs-ux-plan.md).
  - [x] P0 — colony-api ✓ CHECKPOINT passed (dockerized, live-verified rpi4) (dockerized, Mender-shaped: `/rigs`, `/deployments`)
  - [x] Runtime 0.2.2-bookworm-arm64 (com HAS ListMachines) → rpi4 reprovisioned from S3, ListMachines live; com-half degrade REMOVED (no fallback). JETSON still on old runtime — rebuild focal-arm64 + reprovision when it rejoins the cluster.
  - [x] P1 — Connect Device ✓ CHECKPOINT passed (live accept-by-MAC; Mender-visible; com-half degrades on old runtime)
  - [x] P2 — base-state mirror + merged deployments ✓ CHECKPOINT passed (rpi4 Mender tags base_version=0.2.2; 1 base + 16 app in one list)
  - [x] P3 — Fleet panel: UF status dots (synced/registered/observed), Health·SM·UCM pills, inline group assign, Connect, per-device MERGED TIMELINE side-panel (colony base + Mender app + state)
  - [x] P4 — Deploy board (modern UF 3-col dashboard, route-by-release-type, runtime-compat gate) ✓ LIVE dalek:8090
  - [x] Stateless GS (base live from supervisor release_version, app from Mender) + ACT columns (pin/cleanup/delete, unpin-before-delete) + reverted Assigned→Deploy-bar (enabled only when compatible) + nginx X-GS-Key inject ✓ LIVE. FIXED: GetSystemInfo empty machine_name/release_version was STALE codegen (theia 498a83c/b231397) — GS now reads base_source=live; mirror is just fallback.
  - [x] P4b — arity-1 Assign (Assigned tab, in-context, compat-gated) + zero-arity Cleanup (🧹 row action, inline confirm = colony cleanup) ✓ LIVE
  - [x] P5 — Releases catalog (runtime↔app DEPENDENCY graph, no-backward-compat gate) ✓
  - [x] P5b — Releases: lock state (deployed = 🔒 immutable, delete-guarded) + role plane (theia-roles/<fleet>/<ver>/<role>.mender)
  - [x] P6 — Rollouts (phased-by-group, N sequential sub-groups, Now/Scheduled, operator-gated Advance + Abort; no percent thresholds) — stateless (plan lives in UI)

See [design/gs-ux-design.md §8](design/gs-ux-design.md) for the full UF→GS
panel-parity cross-check and §9 for open design questions.

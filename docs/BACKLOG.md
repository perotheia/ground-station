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
  - [ ] P3 — Fleet panel: status dots, group, Connect, merged timeline
  - [x] P4 — Deploy board (modern UF 3-col dashboard, route-by-release-type, runtime-compat gate) ✓ LIVE dalek:8090
  - [x] P4b — arity-1 Assign (Assigned tab, in-context, compat-gated) + zero-arity Cleanup (🧹 row action, inline confirm = colony cleanup) ✓ LIVE
  - [x] P5 — Releases catalog (runtime↔app DEPENDENCY graph, no-backward-compat gate) ✓
  - [ ] P5b — Releases: lock state + role plane
  - [ ] P6 — Rollouts (phased-by-group; no thresholds)

See [design/gs-ux-design.md §8](design/gs-ux-design.md) for the full UF→GS
panel-parity cross-check and §9 for open design questions.

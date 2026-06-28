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
  - [ ] P0 — colony-api (dockerized, Mender-shaped: `/rigs`, `/deployments`)
  - [ ] P1 — Connect Device (com + Mender accept, by MAC)
  - [ ] P2 — base-state mirror into Mender inventory (Mender UX reflects colony)
  - [ ] P3 — Fleet panel: status dots, group, Connect, merged timeline
  - [ ] P4 — Deploy board (3-col, route-by-release-type, Confirm dialog)
  - [ ] P5 — Releases catalog (runtime/app/role planes, lock state)
  - [ ] P6 — Rollouts (phased-by-group; no thresholds)

See [design/gs-ux-design.md §8](design/gs-ux-design.md) for the full UF→GS
panel-parity cross-check and §9 for open design questions.

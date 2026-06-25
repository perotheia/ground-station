# ground-station — Theia fleet Operations & Monitoring

The **cloud ground station** for a Theia fleet: it augments a Mender OTA backend
with the operator-facing surface for **day-2 fleet operations** — enroll devices,
plan + push software campaigns, watch rollouts, and aggregate device health.

It is a **separate repo from `theia` by design**: deployment and fleet tooling are
*swappable adapters*, not part of the on-device product. ground-station speaks the
**Mender Management API** + a standalone `docker compose`; it never compiles or
checks out the Theia source. The only coupling to `theia` is the **`.mender`
artifact contract** — the tarball shape the on-device `theia-release` update module
consumes (a data contract, not a code dependency).

```
                         Cloud / operator
                              |
                       ground-station          <- THIS repo
                    +---------+----------+
                  Mender GW  fleet ops  enroll
                    |          |          |
                    +---- Mender Mgmt API + device pull ----+
                                                            v
   colony (provision/orchestrate)  --dist/<rig> bundle-->  fleet rigs (theia)
                                                            on-device: VUCM <- UCM <- EXEC
```

The separation of concerns (the governing principle): **users / devices / packages
/ deployments** stay distinct — Mender already models this (useradm / deviceauth +
inventory / deployments + S3 / deployment CRUD), and ground-station's surface
preserves it. Device fleet *deployment* (provision/orchestrate) lives in the
sibling **`colony`** repo; this repo is *operations & monitoring*.

## Layout

| Dir | What | Origin |
| --- | --- | --- |
| `mender/server/` | Mender OSS server bring-up (`up.sh` clones + composes Mender at a pinned tag; traefik + S3 route fixes) | theia `deploy/mender/server/` |
| `mender/artifact/` | `build-artifact.sh` — pack a Theia release-dir into a `.mender` artifact | theia `deploy/mender/build-artifact.sh` |
| `fleet/` | `fleet.py` — Mender Management API client (upload / deploy / status / devices / release); `campaign.sh` — direct-UCM-over-probe (self-hosted demo) | theia `deploy/vucm/` |
| `enroll/` | `rig_enroll.py` + `tailscale_client.py` — day-0 device enrollment over com gRPC (identity + PKI + VPN auth-key) | theia `tools/rig-enroll/` |
| `docker-compose.yml` | the ground station: Mender GW + (future) operator UX | NEW |

## Bring-up

    # 1. stand up the Mender GW (clones Mender OSS, composes it; ~2-3GB, opt-in)
    mender/server/up.sh up

    # 2. enroll a rig (day-0: identity + accept in deviceauth)
    mender/server/enroll-rig.sh <rig-host>          # mender-client side
    #   or, over com gRPC (richer: PKI + VPN key):
    enroll/rig_enroll.py <rig> ...

    # 3. build + push a campaign
    fleet/fleet.py release <version> <release-dir> <device-group>
    #   build-artifact.sh packs the .mender, fleet.py uploads + deploys via the API

The device's on-device side (the `theia-release` Mender update module + the
Mender->UCM handoff state-scripts) lives in the **`theia` repo** and ships in the
runtime `.deb` — ground-station drives it remotely, it does not contain it.

## What's next (the operator UX)

`fleet.py` is the CLI today. The fleet **operator UX** (campaign planning,
device-group targeting, rollout monitoring, health/inventory — VUCM aggregates
UCM-version + PHM-health + SM-state into Mender inventory) is the surface this repo
grows. v1 may lean on Mender's own GUI + `fleet.py`; a thin fleet-ops layer on top
is the likely shape. See theia `docs/tasks/BACKLOG/repo-separation.md`.

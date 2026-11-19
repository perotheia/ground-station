# Provisioning review (2026-11-19): what happens today, what is foreign, the simplest flow for Theia

Evidence: the code of ground-station, colony and theia on 2026-11-19, and today's real enrollment of the
BeagleBone AI-64 (`ai64-proto`, Debian 13) and the SK-TDA4VM (`sk-tda4vm`, Arago) through the GS on panzer.
This is a proposal: nothing below is rebuilt without the user's OK.

## 1. What actually happens today (the AI-64, enrolled through the GS)

```
operator / GS (panzer)                                   rig
 0  hand-place the GS ssh pubkey on the rig user ─────────▶ ~debian/.ssh/authorized_keys   (manual, out of band)
 1  POST /api/deployments/base kind=provision
      gs-api mints a Tailscale key (tag:fleet-rig) ─┐
      colony-api ─ ansible provision.yml (ssh) ─────┴─────▶ rig_class, apt deps, etcd, Mender repo + client 5.2.0,
                                                           mender.conf + identity script (device_id=<rig>),
                                                           verify key (pushed), own key + CSR -> GS signer -> cert,
                                                           tailscale up (or already joined), TIPC module
 2  rig's mender-authd checks in over the tailnet (rig -> GS :443)
 3  POST /api/devices/connect identity=<rig>   (accept the pending auth set; then "add to VPN" records remote_ip)
 4  POST /api/deployments/base kind=orchestrate ─ ansible ─▶ runtime + services .debs from S3 (TLS, controller's
                                                           sha256), theia-supervisor.service, com on mTLS
 5  app/base SWP: POST /api/deployments (Mender) ────────▶ mender-update + theia-swp module: stage, flip
                                                           `current`, health gate, commit or ArtifactRollback
```
Steps 1, 2, 4, 5 are GS-driven and verified today; 0 is manual; 3 needed two extra calls (the board's first
check-in was MAC-based and left a stale pending entry; `remote_ip` was not filled by Connect).

## 2. The other mechanisms that ALSO exist

| mechanism | origin | what it does | status today |
|---|---|---|---|
| `enroll/rig_enroll.py` + com `Provision` RPC | **mosaic** (`moz orchestrate cloud_provision`, its README says so) | pushes uuid, VIN, a client private key + cert + CA chain, and a VPN key INTO a rig over com gRPC | **foreign + unsafe**: the private key crosses the wire; needs com already running and reachable, i.e. after the VPN it is supposed to configure (chicken and egg); now restricted to CN fleet-gs, but nothing in the GS calls it |
| `/api/devices/preauthorize` | Mender's preauth model | registers identity + the device's PUBLIC key before first check-in | needs the device key before the client ever ran (only a factory has it); in practice Connect (accept pending) is the path used |
| `/api/devices/probe` + `/set-identity` | GS + colony-api | ssh to a host, read its MAC, rewrite its Mender identity script | duplicates what provision now does (identity = rig name); `/set-identity` was a root RCE until today |
| `/api/devices/{id}/vpn`, `/{id}/ip` | GS | record an IP by hand | duplicated: provision reports the tailnet IP (`COLONY_VPN_IP`), but Connect / the device record do not use it |
| `mender/server/enroll-rig.sh` | GS (hand-run) | writes mender.conf, CA, identity, installs the client via get.mender.io | replaced by colony `tasks/mender-enroll.yml` today; still documented |
| nm FC VPN join (`vpn_authkey` from etcd -> `tailscale up` via popen) | theia, Headscale era | a THIRD way to join the VPN, from runtime config | dead in the fleet flow; a key in etcd + a shell line (hardening M5) |
| `/etc/theia/manifest/<machine>/certs/` | com Provision + `theia manifest` dev certs + SWP payload | THREE sources for com's certificates | replaced by `/etc/theia/pki` (key generated on the rig); the old paths are still read as a fallback |
| VUCM -> UCM campaign (`/api/campaign/*`, `/api/ucm/*`) | AUTOSAR UCM model (native to Theia's ARA design) | GS -> com -> VUCM admission (SM parked, NM link, PHM) -> UCMs -> `mender-update install` standalone, cross-board barrier in etcd, two-phase commit | a SECOND OTA control path next to Mender deployments; not exercised today |
| state script `ArtifactInstall_Leave_01_ucm-request` (Mender -> UCM hand-off) | theia | meant to let UCM adopt a release Mender installed | **dead**: installed in `/etc/mender/scripts`, but Mender runs Artifact* state scripts only from INSIDE the artifact; today's campaign log has 19 module lines and 0 from the script |
| `theia provision` / `theia orchestrate` CLI | theia | wrap the GS / colony | fine as GS clients; the skill doc still describes `deploy/ansible` + `theia dist` (pre-split) |
| `deploy/colony-patches/` in theia | theia | copies of colony / GS files | dead, drifting copies (remove) |
| colony registry rigs vs registry-free host+role | colony | two ways to name a target | the GS uses registry-free only |

## 3. One device, many identities (the AI-64 today)
Mender device id `94b200f0-...` (server-side) - Mender identity `device_id=ai64-proto` - a stale second identity
`mac=24:76:25:a1:e7:ed` - colony rig `ai64-proto` - Theia machine `master` (a ROLE: manifest dir
`/etc/theia/manifest/master`, the VPN-IP report says "master") - a second manifest dir
`/etc/theia/manifest/ai64-proto` from an earlier bench run - tailnet host `ai64-proto` - certificate CN
`ai64-proto` - preauthorize's "controller id" (a UUID, unused here) - com Provision's uuid + VIN (unused).
Role and identity are mixed: `machine` means "master" in the playbooks and in Theia's manifests but a device
name everywhere else.

## 4. The simplest flow that fits Theia (proposal)
One identity: **the device name** (`ai64-proto`), chosen by the operator once. It is the Mender identity key
(`device_id`), the certificate CN, the tailnet host name, the colony rig, the GS row. The ROLE
(master / zonal) is an attribute of the device in the GS (a Mender tag), never an identity.

Two operator steps, both in the GS UI:
1. **Add device** (name, address on the bench or tailnet, ssh user, role). The GS shows its ssh pubkey to place
   once (or the image carries it). This runs today's `provision`: VPN key, Mender client + config, own key +
   fleet certificate, verify key. The device then checks in and the GS ACCEPTS IT AUTOMATICALLY when the
   identity matches a device the GS just provisioned (no separate Connect, no preauthorize, no MAC). `remote_ip`
   comes from the provision report.
2. **Deploy** (runtime = colony orchestrate; app/base SWP = Mender deployment), with rollback by the health
   gate (works today).
Removed from the operator's world: preauthorize, probe, set-identity, add-to-VPN, set-IP, controller ids, MAC
identities, `enroll-rig.sh`, com `Provision`.

OTA control: keep ONE path per fleet shape. Single-board rigs: Mender deployments + the module's health gate
(verified). Multi-board vehicles: VUCM's campaign is the only one that can do the cross-board barrier; it should
DRIVE Mender (one deployment per board, VUCM gating commit) rather than run `mender-update` itself, and the
Mender -> UCM hand-off must ship inside the artifact (or go). DECIDE (the user): is a multi-board vehicle in scope
now? If not, park VUCM/UCM campaigns behind a flag and keep the single path.

## 5. Migration list (small steps, each testable on the two boards)
1. GS: auto-accept on check-in for devices provisioned by the GS (identity == a provision record); Connect
   becomes "verify". Fill `remote_ip` from the provision result (code exists: `vpn_ip`).
2. colony + Theia: separate role from name: `machine` = the device name everywhere on the rig (manifest dir,
   VPN report, unit), `role` = master/zonal only where the executor needs it. Clean the stale
   `/etc/theia/manifest/<role>` dirs.
3. Remove: com `Provision` + `rig_enroll.py` (keep `GetProvisionStatus` as a read), `/preauthorize`, `/probe`,
   `/set-identity`, `/{id}/vpn`, `/{id}/ip`, `enroll-rig.sh`, the nm VPN join, theia `deploy/colony-patches/`.
   Delete the stale MAC auth set on the AI-64.
4. Certificates: com reads ONLY `/etc/theia/pki` on a deployed rig; `release-swp` refuses key material
   (spec-com-mtls part H).
5. OTA: decide on VUCM (section 4); either ship the UCM hand-off in the artifact or remove the dead state
   script from colony.
6. Docs: the skill references (provision-orchestrate.md, fleet-deploy-cli.md) and two-plane-ota-ops.md to the
   flow above.

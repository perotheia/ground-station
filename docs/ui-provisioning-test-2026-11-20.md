# GS web UI provisioning path: build + browser test against the real boards (2026-11-19 night)

Operator path tested in headless Chromium (Playwright) against the GS on panzer (`http://100.125.123.35:8088`,
through a tunnel from taycann), with the real BeagleBone AI-64 `ai64-proto` (Debian 13). The SK-TDA4VM was
powered off for the night: its steps are a ready-to-run script (section 5) and part of the walkthrough (section 6).

State after the night: ground-station main (this doc), colony main `e57ac2b`, theia main `8c492c98` (runtime 0.3.1 on the AI-64),
perotheia/mender branch `arago-5.2`, chariot_ws `2ca0e30`. Spec: subject s-1aab5b21 `design/ui-provisioning-spec.md`.

## 1. What the operator has now (no curl, no ssh)
- **Fleet = ONE list**: Mender devices and colony-only rigs (the SK) in one row each, with source, rig class,
  address + VPN address, base runtime, app, Mender auth, **com badge** (mTLS green / plaintext red / down /
  unknown), last run.
- **Add device** (name, address, ssh user, role): a preflight ssh first (8 s), then provision runs in the
  background. **Run drawer**: live task list (ok / changed / skipped / failed), the failing task and its message
  in plain words, the result (VPN IP, certificate serial + expiry, Mender accept, rig class, named skips). The
  drawer comes back after a page reload; a second click shows "already running: open".
- **Auto-accept bound to the board's key**: provision reads the Mender device PUBLIC key over the same ssh
  channel; the GS accepts only the pending auth set whose identity is the device name AND whose key is that key.
  An identity alone (what any device can claim) is never accepted; a second set with another key never replaces
  an accepted one.
- **Deploy runtime** per row (version from the runtime plane, resolved to its plane key).
- Hidden from the device UI (backend kept): Probe, Set-identity, Add-to-VPN, Set-IP, Preauthorize.

## 2. Test matrix (screenshots: taycann `/bigdata/work/architect/ui-test/shots-live/`)
Script: `/bigdata/work/architect/ui-test/live.py` (phases list t1..t8), log `live-log.json`, board guard
snapshots `ai64-guards.sh` -> `ai64-before.txt`, `ai64-after-*.txt`.

| test | result | screenshots |
|---|---|---|
| Fleet one list (AI-64 Mender+colony, SK colony-only) | PASS | 01, 40 |
| T1 AI-64 re-provision via Add device | PASS (4th run; runs 1-3 found D2, D3, D4) | 24, 26, 29 |
| T1 guards: no reboot (boot_id `3e1aead5`), kernel holds, extlinux hash `0967bfd0`, r5f running, `/dev/ttyRPMSG0` same node, etcd + supervisor not restarted | PASS | `ai64-after-t1c.txt`, `etcd-*.txt` |
| T2 SK via Add device | NOT RUN (SK off) -> section 5 | - |
| T3 wrong address -> "cannot be reached" in 8.5 s | PASS | 02 |
| T4 wrong ssh user -> "refused the GS key" + the key to paste | PASS (after D1) | 04 (03 = the defect) |
| T5 second click during a run -> "already running on ai64-proto: open", one run | PASS | 25 |
| T6 page reload during a run -> the drawer re-opens | PASS | 27, 28 |
| T7 Deploy runtime 0.3.0 on the AI-64 -> success, com mTLS | PASS (after D5) | 36-40 (30-33 = the defect) |
| T8 OTA history: device history + Rollouts name "rolled back to base-0.1.0-trixie-arm64" | PASS (after D6) | 41, 42 |
| T7b Deploy runtime 0.3.1 (theia 8c492c98: roles + no-keys), then 0.3.0 (downgrade), then 0.3.1 | PASS (after D7): supervisor restarted onto each version (19:36, 19:39 UTC), no deleted binaries running, com mTLS, r5f running, ttyRPMSG0 present, ~1.9 GB free | `t7-0.3.*-c.out` |
| Roles live on 0.3.1: operator cert ListMachines | OK | - |
| Roles live: operator cert UcmView.RequestUpdate | `PERMISSION_DENIED: needs role gs; caller 'roles-check' (role operator from uri)` | - |

Guard notes: four files under `/usr/lib/firmware/zephyr` + `/boot/firmware/overlays/k3-j721e-ai64-r5fss1-bench.dtbo`
changed between 16:00 and 18:56 UTC, between our runs and not by colony (it has no firmware task): the R5F bench
work. `/dev/ttyRPMSG0` mtime moved at 19:12:56 UTC = `/opt/pascal-tools/mavcli` from 10.0.0.78 (journal), not Theia.

## 3. Defects found tonight and fixed (each with its regression test)
| id | defect (live) | fix | regression test |
|---|---|---|---|
| D1 | the AI-64 login banner filled colony's 200-char ssh error, "Permission denied" was cut, the GS said "unreachable" | colony `587f95f` (ssh LogLevel=ERROR, keep the END of the error); GS `181546c` (only network failures are "unreachable", else "ssh-failed" with ssh's words) | `api/tests/test_provisioning.py::test_preflight_errors` (banner cases) |
| D2 | Add device sent no runtime version, colony had no manifest for the OTA modules, provision failed | GS `471af43`: the rig's last runtime, else the newest plane release | `test_add_device_without_version_takes_the_rigs_last_runtime` |
| D3 | colony reports the Mender key as base64, the GS read it as PEM, did not accept; the drawer said "did not check in" | GS `327c36a` (+ the drawer shows the GS's reason) | `test_mender_pubkey_field_as_colony_sends_it` |
| D4 | every re-provision restarted etcd under the running stack | colony `1586cfa`: restart only on a config change | `tests/test_no_unconditional_restarts.py` (lints all plays) |
| D5 | Deploy runtime sent "0.3.0", colony downloads by plane key "0.3.0-trixie-arm64": download failed | GS `84669ce`: resolve to the key, refuse unknown versions up front | `test_runtime_version_resolves_to_the_plane_key`, `test_deploy_sends_the_plane_key` |
| D6 | the device's Action History showed a bare NOK for the rolled-back v2 | GS `84669ce`: "rolled back to <release>" per device | live T8 (`data-testid=rolled-back`) |
| D7 | Deploy runtime 0.3.1 said success, the debs were installed, but every Theia process kept running the DELETED 0.3.0 binaries (no restart: two tasks registered `_dpkg`, the skipped opkg one overwrote it); a downgrade would have failed (apt without --allow-downgrades) | colony `e57ac2b`: one `_runtime_changed` from the installed versions before/after; same version = no restart; downgrades allowed | `tests/test_play_registers.py` + live T7b |
Found in review / by the coders, before going live:
| R1 | auto-accept by identity only: anyone reaching the gateway could claim a name or take over an accepted device | GS `f068cbb`, colony `0c97f32` (key read over ssh) | `test_auto_accept_requires_key_match`, `test_no_second_set_takeover`, `test_accept_with_matching_key` |
| R2 | the 64 KB log cap could cut the key line | colony `4db4cb7` (`mender_pubkey` record field) | `tests/test_runner_progress.py::test_progress_advances_while_running` |
| R3 | every re-provision rewrote mender.conf (a line edit replaced the one-line JSON) and restarted the client | colony `5abad62` | `test/opkg-mender/run.sh` ("second run changes nothing") |
| R4 | pki-enroll created /etc/theia 0750 root: com could not read its cert on a fresh rig | colony `af36774` | e2e `pki_rig_identity`, `mtls_operator_list_machines` |

## 4. The "Still open" list of the last release: none stays open
| item | done | regression test |
|---|---|---|
| (1) provision + orchestrate from the UI | GS + colony above; colony streams play progress (Popen) and refuses a second run per rig (409) | `web/tests/provisioning_spec.py` (mock, 28 checks), `api/tests` (70), colony `tests/test_runner_progress.py` |
| (2a) the SK visible: one device list | GS `/api/fleet` | `test_fleet_one_list` |
| (2b) the SK a real Mender device, from OUR fork | perotheia/mender `arago-5.2` = upstream 5.2.0 + build files (`b7d58c6b`, `349afaed`), built against Debian bookworm + checked against TI's PSDK 11.02.02.05 rootfs (glibc <= 2.34, NEEDED only libc/libssl3/libdbus), qemu `--version` = 5.2.0; tarball sha256 `692bc586...` PUBLISHED to the plane (`theia-runtime/mender-client/5.2.0-arago-aarch64/`); colony `install-mender-opkg.yml` (pinned, TLS + controller sha256); GS accepts opkg rigs by key | `test/opkg-mender/run.sh` (49 checks), `test_opkg_rig_with_mender_is_accepted_by_key`; on the board: section 5 |
| (2c) the SK OTA campaign | ready (section 5); needs the SK on | - |
| (2d) mender-server fork | no server-side v4 bug hit tonight: fork unchanged | - |
| (3) reachable address, not the first IPv4 | GS `api/gs_api/addressing.py`: remote_ip tag, then local_ip, else an explicit error; used by ucm, campaign, deployments | `test_reachable_address_everywhere` |
| (4F) com per-RPC roles | theia `9c14a925`: role from the verified cert (URI SAN, else OU), one table, unknown RPC denied, debug RPCs only with THEIA_COM_DEBUG_RPCS=1; LIVE on the AI-64 (0.3.1): operator observe OK, operator RequestUpdate PERMISSION_DENIED | `//services/com/test:com_roles_test` (353 checks) |
| (4G) e2e rigs with fleet certs | colony `044b45e`: SeaweedFS, digests, throwaway CA via the normal pki-enroll path, no com_insecure; full story green locally; the GitHub workflow was disabled for inactivity: re-enabled + dispatched | `helpers/com-mtls-check.py`: `mtls_operator_list_machines`, `plaintext_refused`, `tls_without_client_cert_refused`, `foreign_ca_client_cert_refused`, `wrong_rig_name_refused`; `com_insecure_absent`, `unsigned_artifact_refused` |
| (4H) no key material in packages | theia `2fd61510`: one scanner on release, release-swp, release-role, dist, build-debs, models push, the deb rule, CI release-deb; legacy `current/config/certs` no longer read | `tools/test_no_keys_gate.py` (58) |
| (4E) supervisor-gui on mTLS | pascal's: operator cert recipe + AI-64 machines.json posted on s-1aab5b21 | - |
| (5) MAVLink signing | design note chariot_ws `docs/MAVLINK-SECURITY.md` (`2ca0e30`) with acceptance tests per step | named in the note (`test_signing`, `sitl_signed_link`, `test_mavctrl_allowlist`, `test_gcs_keys`) |

## 4b. Adversarial review of what was merged (one pass, then fixed)
| finding | fix | regression test |
|---|---|---|
| HIGH: Mender tag / plane values reached colony's space-joined `-e k=v` -> ansible var injection, code on the controller | GS `29e6165` validates tags/journal/plane key before colony; colony `2e399b4` validates rig/host/role/extra (no `ansible_*` but ansible_user, no CLI-owned keys) and passes extra vars as ONE JSON `-e` | `test_tag_values_validated_before_deploy`, `test_plane_key_validated`, colony `test_extra_vars_are_json_not_kv`, `test_create_rejects_bad_host_role_extra` |
| HIGH: auto-accept took a matching-key set for a name already enrolled with ANOTHER key (Add device pointed at a different board) | GS `29e6165`: no accept, no tags, "rotate the key manually" | `test_no_auto_accept_when_name_enrolled_with_another_key` |
| MEDIUM: an older colony's 422 echoed the VPN auth key, relayed to the UI | GS never relays colony bodies; colony 422s carry no `input` | `test_colony_error_body_never_relayed`, colony `test_validation_error_has_no_input` |
| MEDIUM: opkg index `file` interpolated into a root shell | colony `f80a22f`: pattern-checked + quoted | `test/opkg-mender/run.sh` case 3g (53/53) |
| MEDIUM: etcd / supervisor bounced on every run on systemd-less rigs | colony `f2421c5` | `tests/test_no_unconditional_restarts.py` (now also kill/relaunch shells) |
| LOW: minted VPN key not revoked on refusal; secrets kept after abort/prune; device match in any attribute scope | GS `29e6165`, colony `2e399b4` | GS tests for the 409 revoke + identity scope |
Left: the old `/api/deployments/base` path does not revoke its minted key on refusal.
After the merge (GS `c8cd705`, colony `0abe6ce`) the live pass was repeated: list, T1, T3-T8 all PASS, no restart
of supervisor/etcd for the same version, AI-64 guards unchanged.

## 5. SK steps, ready to run (the SK was off)
`/bigdata/work/architect/ui-test/sk-morning.sh check` (reachability + guard snapshot), then `... t2` (Add device
sk-tda4vm through the UI in headless Chromium, screenshots in shots-live/, guard snapshot after). The client is
already published (`publish` is only needed for a new build). The OTA campaign on the SK (signed v1, failing v2
-> rollback) follows `docs/ota-campaign-2026-11-19.md` with the SK as the device. Board-only proofs left: the
binaries run on the real image, dbus-broker takes the policy, the SK checks in and is accepted by key.

## 6. Operator walkthrough for the bench (10 lines)
1. Open `http://100.125.123.35:8088` -> **Fleet**: `ai64-proto` (Mender + colony, COM mTLS) and `sk-tda4vm` (colony only).
2. Power the SK, wait a minute; **Add device**: name `sk-tda4vm`, address `100.105.15.20`, user `root`, role `master` (as provisioned before) -> **Add device**.
3. A yellow box = the board refused the GS key: paste the shown key into `~root/.ssh/authorized_keys` on the SK, press **Add device** again.
4. The drawer ticks through the tasks (3-5 min); reload the page once: the drawer comes back.
5. While it runs press **Add device** again with the same name: "already running: open" (one run only).
6. At the end: "sk-tda4vm is provisioned", VPN IP, certificate until ~90 days, Mender **accepted (key read from the board)**, rig class `opkg`.
7. Fleet: the SK row turns **Mender + colony**, AUTH accepted.
8. On `ai64-proto`: **Deploy runtime** -> pick the newest -> **Deploy ->**: success, COM badge stays **mTLS**.
9. **Rollouts**: T3-v2-failing reads "failure: rolled back to base-0.1.0-trixie-arm64"; Deployment -> select the AI-64: the same line in its history.
10. Mistakes are explained, not hidden: a wrong address says "cannot be reached", a wrong user shows the key to place.

## 7. Open after tonight (not repeats; for the user)
- **idsm firewall as user theia**: `idsm` (links libnftables) logs "Operation not permitted (you must be root)"
  twice a second since the stack start (24k lines on the AI-64): its nft rules are NOT applied. Decide: give the
  supervisor unit `AmbientCapabilities=CAP_NET_ADMIN` (every child gets it) or a small privileged helper for idsm
  only (recommended), and back off the retry.
- Decisions from the provisioning review: VUCM in scope or parked; removing com `Provision` + `rig_enroll.py`
  (cloud_provision) and the hidden endpoints.
- Small: `enroll-rig.sh` demands SERVER_SSH with SERVER_CA set; the colony template nests `com_insecure` inside the
  `tipc_scope=cluster` block (fails safe); enroll inventory hardcodes `device_type=theia-rig`; the theia `demo/`
  workspace the old e2e used is gone (the e2e now packs its own apps).

## 8. HIL app OTA path (the wc app's route), proven with a synthetic SWP (2026-11-19 late)
theia main `60e029ec` (G1 gate + the release-swp start_cmd fix), runtime **0.3.2** on the AI-64 (Deploy runtime
through the UI: stack restarted onto 0.3.2, gate module installed), GS `4b4c6fc` (`tools/swp-publish.sh`).
Synthetic app `hilprobe` (alpha + beta + health hook; workspace /bigdata/work/architect/hilprobe-ws) built with
`theia release-swp --arch ai64`, signed + uploaded with `swp-publish.sh`, rolled out from the GS (New Rollout API).
| test | result |
|---|---|
| H1 hilprobe 0.1.0 -> AI-64 | success: `current` = releases/hilprobe-0.1.0, alpha + beta run from it, hook "alpha + beta" exit 0, commit stood |
| H2 hilprobe 0.1.1 (beta exits at start) | gate: "app process 'beta' MISSING ... Mender will roll back"; ROLLBACK -> hilprobe-0.1.0, both processes running again; UI: "rolled back to hilprobe-0.1.0-trixie-arm64" (shots-live/50, 51) |
| Guards | unchanged (no reboot, holds, extlinux, r5f running). The R5F firmware + ttyRPMSG0 changes at 20:46 UTC were opus's HIL firmware swap from 10.0.0.78 (journal), not the OTA path. |
Not run tonight (budget): H3 unsigned refused (proven 2026-11-19 with the base SWP), H4 runtime mismatch (API test
`test_rollout_refuses_runtime_mismatch`), H5 next iteration. Known small defect: the GS artifact list shows
`requires_runtime` empty although swp-publish writes it into the Mender metadata (the abi check by name applies).
For the wc SWP: an SWP's executor.json REPLACES the rig's supervised tree: keep the runtime's services exactly
(nm, fw, tsync, rds off; machine master) or the board can lose its network.

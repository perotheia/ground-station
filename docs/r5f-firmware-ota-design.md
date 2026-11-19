# R5F firmware as an OTA artifact (`theia-fw`): design note (architect, 2026-11-20; NOT implemented)

Context: the AI-64's main-domain R5F core 1 (remoteproc6, `5e00000.r5f`) runs ArduRover (Zephyr) and talks to
the A72 over RPMsg (`/dev/ttyRPMSG0`). Today the image is swapped by hand: `/lib/firmware/j7-main-r5f1_0-fw` is
a symlink to `zephyr/<build>.elf`, and the core's memory layout comes from a device-tree overlay named on the
`fdtoverlays` line of `/boot/firmware/extlinux/extlinux.conf` (today `k3-j721e-ai64-r5fss1-lockstep.dtbo`;
`-bench`, `-split` variants exist). The app SWP path (theia-swp) must never touch any of this; firmware gets
its own artifact kind, gate and rollback.

## Two cases, decided by the artifact, never guessed on the rig
| case | what changes | reboot | when |
|---|---|---|---|
| F1 ELF only | the core's image | NO: remoteproc stop/start | every HIL firmware iteration |
| F2 ELF + overlay | memory carve-outs / core mode (lockstep vs split) | YES, one, operator-approved | rare; a layout change |
The artifact carries `fw.json`: `{core: "5e00000.r5f", firmware_name: "j7-main-r5f1_0-fw", elf: "<file>",
elf_sha256, overlay: null | {file, sha256, replaces: "<dtbo>"}, gate: {...}}`. A rig refuses an artifact whose
`core` / `firmware_name` do not match what remoteproc reports (`/sys/class/remoteproc/*/name`, `/firmware`).

## Module `theia-fw` (Mender update module, POSIX sh, root)
- **ArtifactInstall (F1)**: stage the ELF as `zephyr/fw-<artifact>.elf` (sha256 checked against `fw.json`,
  which is covered by the artifact signature), record the current link target, then: stop the Theia app
  processes that hold the RPMsg port (via the supervisor, not by killing), `echo stop > .../state`, flip the
  symlink atomically (`ln -sfn` on a temp name + `mv -T`), `echo start`.
- **ArtifactCommit gate**: within `THEIA_FW_GATE_S` (default 30 s): remoteproc `state == running`, the
  `virtio*.rpmsg-tty` endpoint and `/dev/ttyRPMSG0` back, and an application-level heartbeat (a MAVLink
  HEARTBEAT from the autopilot sysid, read by a small `fw-gate` helper with the port opened exclusively for at
  most 5 s, before the app processes are released). Any miss -> exit 1 -> Mender runs ArtifactRollback.
- **ArtifactRollback**: stop, flip the link back to the recorded target, start, re-run the same gate (logged;
  a failing rollback leaves the core stopped and says so, loud, in the device log: never a silent half state).
- **F2 (overlay)**: ArtifactInstall writes the new dtbo next to the old one, edits ONLY the token on the
  `fdtoverlays` line (keeps `extlinux.conf.prev`), sets `REBOOT_REQUIRED`; Mender's ArtifactReboot is used ONLY
  when the deployment was created with the GS flag "allow reboot" (the operator's explicit yes). After reboot:
  the same gate; failure -> restore `extlinux.conf.prev` + the old ELF link + reboot once more.
- **Never**: `/boot/firmware` beyond the one overlay token, other remoteprocs (C66/C71 DSPs, MCU R5F,
  r5f0_x), the kernel or its holds, any file not named in `fw.json`.

## GS and signing
Same signing key and `ArtifactVerifyKey` as SWPs; a separate artifact type (`theia-fw`) so Releases and
Rollouts show it as FIRMWARE. A firmware rollout targets one device at a time; F2 needs "allow reboot".
Coexistence with the app SWP: a firmware deployment and an app deployment never run at the same time on a rig
(Mender runs one deployment per device; the GS also refuses to create a second while one is pending).

## Builder
chariot / pascal's Zephyr build (`ibnHatab/zephyr` ai64, ArduPilot branch ai64) produces the ELF; a small
`theia release-fw` (or a script in the firmware repo) writes `fw.json` + the Mender artifact with
`mender-artifact write module-image -T theia-fw`, signs it, uploads like a SWP. No keys inside (no-keys gate).

## Tests (when built)
`test_fw_module.sh` with a fake sysfs remoteproc + fake rpmsg tty: F1 commit, F1 gate miss -> rollback to the
old link, wrong core refused, sha mismatch refused, F2 without "allow reboot" refused, rollback failure reported.
Live (bench, pascal present): F1 ELF swap A -> B -> rollback on a bad ELF (one that never sends a heartbeat).

## Open (the user / pascal)
1. The heartbeat source for the gate: MAVLink HEARTBEAT (ArduRover) is natural; a Zephyr-level rpmsg ping
   would make the gate independent of ArduPilot.
2. Who may hold `/dev/ttyRPMSG0` in normal operation (the WC MavLink node vs pascal's tools) — the gate needs a
   short exclusive window.
3. Whether F2 is ever wanted over the air, or only on the bench.

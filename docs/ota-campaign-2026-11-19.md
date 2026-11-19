# OTA campaign on the BeagleBone AI-64, 2026-11-19 (fleet-infra s-1aab5b21, accept 4)

Ground station on panzer (Mender server v4.1.5, images pinned), board `ai64-proto` (Debian 13, Mender client
5.2.0, theia runtime 0.3.0-trixie-arm64), artifacts of type `theia-swp` built with mender-artifact 4.4.2 and
signed with the fleet key (RSA-3072, private key only on the GS). All three deployments went through the
Mender management API from gs-api to the one device. Raw logs: panzer `~/fleet/campaign/log/`.

| # | artifact | expected | result on the board |
|---|---|---|---|
| T1 | `base-0.1.1-unsigned` (no signature) | refused | **failure in 5 s**: `Signature verification Error: expecting signed artifact, but no signature file found`; `current` unchanged |
| T2 | `base-0.1.0-trixie-arm64` (signed, the runtime's own executor) | installs | **success in 35 s**: `current -> releases/base-0.1.0-trixie-arm64`, commit health gate passed (supervisor stable), 13 processes |
| T3 | `base-0.2.0-trixie-arm64` (signed, executor.json deliberately not JSON) | commit fails, rollback | **failure in 50 s**: installed, `current -> base-0.2.0`, supervisor restarted, `COMMIT FAILED — no healthy supervisor` after the 30 s gate, `ROLLBACK current -> releases/base-0.1.0-trixie-arm64`, supervisor restarted; the board reports `base-0.1.0` again |

Around every step: supervisor active with 0 unit restarts after the step, 6 remoteproc cores `running` (incl.
`5e00000.r5f`, ArduRover), `/dev/ttyRPMSG0` present, free RAM 1.92-1.93 GB, load <= 0.74. No reboot.

What this proves: unsigned artifacts are refused (the verify key was pushed by the controller at provision);
a signed release installs by the release-dir + symlink flip; a release whose supervisor does not come up is
NOT committed and is rolled back automatically (the health gate added in theia 73b515a).

What it does not prove: anti-downgrade (not implemented), the UCM hand-off (the state script is not carried in
the artifact), an app SWP with binaries (these were null-software base packages), and the SK (no Mender client
on Arago: base plane only; the route there is meta-mender in a TI Yocto build).

## Rule until com mTLS lands
A rig does not run the Theia stack (com) outside a bench before hardening C1 (mandatory mTLS) and C2
(authenticated provisioning) are merged: com listens on `*:7700` without TLS today and DoIP on `0.0.0.0:13400`.
After this campaign the AI-64's `theia-supervisor` unit was stopped and disabled at boot.

# Two-plane OTA — operator command reference

> **Status:** validated (prototype proven end-to-end on taycann) · **Scope:** the
> day-1/2/3 update flow for a Theia rig — runtime provision, base, and app SWP —
> across the CLI (`theia`), the GS web UI, and the Mender/colony APIs.
> **Design:** theia `docs/design/two-plane-ota.md`.

---

## 1. The model in one paragraph

A Theia rig runs **exactly one active release at a time**, selected by the
`/opt/theia/current` symlink. The **runtime** is itself a release
(`releases/runtime-<ver>`); an **app SWP** is another (`releases/<swp>-<ver>`).
A rollout is an atomic `current`-flip; a rollback is the reverse. The two planes
are independent in version (a runtime *patch* never forces an SWP change) and
meet only on the `/opt/theia` file layout.

```
/opt/theia/
  bin/supervisor                         the updater — flat, never swapped
  current → releases/<active>            the ONE active release (flip point)
  releases/
    runtime-0.3.2-noble-amd64/{bin,config}   the runtime release (colony/deb)
    base-0.1.0-noble-amd64/{bin,config}      null-software SWP (day-2 baseline)
    otatest-0.1.0-noble-amd64/{bin,config}   an app SWP (day-3)
  previous → releases/<prev>             rollback target
```

The supervisor always reads `current/config/executor.json` (via `theia-run`), so
the flip is the whole activation — a supervisor restart adopts the new tree.

---

## 2. The version rule (what bump = what deploy)

| SWP bump | Meaning | Runtime action | How to deploy |
|---|---|---|---|
| **patch** x.y.**Z** | free binary swap, no interface/config change | none | rollout the SWP |
| **minor** x.**Y**.0 | interface moved — needs a config migration | none (same runtime) | rollout SWP (migration runs before the flip) |
| **major** **X**.0.0 | runtime interface changed | **re-provision runtime first** | provision runtime, then rollout SWP |

**An SWP major depends on a runtime minor.** The GS rollout gate enforces it:
a device's runtime **major.minor** must equal the SWP's `requires_runtime`
major.minor (patch-independent). A `0.4.0` device is blocked for an SWP pinned to
`0.3.x` — "update the base first".

---

## 3. Day-1 — provision the runtime (colony)

Lays `releases/runtime-<ver>/{bin,config}` + `current → it`. Two ways:

**CLI (drives colony via the GS):**
```
theia deploy <distribution> <device> --watch      # runtime + base in one
```

**GS web UI — Deployment tab:**
1. Select the target (Targets column) and the distribution (Distributions column).
2. Click **Deploy →**. Both planes run; watch the Action History (`base` =
   colony/runtime, `app` = Mender/SWP).

**Verify on-device:**
```
readlink /opt/theia/current            # → releases/runtime-<ver>
systemctl is-active theia-supervisor   # active
tdb info | grep release                # the running release
```

---

## 4. Day-2 — the null-software base SWP

The **resting SWP state**: services baseline, no FCs. `current → releases/base-<ver>`.
Makes day-2 and day-3 symmetric flips.

**Build + publish** (from the app workspace):
```
theia release-swp base --null \
    --arch noble --swp-version 0.1.0 \
    --requires-runtime 0.3.2-noble-amd64 --s3 <minio-url>
```
`--null` strips the app nodes (drops `applications_sup`) and ships no binaries —
the module assembles `bin` from the current runtime release on flip.

**Deploy:** same as any SWP (a distribution referencing the `base` build, or a
direct Mender deployment). On flip: `current → releases/base-<ver>`, services
running, **no app proc**.

---

## 5. Day-3 — an app SWP

**Build + publish:**
```
theia manifest <app-rig>                # generate the manifest first
theia release-swp <app> \
    --arch noble --swp-version 0.1.0 \
    --requires-runtime 0.3.2-noble-amd64 --s3 <minio-url>
```

**Rollout (CLI):**
```
theia rollout create <name> --app <app> --to <ver> --group <group> [--phases N]
theia rollout status <name> --ucm        # phases + on-device UCM/SM state
theia rollout advance <name>             # gate the next phase
theia rollout abort <name>               # halt (rolls devices back)
```

**Rollout (GS web UI — Rollouts tab):** New Rollout → pick app + version +
group; each phase gates manually. Select a rollout row to see the transport +
ECU (UCM) planes.

On flip: `current → releases/<app>-<ver>`, the app's `executor.json` shadows the
baseline (its FC nodes now run), supervisor restarts to adopt it.

---

## 6. Rollback

Every install snapshots `previous` first, so rollback is one flip:
```
# Mender drives it on a failed deployment (ArtifactRollback);
# manual: the module's ArtifactRollback flips current → previous.
readlink /opt/theia/previous            # the rollback target (a real release)
```

---

## 7. Inspect + manage (CLI)

```
theia fleet                              # devices: name, runtime, IP, group
theia releases base|app|roles            # published builds (L=deployed, P=pinned)
theia distributions                      # the combined bundles
theia deploy status [device]             # the Action History, both planes
theia target list|pin|unpin|delete|clear # manage a device / clear history
tdb info                                 # host + build facts + the running release
```
Env for the GS verbs: `$THEIA_GS_URL` (default `http://10.0.0.99:8090`),
`$THEIA_GS_KEY` (mutating routes).

---

## 8. On-device checks (a rig)

```
readlink /opt/theia/current              # the active release
ls /opt/theia/releases/                  # all staged releases (coexist)
sudo grep -c '"name": *"<fc>"' /opt/theia/current/config/executor.json   # is an FC active?
systemctl show theia-supervisor -p NRestarts --value                     # 0 = stable
find -L /opt/theia/current -maxdepth 3 -name executor.json >/dev/null && echo loop-safe
```

---

## 9. Gotchas

- **Signing.** A fleet with `ArtifactVerifyKey` set (mender.conf) rejects an
  unsigned SWP ("expecting signed artifact"). Sign with the fleet key
  (`theia cert generate` / `--sign-key`); a test rig can drop the verify key.
- **The launcher + modules ship in the runtime plane.** `theia release <target>
  --s3` bundles `theia-run.sh` + the `ota/modules/` into the runtime plane's
  `manifest.tar.gz`; colony ships them. A stale plane = a stale launcher/module —
  re-release the runtime plane after changing them.
- **`current` is NEVER `.`** — the runtime is a real release dir. `current → .`
  self-loops on `-L` walks and breaks `previous`/rollback.

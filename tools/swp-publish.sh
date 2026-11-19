#!/usr/bin/env bash
# tools/swp-publish.sh — sign an application SWP (the output of `theia release-swp`) with the fleet
# signing key and upload it into the fleet's Mender server, where the GS Releases page lists it and a
# GS Rollout can deploy it (spec hil-app-ota: G2 artifacts, G3 runtime rule).
#
# RUNS ON THE GROUND-STATION HOST (panzer): the signing key never leaves it, and the upload goes
# through the gs-api container's Mender client (gs-api has no upload endpoint; the Mender token
# stays in that container's settings, nothing is printed).
#
# Usage:
#   tools/swp-publish.sh [--dry-run] [--unsigned] [--device-type T] [--out DIR]
#                        [--description TEXT] [--allow-any-name] <swp>...
#     <swp>            a `theia release-swp` output, copied to this host:
#                        dist/apps/<app>/<app>-<ver>-<abi>.mender   (the unsigned .mender a build host
#                                                                   without the fleet key writes), or
#                        dist/apps/<app>/<app>-<ver>-<abi>.tar.gz   (the payload alone)
#     --dry-run        build, sign and validate into --out; do NOT upload (prints what it would do)
#     --unsigned       do not sign (a negative test artifact: the rig must refuse it)
#     --device-type T  Mender device type (default theia-rig, the fleet's)
#     --out DIR        where the signed .mender goes (default ~/fleet/hil)
#     --description S  Mender artifact description (default "<name> via swp-publish")
#     --allow-any-name accept an artifact name that is not <app>-<semver>-<abi> (test artifacts)
#
# What it does, per <swp>:
#   1. takes the payload files release-swp packs (<name>.tar.gz + version.txt + machines.json) out
#      of the .mender (or next to the .tar.gz); the artifact NAME is version.txt (= what the
#      on-device theia-swp module checks), which must be <app>-<semver>-<abi> (the GS reads the abi
#      from the name);
#   2. reads requires_runtime.txt + app.json from the payload, refuses a payload carrying a private
#      key (same rule as theia's no-keys gate: the module also refuses certs) and prints the
#      declared processes / health hook;
#   3. re-packs it with the pinned mender-artifact (mendersoftware/create-artifact-worker:v4.1.5)
#      as a `theia-swp` module image, device type theia-rig, meta-data {"requires_runtime": ...}
#      (the GS G3 rule reads it: the exact runtime, not only the abi), SIGNED with
#      ~/fleet/secrets/swp-signing/artifact-signing-key.pem;
#   4. `mender-artifact validate --key artifact-verify-key.pem` (the key colony gives every rig);
#   5. uploads it to Mender via gs-api (skipped with --dry-run) and lists it back.
#
# Example (chariot, the WC HIL SWP):
#   build host:  theia release-swp wc --arch ai64 --swp-version 0.1.0 --requires-runtime 0.3.2
#                scp dist/apps/wc/wc-0.1.0-trixie-arm64.mender panzer.local:~/fleet/hil/in/
#   panzer:      ~/fleet/ground-station/tools/swp-publish.sh --dry-run ~/fleet/hil/in/wc-0.1.0-trixie-arm64.mender
#                ~/fleet/ground-station/tools/swp-publish.sh ~/fleet/hil/in/wc-0.1.0-trixie-arm64.mender
#   then: GS web, Releases -> the artifact -> New Rollout -> the HIL device.
set -euo pipefail

IMG=mendersoftware/create-artifact-worker:v4.1.5
KEYS="${SWP_SIGNING_DIR:-$HOME/fleet/secrets/swp-signing}"
GS_API_CONTAINER="${GS_API_CONTAINER:-gs-api}"
DRY=0; UNSIGNED=0; DTYPE=theia-rig; OUT="$HOME/fleet/hil"; DESC=""; ANY_NAME=0; INPUTS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --unsigned) UNSIGNED=1 ;;
    --device-type) DTYPE="${2:?}"; shift ;;
    --out) OUT="${2:?}"; shift ;;
    --description) DESC="${2:?}"; shift ;;
    --allow-any-name) ANY_NAME=1 ;;
    -h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
    -*) echo "swp-publish: unknown option $1" >&2; exit 2 ;;
    *) INPUTS+=("$1") ;;
  esac; shift
done
[ ${#INPUTS[@]} -gt 0 ] || { echo "usage: swp-publish.sh [--dry-run] <release-swp .mender|.tar.gz>..." >&2; exit 2; }
[ "$UNSIGNED" = 1 ] || [ -r "$KEYS/artifact-signing-key.pem" ] || { echo "swp-publish: no signing key in $KEYS" >&2; exit 1; }
[ -r "$KEYS/artifact-verify-key.pem" ] || { echo "swp-publish: no verify key in $KEYS" >&2; exit 1; }
mkdir -p "$OUT"; OUT="$(cd "$OUT" && pwd)"

ma() {   # mender-artifact from the pinned image; $W is mounted at /w
  docker run --rm --user "$(id -u):$(id -g)" --entrypoint mender-artifact -v "$W:/w" "$IMG" "$@"
}

publish_one() {
  local in; in="$(readlink -f "$1")"
  W="$(mktemp -d "$OUT/.swp-publish.XXXXXX")"   # removed by the caller loop
  mkdir -p "$W/files" "$W/payload"
  case "$in" in
    *.mender)
      tar -xf "$in" -C "$W" data/0000.tar.gz 2>/dev/null \
        || { echo "swp-publish: $in has no data/0000.tar.gz (not a single-update module artifact)" >&2; return 1; }
      tar -xzf "$W/data/0000.tar.gz" -C "$W/files" ;;
    *.tar.gz)
      cp "$in" "$W/files/"
      tar -xzf "$in" -C "$W/files" ./version.txt ./manifest/machines.json
      mv "$W/files/manifest/machines.json" "$W/files/machines.json"; rmdir "$W/files/manifest" ;;
    *) echo "swp-publish: $in is neither a .mender nor a payload .tar.gz" >&2; return 1 ;;
  esac
  [ -f "$W/files/version.txt" ] || { echo "swp-publish: no version.txt in $in" >&2; return 1; }
  local name tgz; name="$(head -1 "$W/files/version.txt" | tr -d '[:space:]')"
  tgz="$W/files/$name.tar.gz"
  [ -f "$tgz" ] || { echo "swp-publish: payload $name.tar.gz missing in $in" >&2; return 1; }
  if [ "$ANY_NAME" = 0 ] && ! printf '%s' "$name" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9_.+]*-[0-9]+\.[0-9]+\.[0-9]+-[a-z0-9]+-[a-z0-9]+$'; then
    echo "swp-publish: artifact name '$name' is not <app>-<semver>-<abi> (the GS reads the abi from it); --allow-any-name for a test artifact" >&2
    return 1
  fi
  tar -xzf "$tgz" -C "$W/payload"
  if grep -rlsE -- '-----BEGIN ([A-Z]+ )?PRIVATE KEY-----' "$W/payload" >/dev/null \
     || find "$W/payload" -name '*.key' | grep -q .; then
    echo "swp-publish: REFUSED — $name carries private key material" >&2; return 1
  fi
  local rt; rt="$(tr -d '[:space:]' < "$W/payload/requires_runtime.txt" 2>/dev/null || true)"
  echo "== $name  (requires_runtime ${rt:-none}, device type $DTYPE)"
  if [ -f "$W/payload/app.json" ]; then
    python3 - "$W/payload/app.json" <<'PY' || { echo "swp-publish: unreadable app.json in $name" >&2; return 1; }
import json, sys
a = json.load(open(sys.argv[1]))
procs = ", ".join("%s (bin/%s)" % (p["name"], p["exe"]) for p in a.get("processes") or [])
print("   processes:", procs or "none (supervisor-only gate)",
      "| health hook:", a.get("health") or "none", "| gate:", a.get("gate") or "defaults")
PY
  else
    echo "   WARNING: no app.json (the rig gates on the supervisor only)"
  fi
  local parts=(--file "/w/files/$name.tar.gz" --file /w/files/version.txt)
  [ -f "$W/files/machines.json" ] && parts+=(--file /w/files/machines.json)
  local meta=()
  if [ -n "$rt" ]; then
    printf '{"requires_runtime": "%s"}\n' "$rt" > "$W/meta.json"; meta=(--meta-data /w/meta.json)
  fi
  local sign=() suffix=""
  if [ "$UNSIGNED" = 0 ]; then
    install -m 600 "$KEYS/artifact-signing-key.pem" "$W/.sign.pem"; sign=(--key /w/.sign.pem)
  else
    suffix=".unsigned"
  fi
  ma write module-image --type theia-swp --artifact-name "$name" --device-type "$DTYPE" \
     "${parts[@]}" "${meta[@]}" "${sign[@]}" --output-path "/w/out.mender" >"$W/write.log" 2>&1 \
    || { cat "$W/write.log" >&2; echo "swp-publish: mender-artifact write FAILED for $name" >&2; return 1; }
  rm -f "$W/.sign.pem"
  cp "$KEYS/artifact-verify-key.pem" "$W/vk.pem"
  if [ "$UNSIGNED" = 0 ]; then
    ma validate --key /w/vk.pem /w/out.mender >"$W/validate.log" 2>&1 \
      || { cat "$W/validate.log" >&2; echo "swp-publish: validate --key FAILED for $name" >&2; return 1; }
    echo "   signed + validated with artifact-verify-key.pem"
  else
    echo "   UNSIGNED (negative test artifact: a rig with ArtifactVerifyKey refuses it)"
  fi
  ma read -k /w/vk.pem /w/out.mender 2>/dev/null \
    | grep -E '^ *(Signature|Compatible types|- Type):|"requires_runtime"' | sed 's/^ */   | /' || true
  local dst="$OUT/$name$suffix.mender"
  mv "$W/out.mender" "$dst"
  echo "   $dst  $(stat -c %s "$dst") B  sha256 $(sha256sum "$dst" | cut -c1-64)"
  if [ "$DRY" = 1 ]; then
    echo "   DRY-RUN: would upload $(basename "$dst") to Mender via $GS_API_CONTAINER (description: ${DESC:-$name via swp-publish})"
    return 0
  fi
  local tmpn="/tmp/swp-publish-$$-$(basename "$dst")"
  docker cp "$dst" "$GS_API_CONTAINER:$tmpn" >/dev/null
  docker exec -i "$GS_API_CONTAINER" python - "$tmpn" "$name" "${DESC:-$name via swp-publish}" <<'PY'
import os, sys
from pathlib import Path
from gs_api.clients import mender_client
from gs_api.settings import settings
f, name, desc = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
m = mender_client(settings())
try:
    print("   uploaded:", name, "id", m.upload_artifact(f, desc))
finally:
    os.unlink(f)
rows = [a for a in m.list_artifacts() if a.get("name") == name]
print("   in Mender:", ", ".join(f"{a.get('name')} signed={a.get('signed')} size={a.get('size')}" for a in rows) or "NOT LISTED")
sys.exit(0 if rows else 1)
PY
}

rc=0
W=""; trap 'rm -rf "${W:-/nonexistent}"' EXIT
for i in "${INPUTS[@]}"; do
  publish_one "$i" || rc=1
  rm -rf "$W"; W=""
done
exit $rc

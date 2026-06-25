#!/usr/bin/env bash
# mender/runtime/seed-official.sh — seed an OFFICIAL Theia runtime release into the
# MinIO runtime plane (the baseline colony provisions from). One-time, GW-side.
#
# The runtime plane (theia-runtime + theia-services .debs) is OUR registry, NOT the
# Mender artifact store — colony's factory provision pulls runtime+services from
# here (s3://theia-runtime/<ver>/), so a rig can be provisioned from the official
# tag OR a dev rc WITHOUT cutting a GitHub release. Dev rc's are pushed by
# `theia release --runtime`; THIS seeds the official baseline.
#
# Usage (on the GW host, e.g. dalek):
#   seed-official.sh <ver> <deb-dir> [s3-endpoint]
#     <ver>       e.g. 0.2.0  (the S3 path s3://theia-runtime/<ver>/)
#     <deb-dir>   dir holding theia-runtime_*.deb + theia-services_*.deb (e.g. dist/debian/**)
#     s3-endpoint default http://localhost:9000 (the ground-station MinIO)
#
# Source the runtime debs from theia/dist (theia release --arch host/<arch>).
set -euo pipefail

VER="${1:?usage: seed-official.sh <ver> <deb-dir> [s3-endpoint]}"
DEB_DIR="${2:?deb-dir required (holds theia-runtime + theia-services .debs)}"
S3="${3:-http://localhost:9000}"
USER="${MINIO_USER:-theia}"; PASS="${MINIO_PASSWORD:-theiaminio}"
BUCKET="theia-runtime"

command -v mc >/dev/null || { echo "mc (minio client) required" >&2; exit 1; }
mc alias set gs "$S3" "$USER" "$PASS" >/dev/null
mc mb -p "gs/$BUCKET" 2>/dev/null || true

# Collect the runtime + services debs (recursive — dist/debian nests per-package).
mapfile -t debs < <(find "$DEB_DIR" -name "theia-runtime_*.deb" -o -name "theia-services_*.deb" | sort)
[ "${#debs[@]}" -ge 2 ] || { echo "need theia-runtime + theia-services debs under $DEB_DIR" >&2; exit 1; }

dest="gs/$BUCKET/$VER"
echo "[seed] $VER → s3://$BUCKET/$VER/  ($S3)"
idx_versions=""
for d in "${debs[@]}"; do
  base="$(basename "$d")"
  mc cp "$d" "$dest/$base" >/dev/null
  sha="$(sha256sum "$d" | cut -d' ' -f1)"
  echo "  + $base  sha256=$sha"
  idx_versions+="    {\"file\": \"$VER/$base\", \"sha256\": \"$sha\"},
"
done

# index.json — the version truth for the runtime plane (colony reads `latest` / pins <ver>).
cat > /tmp/runtime-index-$VER.json <<JSON
{ "plane": "runtime", "version": "$VER",
  "debs": [
$(printf '%s' "$idx_versions" | sed '$ s/,$//')
  ] }
JSON
mc cp /tmp/runtime-index-$VER.json "$dest/index.json" >/dev/null
echo "[seed] s3://$BUCKET/$VER/index.json written. colony provisions <ver>=$VER from here."

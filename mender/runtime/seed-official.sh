#!/usr/bin/env bash
# mender/runtime/seed-official.sh — seed an OFFICIAL Theia runtime release into the
# MinIO runtime plane (the baseline colony provisions from). One-time, GW-side.
#
# The runtime plane (theia-runtime + theia-services .debs) is OUR registry, NOT the
# Mender artifact store — colony's factory provision pulls runtime+services from
# s3://theia-runtime/<ver>/, so a rig can be provisioned from the official tag OR a
# dev rc WITHOUT cutting a GitHub release. Dev rc's: `theia release --runtime`.
#
# Usage (on the GW host, e.g. dalek):
#   seed-official.sh <ver> <deb-dir> [s3-endpoint] [distro-tag]
#     <ver>        e.g. 0.2.0  (→ s3://theia-runtime/<ver>/)
#     <deb-dir>    dir holding theia-runtime_*.deb + theia-services_*.deb
#     s3-endpoint  default http://localhost:9000 (the ground-station MinIO)
#     distro-tag   OPTIONAL ABI qualifier, e.g. ubuntu24. com+per link grpc++/
#                  libprotobuf SHARED on x86 (the rig's own apt provides them), so
#                  the deb's sonames are tied to the build distro (Ubuntu 22 →
#                  libprotobuf.so.23, Ubuntu 24 → .so.32). When set, the plane key
#                  becomes <ver>-<distro-tag>/ so amd64 debs of different ABIs don't
#                  collide; the rig's registry sets runtime_version to MATCH (e.g.
#                  0.2.0-ubuntu24). Omit it for the arm64/static distro (self-
#                  contained, no shared grpc/protobuf → ABI-agnostic).
#
# Uses the AWS CLI (S3-compatible) against MinIO — `mc` on a host may be Midnight
# Commander; aws --endpoint-url is unambiguous. Creds from MINIO_USER/PASSWORD.
set -euo pipefail

VER="${1:?usage: seed-official.sh <ver> <deb-dir> [s3-endpoint] [distro-tag]}"
DEB_DIR="${2:?deb-dir required}"
S3="${3:-http://localhost:9000}"
DISTRO="${4:-}"
# The plane KEY — version, optionally ABI-qualified. The rig's runtime_version
# must equal this exactly (it's what install-runtime-s3.yml reads index.json under).
KEY="$VER${DISTRO:+-$DISTRO}"
export AWS_ACCESS_KEY_ID="${MINIO_USER:-theia}"
export AWS_SECRET_ACCESS_KEY="${MINIO_PASSWORD:-theiaminio}"
export AWS_DEFAULT_REGION="us-east-1"
BUCKET="theia-runtime"
aws="aws --endpoint-url $S3 s3"

command -v aws >/dev/null || { echo "aws cli required" >&2; exit 1; }
$aws mb "s3://$BUCKET" 2>/dev/null || true   # idempotent

mapfile -t debs < <(find "$DEB_DIR" -name "theia-runtime_*.deb" -o -name "theia-services_*.deb" | sort)
[ "${#debs[@]}" -ge 2 ] || { echo "need theia-runtime + theia-services debs under $DEB_DIR" >&2; exit 1; }

echo "[seed] $KEY → s3://$BUCKET/$KEY/  ($S3)"
idx=""
for d in "${debs[@]}"; do
  base="$(basename "$d")"
  $aws cp "$d" "s3://$BUCKET/$KEY/$base" >/dev/null
  sha="$(sha256sum "$d" | cut -d' ' -f1)"
  echo "  + $base  sha256=${sha:0:16}…"
  idx+="    {\"file\": \"$KEY/$base\", \"sha256\": \"$sha\"},
"
done
cat > /tmp/runtime-index-$KEY.json <<JSON
{ "plane": "runtime", "version": "$VER", "distro": "${DISTRO:-any}", "key": "$KEY",
  "debs": [
$(printf '%s' "$idx" | sed '$ s/,$//')
  ] }
JSON
$aws cp /tmp/runtime-index-$KEY.json "s3://$BUCKET/$KEY/index.json" >/dev/null
echo "[seed] s3://$BUCKET/$KEY/index.json written."

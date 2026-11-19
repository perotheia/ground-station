#!/usr/bin/env bash
# mender/server/up.sh — stand up a LOCAL Mender server (the VUCM OTA
# transport) from the open-source mendersoftware/mender-server repo, on a fleet
# host (we use dalek). The harness (fleet/fleet.py) then drives it over the
# Management API: upload a theia-release artifact, deploy to a device group; the
# rig's mender client pulls it → the theia-release module + state-script → UCM.
#
# The OSS Mender server isn't a single image — it's a multi-service compose
# (deployments/deviceauth/inventory/useradm/gui/workflows + mongo/nats/traefik).
# This clones it at a pinned tag and runs ITS `docker compose up -d`. Opt-in:
# needs docker + ~2-3GB + network. This is the next-release server team's surface;
# the harness proves the wire today.
#
# Usage (on the fleet host, e.g. dalek):
#   up.sh up                    # clone (if needed) + compose up -d
#   up.sh user <email> <pass>   # create the initial admin
#   up.sh token <email> <pass>  # mint a PAT, print `export MENDER_TOKEN=…`
#   up.sh down                  # compose down
#
# The API gateway listens on https://localhost (self-signed). From another host,
# point fleet.py at https://<fleet-host> --insecure (or add docker.mender.io to
# /etc/hosts → the fleet host IP, the cert's vhost).
set -euo pipefail

MENDER_SERVER_REF="${MENDER_SERVER_REF:-v4.1.5}"
# The IMAGES are a separate pin: upstream's .env at a release tag is a MOVING tag (v4.0.1: `v4.0`; v4.1.5: `main`),
# so a tagged checkout alone runs whatever was pushed last. Pin the images to the same release.
export MENDER_IMAGE_TAG="${MENDER_IMAGE_TAG:-$MENDER_SERVER_REF}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-fleet-mender}"   # one named project: removable cleanly

# Mender's own secrets: upstream defaults are public (S3 key "mender" / "thisisnotsecure", presign secret
# "insecure secret"). Generate ours once into a 0600 file beside the checkout and feed them to compose:
# the S3 credentials through upstream's own variables, the presign secret through a small override.
_secrets() {
    SEC="${MENDER_SECRETS_FILE:-$WORK/../secrets/mender-server.env}"
    mkdir -p "$(dirname "$SEC")"
    if [ ! -f "$SEC" ]; then
        ( umask 077; printf 'MENDER_ACCESS_KEY_ID=mender-%s\nMENDER_SECRET_ACCESS_KEY=%s\nMENDER_PRESIGN_SECRET=%s\n' \
            "$(openssl rand -hex 4)" "$(openssl rand -hex 24)" "$(openssl rand -base64 32)" > "$SEC" )
    fi
    set -a; . "$SEC"; set +a
    cat > "$WORK/docker-compose.override.yml" <<'YML'
# written by ground-station mender/server/up.sh: our own presign secret (upstream hard-codes a public one)
services:
  deployments:
    environment:
      DEPLOYMENTS_PRESIGN_SECRET: "${MENDER_PRESIGN_SECRET:?}"
YML
}
WORK="${MENDER_SERVER_DIR:-$HOME/mender-server}"
REPO="https://github.com/mendersoftware/mender-server.git"

ACTION="${1:-up}"

_clone() {
    if [ ! -d "$WORK/.git" ]; then
        echo "[server] clone $REPO @ $MENDER_SERVER_REF → $WORK"
        git clone --depth 1 -b "$MENDER_SERVER_REF" "$REPO" "$WORK"
    fi
}

case "$ACTION" in
  up)
    _clone
    # No override: upstream v4.1.5 ships traefik v3.7.x and its own S3 (SeaweedFS) and routes correctly on
    # Docker 29 (verified on panzer 2026-11-19). The old theia override + static routes are retired.
    rm -f "$WORK/compose/config/traefik/theia-routes.yaml"
    _secrets
    # The gateway's TLS key: upstream COMMITS a demo certificate + private key (compose/certs/mender.{crt,key}),
    # i.e. a public key pair. Replace it with our own (once), same names (CN docker.mender.io + the fleet names in
    # FLEET_TLS_NAMES); rigs and the GS pin this certificate.
    if git -C "$WORK" diff --quiet -- compose/certs 2>/dev/null; then
        echo "[server] generating the gateway certificate (replacing upstream's public demo key)"
        san="DNS:docker.mender.io,DNS:*.docker.mender.io,DNS:localhost"
        for n in ${FLEET_TLS_NAMES:-}; do case "$n" in *[0-9].*[0-9]) san="$san,IP:$n" ;; *) san="$san,DNS:$n" ;; esac; done
        ( umask 077; openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 730 \
            -subj "/CN=docker.mender.io/O=perotheia fleet" -addext "subjectAltName=$san" \
            -keyout "$WORK/compose/certs/mender.key" -out "$WORK/compose/certs/mender.crt" 2>/dev/null )
        chmod 644 "$WORK/compose/certs/mender.crt" "$WORK/compose/certs/mender.key"   # the containers read both
    fi
    cd "$WORK"
    echo "[server] docker compose up -d (this pulls ~2-3GB on first run)"
    docker compose up -d
    echo "[server] up. API gateway: https://localhost (self-signed cert, vhost docker.mender.io)"
    echo "[server] next: up.sh user <email> <pass>"
    ;;

  user)
    email="${2:?usage: up.sh user <email> <pass>}"; pass="${3:?password required}"
    _secrets
    cd "$WORK"
    docker compose exec -T useradm useradm create-user --username "$email" --password "$pass"
    echo "[server] admin '$email' created"
    ;;

  token)
    # Mint a Personal Access Token for fleet.py: log in for a JWT, then POST a PAT.
    email="${2:?usage: up.sh token <email> <pass>}"; pass="${3:?password required}"
    base="${MENDER_SERVER:-https://localhost}"
    jwt="$(curl -sk -u "$email:$pass" -X POST "$base/api/management/v1/useradm/auth/login")"
    [ -n "$jwt" ] || { echo "login failed" >&2; exit 1; }
    pat="$(curl -sk -H "Authorization: Bearer $jwt" -H "Content-Type: application/json" \
        -X POST "$base/api/management/v1/useradm/settings/tokens" \
        -d '{"name":"theia-fleet","expires_in":7776000}')"
    # The PAT is the response body (a raw JWT string for v4).
    echo "[server] export MENDER_TOKEN=$(echo "$pat" | tr -d '\"')"
    ;;

  down)
    _secrets; cd "$WORK"; docker compose down ;;

  reset)
    # Wipe the STATE (mongo = deviceauth/inventory/deployments, s3 = artifacts)
    # but KEEP the pulled images — a clean server for a fresh e2e run without the
    # ~2-3GB re-pull. Without this, deviceauth accumulates stale/rejected
    # auth-sets across runs and a re-enrolling device (same MAC identity) gets
    # "dev auth: unauthorized" instead of landing PENDING.
    if [ -d "$WORK/compose" ]; then
      _secrets; cd "$WORK"; docker compose down -v --remove-orphans 2>/dev/null || true
    fi
    docker volume rm mender_mongo mender_s3 2>/dev/null || true
    echo "[server] reset: state volumes wiped (images kept)"
    ;;

  *)
    echo "usage: up.sh {up|user|token|down|reset}" >&2; exit 2 ;;
esac

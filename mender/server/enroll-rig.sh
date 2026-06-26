#!/usr/bin/env bash
# deploy/mender/server/enroll-rig.sh — enrol a rig's mender client to the local
# Mender server (mender/server/up.sh), so it appears in the web UI and can
# pull theia-release OTA. Run from the controller (the dev box); reaches the rig
# over SSH and the server over its API.
#
# What it does (the steps proven on rig1-central → dalek server):
#   1. ship the server's self-signed CA to the rig + trust it
#   2. add the cert vhost (docker.mender.io) → server IP in the rig's /etc/hosts
#   3. write /etc/mender/mender.conf (ServerURL + the CA)
#   4. install minimal identity + inventory scripts (Debian's mender-client ships
#      none) — identity = NIC MAC, inventory = device_type/hostname/IP
#   5. bootstrap + (re)start the client → it submits an auth request (lands pending)
#   6. accept the device via the server's device-auth API → accepted
#   7. restart the client → it authorizes + submits inventory (shows in the UI)
#
# Usage:
#   enroll-rig.sh <rig-ssh> <server-ip> [server-host] [admin-email] [admin-pass]
# e.g.
#   enroll-rig.sh rig1-central 10.0.0.99
#
# This installs the 4.x mender-update client (Debian's 3.4.0 is too old for server
# v4.0.1's deployments API) + seeds a baseline theia-release artifact so the client's
# provides store has a real artifact_name (else the deployments endpoint 400s). Then
# enrols + accepts. Full server→pull→theia-release→UCM is then live. Pass a baseline
# .mender as $4 (BASELINE_ARTIFACT) to seed; default skips seeding.
set -euo pipefail

RIG="${1:?usage: enroll-rig.sh <rig-ssh> <server-ip> [host] [admin-email] [admin-pass]}"
SERVER_IP="${2:?server IP required}"
SERVER_HOST="${3:-docker.mender.io}"
ADMIN_EMAIL="${4:-admin@docker.mender.io}"
ADMIN_PASS="${5:-password123}"
BASE="https://$SERVER_IP"

echo "[enroll] $RIG → $SERVER_HOST ($SERVER_IP)"

# --- 1. fetch the server CA (self-signed cert == its own CA) ---
# Two ways to get it:
#   SERVER_CA=/path/to/mender.crt   — a local CA file (the containerized/CI path,
#                                     where there is no SSH host running the server)
#   SERVER_SSH=<alias>              — ssh into the server host and cat its CA
#                                     (the dev-box/dalek path; default).
SERVER_SSH="${SERVER_SSH:-dalek}"
ca="$(mktemp)"
if [ -n "${SERVER_CA:-}" ] && [ -s "${SERVER_CA}" ]; then
    cp "$SERVER_CA" "$ca"
else
    ssh "$SERVER_SSH" 'cat ~/mender-server/compose/certs/mender.crt' > "$ca"
fi
[ -s "$ca" ] || { echo "could not get server CA (set SERVER_CA=<file> or SERVER_SSH=<host>)"; exit 1; }

# --- 2-4. ship CA + hosts + mender.conf + identity/inventory to the rig ---
scp "$ca" "$RIG:/tmp/mender-server-ca.crt" >/dev/null
ssh "$RIG" "sudo sh -s" <<EOF
set -e
grep -q "$SERVER_HOST" /etc/hosts || echo "$SERVER_IP $SERVER_HOST s3.$SERVER_HOST" >> /etc/hosts
install -d -m0755 /etc/mender
cp /tmp/mender-server-ca.crt /etc/mender/server.crt
cp /tmp/mender-server-ca.crt /usr/local/share/ca-certificates/mender-server.crt
update-ca-certificates >/dev/null 2>&1 || true
cat > /etc/mender/mender.conf <<CONF
{
  "ServerURL": "https://$SERVER_HOST",
  "ServerCertificate": "/etc/mender/server.crt",
  "TenantToken": "",
  "InventoryPollIntervalSeconds": 30,
  "UpdatePollIntervalSeconds": 30,
  "RetryPollIntervalSeconds": 30
}
CONF
install -d -m0755 /usr/share/mender/identity /usr/share/mender/inventory
cat > /usr/share/mender/identity/mender-device-identity <<'ID'
#!/bin/sh
mac=\$(cat /sys/class/net/eth0/address 2>/dev/null || cat /sys/class/net/*/address 2>/dev/null | head -1)
echo "mac=\$mac"
ID
chmod 755 /usr/share/mender/identity/mender-device-identity
cat > /usr/share/mender/inventory/mender-inventory-theia <<'INV'
#!/bin/sh
echo "device_type=theia-rig"
echo "hostname=\$(hostname)"
echo "kernel=\$(uname -r)"
echo "ipv4_eth0=\$(ip -4 -o addr show eth0 2>/dev/null | awk '{print \$4}' | head -1)"
INV
chmod 755 /usr/share/mender/inventory/mender-inventory-theia
# device identity files mender install / auth also need
[ -f /var/lib/mender/device_type ] || { install -d -m0755 /var/lib/mender; echo device_type=theia-rig > /var/lib/mender/device_type; }
[ -f /etc/mender/artifact_info ] || echo artifact_name=unprovisioned > /etc/mender/artifact_info
EOF

# --- 5. install the 4.x client (mender-update + mender-auth) ---
# Debian's mender-client 3.4.0 can't pull from server v4.0.1. Install the 4.x
# stack via Mender's official repo script (publishes +debian+trixie arm64 pkgs).
echo "[enroll] installing the 4.x mender-update client"
ssh "$RIG" 'set -e
  if ! command -v mender-update >/dev/null 2>&1; then
    curl -fsSL https://get.mender.io -o /tmp/get-mender.sh
    sudo bash /tmp/get-mender.sh mender-update mender-auth
  fi
  sudo systemctl disable --now mender-client 2>/dev/null || true   # retire 3.4.0
  sudo install -d -m0755 /var/lib/mender
  echo device_type=theia-rig | sudo tee /var/lib/mender/device_type >/dev/null'

# --- 5b. seed a baseline artifact so the provides store has a real artifact_name ---
# (a blank/unknown artifact_name → the deployments endpoint 400s "cannot be blank")
BASELINE_ARTIFACT="${4:-}"
if [ -n "$BASELINE_ARTIFACT" ] && [ -f "$BASELINE_ARTIFACT" ]; then
    echo "[enroll] seeding baseline artifact $(basename "$BASELINE_ARTIFACT")"
    scp "$BASELINE_ARTIFACT" "$RIG:/tmp/baseline.mender" >/dev/null
    ssh "$RIG" 'sudo install -d -m0755 /opt/theia
                sudo mender-update install /tmp/baseline.mender >/dev/null 2>&1 || true
                sudo mender-update commit >/dev/null 2>&1 || true'
fi

# API helper: hit the server's Management API. Direct curl when we have the CA
# locally (the CI/container path — the API is network-reachable); else proxy the
# call through SSH on the server host (the dev-box/dalek path).
_api() {  # METHOD PATH [DATA]
    local m="$1" p="$2" d="${3:-}"
    if [ -n "${SERVER_CA:-}" ]; then
        curl -sk -X "$m" ${AUTHHDR:+-H "$AUTHHDR"} ${d:+-H 'Content-Type: application/json' -d "$d"} ${BASICAUTH:+-u "$BASICAUTH"} "$BASE$p"
    else
        ssh "$SERVER_SSH" "curl -sk -X '$m' ${AUTHHDR:+-H '$AUTHHDR'} ${d:+-H 'Content-Type: application/json' -d '$d'} ${BASICAUTH:+-u '$BASICAUTH'} '$BASE$p'"
    fi
}

# --- 6. start the 4.x daemons → submit auth request ---
echo "[enroll] starting mender-authd + mender-updated (submits the auth request)"
ssh "$RIG" 'sudo systemctl restart mender-authd; sleep 3; sudo systemctl restart mender-updated'

# --- 7. accept the device via the device-auth API. POLL for it to appear PENDING
#        instead of a fixed sleep (the old `sleep 8` raced the client's auth
#        request — the device wasn't listed yet → "no pending auth set"). ---
BASICAUTH="$ADMIN_EMAIL:$ADMIN_PASS"; AUTHHDR=""
jwt="$(_api POST /api/management/v1/useradm/auth/login)"; jwt="${jwt//\"/}"
[ -n "$jwt" ] || { echo "[enroll] server login failed"; exit 1; }
BASICAUTH=""; AUTHHDR="Authorization: Bearer $jwt"
dev=""; aset=""
for _try in $(seq 1 20); do
    devs="$(_api GET /api/management/v2/devauth/devices)"
    read -r dev aset <<EOF2
$(printf '%s' "$devs" | python3 -c 'import sys,json
try: data=json.load(sys.stdin)
except Exception: data=[]
for d in data:
    for a in d.get("auth_sets",[]):
        if a["status"]=="pending": print(d["id"], a["id"]); break' 2>/dev/null)
EOF2
    [ -n "$dev" ] && break
    sleep 2
done
[ -n "$dev" ] || { echo "[enroll] no pending auth set after polling — check client logs on $RIG"; exit 1; }
echo "[enroll] accepting device $dev (auth set $aset)"
_api PUT "/api/management/v2/devauth/devices/$dev/auth/$aset/status" '{"status":"accepted"}'

# --- 8. restart → authorize + submit inventory + poll for deployments ---
ssh "$RIG" 'sudo systemctl restart mender-authd; sleep 3; sudo systemctl restart mender-updated'
echo "[enroll] done. $RIG is accepted; it reports inventory + polls for deployments."
echo "[enroll] confirm in the UI: $BASE  ($ADMIN_EMAIL)"
echo "[enroll] deploy:  fleet/fleet.py --insecure deploy <artifact> <group>"
rm -f "$ca"

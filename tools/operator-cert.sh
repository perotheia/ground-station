#!/usr/bin/env bash
# tools/operator-cert.sh — an OPERATOR's client certificate for the com clients (supervisor-gui, rtdb,
# theia observer) on a fleet where com serves mutual TLS. The key is generated HERE and never leaves this
# machine; the ground station signs the request (fleet CA, role operator, 30 days).
#   GS=http://<gs>:8088 tools/operator-cert.sh <your-name>        (GS_API_KEY in the env if you call gs-api
#                                                                  directly; gs-web injects it for tailnet members)
# Writes ~/.config/theia-fleet/{ca.crt,operator.crt,operator.key} and prints the environment to use:
#   THEIA_COM_TLS_CA / THEIA_COM_TLS_CLIENT_CERT / THEIA_COM_TLS_CLIENT_KEY   (read by supervisor-gui + rtdb)
# Connect to a rig BY ITS NAME (the certificate names it: e.g. ai64-proto:7700 over the fleet tailnet), or
# set THEIA_COM_TLS_NAME=rig.fleet.theia where the client supports an override (rtdb).
set -euo pipefail
NAME="${1:?usage: operator-cert.sh <operator-name>}"; GS="${GS:?set GS=http://<ground station>:8088}"
D="${THEIA_FLEET_DIR:-$HOME/.config/theia-fleet}"; mkdir -p "$D"; chmod 700 "$D"
[ -f "$D/operator.key" ] || ( umask 077; openssl ecparam -genkey -name prime256v1 -noout -out "$D/operator.key" )
CSR="$(openssl req -new -key "$D/operator.key" -subj "/CN=$NAME")"
BODY="$(python3 -c 'import json,sys; print(json.dumps({"device_id": sys.argv[1], "csr_pem": sys.argv[2], "role": "operator"}))' "$NAME" "$CSR")"
OUT="$(curl -fsS ${GS_API_KEY:+-H "X-GS-Key: $GS_API_KEY"} -H 'Content-Type: application/json' -X POST -d "$BODY" "$GS/api/pki/sign")"
python3 -c 'import json,sys; d=json.loads(sys.argv[1]); open(sys.argv[2]+"/operator.crt","w").write(d["cert_pem"]); open(sys.argv[2]+"/ca.crt","w").write(d["ca_pem"]); print("issued to", d["device_id"], "until", d["not_after"][:10])' "$OUT" "$D"
openssl verify -CAfile "$D/ca.crt" "$D/operator.crt" >/dev/null
cat <<ENV
export THEIA_COM_TLS_CA=$D/ca.crt
export THEIA_COM_TLS_CLIENT_CERT=$D/operator.crt
export THEIA_COM_TLS_CLIENT_KEY=$D/operator.key
ENV

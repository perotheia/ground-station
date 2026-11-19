# Ground station + provisioning server on a fleet host (as installed on panzer, 2026-11-19)

One directory (`~/fleet`), two compose projects (`fleet-mender`, `fleet-gs`), every image pinned, every secret
generated on the host into `~/fleet/secrets` (0700), nothing in git. Remove it all with the last section.

## What runs
| project | services | ports |
|---|---|---|
| `fleet-mender` | upstream Mender server **v4.1.5** as shipped (own traefik v3.7.15, SeaweedFS, mongo, nats), images pinned to the release | 80/443 (gateway, OUR certificate) |
| `fleet-gs` | `gs-api` (FastAPI: devices, deployments, fleet PKI signer, fleet VPN keys), `gs-web` (UI + API proxy), `colony-api` (Ansible provision / orchestrate / cleanup), `gs-s3` (SeaweedFS 4.48 by digest: runtime / swp / roles / distributions / rollouts planes) | `GS_BIND:8088` (the fleet-tailnet address only); S3 only through the gateway's TLS (`https://docker.mender.io/theia-*`) and `127.0.0.1:9000` |

## Prerequisites
Ubuntu 22.04+, Docker 25+ with compose v2, `git`, `openssl`, `curl`; the host joined to the FLEET tailnet as
`tag:fleet-gs` (colony `docs/fleet-vpn.md`); the fleet tailnet API token in `~/.config/colony-fleet/ts_api_token`
(0600); the tailnet policy applied from colony `docs/tailscale-fleet-policy.hujson`.

## Install
```sh
mkdir -p ~/fleet/secrets && chmod 700 ~/fleet ~/fleet/secrets && cd ~/fleet
git clone https://github.com/perotheia/ground-station.git && git clone https://github.com/perotheia/colony.git
TS_IP=<this host's fleet-tailnet IPv4>

# 1. Mender server (clones upstream at the pinned tag, replaces upstream's public demo TLS key pair with
#    our own, generates its S3 + presign secrets)
MENDER_SERVER_DIR=~/fleet/mender-server MENDER_SECRETS_FILE=~/fleet/secrets/mender-server.env \
  FLEET_TLS_NAMES="$TS_IP $(hostname)" ground-station/mender/server/up.sh up
(umask 077; openssl rand -hex 16 > secrets/mender_admin_pass)
MENDER_SERVER_DIR=~/fleet/mender-server MENDER_SECRETS_FILE=~/fleet/secrets/mender-server.env \
  ground-station/mender/server/up.sh user fleet-admin@perotheia.local "$(cat secrets/mender_admin_pass)"
# a personal access token for gs-api (90 days): log in, POST /api/management/v1/useradm/settings/tokens
jwt=$(curl -sk -u "fleet-admin@perotheia.local:$(cat secrets/mender_admin_pass)" -X POST https://localhost/api/management/v1/useradm/auth/login)
(umask 077; curl -sk -H "Authorization: Bearer $jwt" -H 'Content-Type: application/json' -X POST \
   -d '{"name":"gs-api","expires_in":7776000}' https://localhost/api/management/v1/useradm/settings/tokens > secrets/mender_pat)

# 2. secrets + the GS environment
(umask 077; ssh-keygen -q -t ed25519 -N "" -C fleet-gs@$(hostname) -f secrets/fleet_ssh
 for s in s3_secret gs_api_key colony_api_key; do openssl rand -hex 24 > secrets/$s; done
 mkdir -p secrets/fleet-ca secrets/swp-signing theia-ws)
cat > ground-station/.env <<ENV
COMPOSE_PROJECT_NAME=fleet-gs
MENDER_NETWORK=fleet-mender_default
MENDER_SERVER=https://docker.mender.io
MENDER_GATEWAY_IP=$TS_IP
MENDER_CA_PATH=$HOME/fleet/mender-server/compose/certs/mender.crt
MENDER_TOKEN=$(cat secrets/mender_pat)
MENDER_INSECURE=0
MENDER_RIG_IP=$TS_IP
S3_ENDPOINT=http://gs-s3:9000
THEIA_S3_ENDPOINT=http://gs-s3:9000
S3_ACCESS_KEY=theia-gs
S3_SECRET_KEY=$(cat secrets/s3_secret)
GS_API_KEY=$(cat secrets/gs_api_key)
COLONY_API_KEY=$(cat secrets/colony_api_key)
COLONY_DIR=$HOME/fleet/colony
THEIA_WS_DIR=$HOME/fleet/theia-ws
SSH_KEY=$HOME/fleet/secrets/fleet_ssh
TS_API_TOKEN_PATH=$HOME/.config/colony-fleet/ts_api_token
GS_PKI_PATH=$HOME/fleet/secrets/fleet-ca
GS_BIND=$TS_IP
GS_WEB_PORT=8088
GS_COM_AGGREGATOR=<the master rig's tailnet address>:7700
ENV
chmod 600 ground-station/.env

# 3. the GS
cd ground-station && docker compose -f docker-compose.yml up -d --build && tools/s3-init.sh

# 4. the OTA signing key (private key stays here) + its public half in the provisioning plane
cd ~/fleet/secrets/swp-signing && (umask 077; openssl genrsa -out artifact-signing-key.pem 3072)
openssl rsa -in artifact-signing-key.pem -pubout -out artifact-verify-key.pem
# upload artifact-verify-key.pem to s3://theia-runtime/provisioning/artifact-verify-key.pem (boto3 in gs-api or aws cli)
```

## Checks
`curl http://$TS_IP:8088/api/health`; `/api/devices` = 200 through gs-web, 401 straight to gs-api without the
key; `https://docker.mender.io/theia-runtime/` (resolved to `$TS_IP`, `--cacert .../mender.crt`) = 200 and
`/theia-swp/` = 403; no listener on `:9000` or `:8088` outside loopback / the tailnet address.

## Day to day
- **Publish a runtime** (from a theia checkout, e.g. the AI-64: `--config=ai64`):
  `MINIO_USER=theia-gs MINIO_PASSWORD=<s3 secret> theia release services --arch ai64 --version <v> --s3 http://127.0.0.1:9000`
  (on the host, or through `ssh -L`); it never uploads key material.
- **Enroll a rig**: put the GS ssh key (`GET /api/devices/our-pubkey`) on the rig's provisioning user, then
  `POST /api/deployments/base {"rig","kind":"provision","ip","user","role":"master","runtime_version"}`: VPN join
  (per-rig key), Mender client + config, per-device certificate, verify key. Accept it: `POST /api/devices/connect`.
  Then `kind: orchestrate` installs the runtime; com serves mutual TLS with the device certificate.
- **Operator certificate** (supervisor-gui, rtdb): `GS=http://$TS_IP:8088 tools/operator-cert.sh <name>`.
- **OTA**: build + sign a `theia-swp` artifact with the signing key, upload, deploy (docs/ota-campaign-2026-11-19.md).
- Rig classes: Debian rigs get everything; opkg rigs (TI Arago) are base plane only: no Mender client (the route
  is meta-mender in a TI Yocto build), reported as named skips.

## Remove
```sh
cd ~/fleet/ground-station && docker compose -f docker-compose.yml down -v
cd ~/fleet/mender-server && set -a && . ~/fleet/secrets/mender-server.env && set +a && \
  COMPOSE_PROJECT_NAME=fleet-mender MENDER_IMAGE_TAG=v4.1.5 docker compose down -v
rm -rf ~/fleet        # includes the fleet CA and the signing key: rigs must be re-enrolled after this
```

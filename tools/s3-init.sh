#!/usr/bin/env bash
# tools/s3-init.sh — create the GS's S3 planes (buckets) once, from inside gs-api (it has boto3 + the key).
# Idempotent. Run after `docker compose up -d`.
set -euo pipefail
docker exec -i gs-api python - <<'P'
import os, boto3
from botocore.config import Config
s3 = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT"], aws_access_key_id=os.environ["MINIO_USER"],
                  aws_secret_access_key=os.environ["MINIO_PASSWORD"], config=Config(signature_version="s3v4"),
                  region_name="us-east-1")
have = {b["Name"] for b in s3.list_buckets()["Buckets"]}
for b in ("theia-runtime", "theia-swp", "theia-roles", "theia-distributions", "theia-rollouts"):
    if b not in have:
        s3.create_bucket(Bucket=b)
print("buckets:", sorted(b["Name"] for b in s3.list_buckets()["Buckets"]))
P

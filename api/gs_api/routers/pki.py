"""The fleet PKI signer (hardening C2/C3, design/pki-design): ONE fleet CA on the ground station; every rig
generates its own key and sends a CSR through colony's ssh path; this signs it. No private key but the CA's
ever exists here, and the CA key never leaves GS_PKI_DIR (a 0700 host directory mounted into gs-api only).

  POST /api/pki/sign   {device_id, csr_pem, role: "rig"|"gs"}  -> {cert_pem, ca_pem, not_after, serial}
  GET  /api/pki/ca                                             -> {ca_pem}
  GET  /api/pki/issued                                         -> the issue log (serial, device, role, dates)

The certificate: CN = device_id, O = "perotheia fleet", OU = role, SAN = URI theia:<role>:<device_id> + DNS
device_id, EKU clientAuth + serverAuth, 90 days (GS_PKI_DAYS). Every route is behind the API key (app gate).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/pki", tags=["pki"])

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
_ROLES = ("rig", "gs")


def _dir() -> Path:
    d = os.environ.get("GS_PKI_DIR", "")
    if not d:
        raise HTTPException(status_code=503, detail="fleet PKI not configured (GS_PKI_DIR)")
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _load_ca():
    """Load the fleet CA; create it on first use (ECDSA P-256, 10 years)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    d = _dir()
    kf, cf = d / "ca.key", d / "ca.crt"
    if not kf.exists():
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "perotheia fleet CA"),
                          x509.NameAttribute(NameOID.ORGANIZATION_NAME, "perotheia fleet")])
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
                .not_valid_after(now + dt.timedelta(days=3650))
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                             content_commitment=False, key_encipherment=False,
                                             data_encipherment=False, key_agreement=False,
                                             encipher_only=False, decipher_only=False), critical=True)
                .sign(key, hashes.SHA256()))
        fd = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
        cf.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key = serialization.load_pem_private_key(kf.read_bytes(), password=None)
    cert = x509.load_pem_x509_certificate(cf.read_bytes())
    return key, cert


class SignRequest(BaseModel):
    model_config = {"extra": "forbid"}
    device_id: str
    csr_pem: str
    role: str = "rig"


@router.get("/ca")
def ca() -> dict:
    from cryptography.hazmat.primitives import serialization
    _, cert = _load_ca()
    return {"ca_pem": cert.public_bytes(serialization.Encoding.PEM).decode()}


@router.get("/issued")
def issued() -> dict:
    f = _dir() / "issued.jsonl"
    rows = [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.exists() else []
    return {"issued": rows[-500:], "count": len(rows)}


@router.post("/sign")
def sign(req: SignRequest) -> dict:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    if not _ID_RE.match(req.device_id):
        raise HTTPException(status_code=422, detail="device_id: letters, digits, . _ - (max 63)")
    if req.role not in _ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of {_ROLES}")
    try:
        csr = x509.load_pem_x509_csr(req.csr_pem.encode())
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=422, detail="csr_pem: not a PEM certificate request")
    if not csr.is_signature_valid:
        raise HTTPException(status_code=422, detail="csr: signature invalid (no proof of the private key)")
    cns = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if len(cns) != 1 or cns[0].value != req.device_id:
        raise HTTPException(status_code=422, detail="csr: CN must equal device_id")
    pub = csr.public_key()
    if isinstance(pub, ec.EllipticCurvePublicKey):
        if pub.curve.key_size < 256:
            raise HTTPException(status_code=422, detail="csr: EC key below 256 bits")
    elif isinstance(pub, rsa.RSAPublicKey):
        if pub.key_size < 3072:
            raise HTTPException(status_code=422, detail="csr: RSA key below 3072 bits")
    else:
        raise HTTPException(status_code=422, detail="csr: EC P-256+ or RSA 3072+ keys only")
    ca_key, ca_cert = _load_ca()
    now = dt.datetime.now(dt.timezone.utc)
    days = int(os.environ.get("GS_PKI_DAYS", "90"))
    # the subject is OURS (never copied from the CSR): CN, O, OU = role
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, req.device_id),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "perotheia fleet"),
                         x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, req.role)])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(ca_cert.subject).public_key(pub)
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=days))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False, key_cert_sign=False,
                                         crl_sign=False, content_commitment=False, data_encipherment=False,
                                         key_agreement=True, encipher_only=False, decipher_only=False),
                           critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH,
                                                  ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectAlternativeName([
                x509.UniformResourceIdentifier(f"theia:{req.role}:{req.device_id}"),
                x509.DNSName(req.device_id)]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                           critical=False)
            .sign(ca_key, hashes.SHA256()))
    row = {"serial": format(cert.serial_number, "x"), "device_id": req.device_id, "role": req.role,
           "not_before": cert.not_valid_before_utc.isoformat(), "not_after": cert.not_valid_after_utc.isoformat()}
    with open(_dir() / "issued.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return {"cert_pem": cert.public_bytes(serialization.Encoding.PEM).decode(),
            "ca_pem": ca_cert.public_bytes(serialization.Encoding.PEM).decode(), **row}

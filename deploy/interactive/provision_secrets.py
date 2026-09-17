"""Offline, once-only secret provisioning. Never prints credential values."""
import argparse
import json
import os
from pathlib import Path
import secrets

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

parser = argparse.ArgumentParser()
parser.add_argument("--directory", type=Path, required=True)
parser.add_argument("--kid", default="primary")
args = parser.parse_args()
if not 1 <= len(args.kid) <= 64:
    raise SystemExit("Invalid signing key ID")
directory = args.directory.resolve()
directory.mkdir(parents=True, mode=0o750, exist_ok=True)
expected = ("controller", "gateway", "bootstrap", "encryption", "signing.pem", "public.json")
if any((directory / name).exists() for name in expected):
    raise SystemExit("Existing material present; provisioning never overwrites stable credentials")
private = Ed25519PrivateKey.generate()
values = {name: secrets.token_urlsafe(40) for name in ("controller", "gateway", "bootstrap")}
values["encryption"] = Fernet.generate_key().decode()
values["signing.pem"] = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
public = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
values["public.json"] = json.dumps({args.kid: {"pem": public}}, indent=2)
for name, value in values.items():
    path = directory / name
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(value + "\n")
    if os.geteuid() == 0:
        os.chown(path, 0, 10001)
if os.geteuid() == 0:
    os.chown(directory, 0, 10001)
print("Stable role, encryption and signing files created; provision the Headscale administrative key separately")

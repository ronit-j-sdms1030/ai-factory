"""Local HMAC and cosign CLI attestation signers."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import attestation


class LocalHMACSigner:
    def __init__(self, secret: bytes, *, keyid: str = "phase1-dev"):
        self.secret = secret
        self.keyid = keyid

    def sign(self, statement: dict) -> dict:
        return attestation.sign(statement, self.secret, keyid=self.keyid)

    def verify(self, envelope: dict) -> dict:
        return attestation.verify(envelope, self.secret)


class CosignCLISigner:
    def __init__(
        self,
        *,
        key: str = "",
        certificate_identity: str = "",
        certificate_oidc_issuer: str = "",
        executable: str = "cosign",
    ):
        self.key = key
        self.identity = certificate_identity
        self.issuer = certificate_oidc_issuer
        self.executable = executable

    def sign(self, statement: dict) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory) / "statement.json"
            bundle = Path(directory) / "bundle.json"
            payload.write_text(attestation.canonical(statement), encoding="utf-8")
            command = [
                self.executable,
                "sign-blob",
                "--yes",
                "--bundle",
                str(bundle),
            ]
            if self.key:
                command.extend(["--key", self.key])
            command.append(str(payload))
            subprocess.run(command, check=True, capture_output=True, text=True)
            return {
                "payloadType": attestation.STATEMENT_TYPE,
                "payload": statement,
                "cosignBundle": json.loads(bundle.read_text(encoding="utf-8")),
            }

    def verify(self, envelope: dict) -> dict:
        statement = envelope.get("payload")
        bundle_data = envelope.get("cosignBundle")
        if not isinstance(statement, dict) or not isinstance(bundle_data, dict):
            raise attestation.SignatureMismatch("not a cosign attestation envelope")
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory) / "statement.json"
            bundle = Path(directory) / "bundle.json"
            payload.write_text(attestation.canonical(statement), encoding="utf-8")
            bundle.write_text(json.dumps(bundle_data), encoding="utf-8")
            command = [
                self.executable,
                "verify-blob",
                "--bundle",
                str(bundle),
            ]
            if self.key:
                command.extend(["--key", self.key])
            else:
                command.extend(
                    [
                        "--certificate-identity",
                        self.identity,
                        "--certificate-oidc-issuer",
                        self.issuer,
                    ]
                )
            command.append(str(payload))
            subprocess.run(command, check=True, capture_output=True, text=True)
        return statement

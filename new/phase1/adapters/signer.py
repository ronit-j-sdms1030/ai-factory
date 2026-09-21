"""Local HMAC and cosign CLI attestation signers."""

from __future__ import annotations

import json
import os
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
        public_key: str = "",
        certificate_identity: str = "",
        certificate_oidc_issuer: str = "",
        executable: str = "cosign",
    ):
        self.key = key
        self.public_key = public_key
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
                "--tlog-upload=false",
                "--bundle",
                str(bundle),
            ]
            if self.key:
                command.extend(["--key", self.key])
            command.append(str(payload))
            self._run(command)
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
                command.extend(
                    [
                        "--key",
                        self.public_key or self.key,
                        "--insecure-ignore-tlog",
                    ]
                )
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
            self._run(command)
        return statement

    def _run(self, command: list[str]) -> None:
        env = os.environ.copy()
        env.setdefault("COSIGN_PASSWORD", "")
        env.setdefault("COSIGN_YES", "1")
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "cosign failed").strip()
            raise RuntimeError(detail)

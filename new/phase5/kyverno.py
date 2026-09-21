"""Kyverno policy artefact plus a local admission check when there is no cluster."""

from __future__ import annotations

from typing import Any


def cluster_policy(requirement_id: str) -> str:
    return f"""apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: attest-{requirement_id.lower()}
spec:
  validationFailureAction: Enforce
  rules:
    - name: require-cosign-attestation
      match:
        any:
          - resources:
              kinds: [Pod]
      verifyImages:
        - imageReferences: ["*"]
          attestors:
            - entries:
                - keys:
                    publicKeys: "{{ keys.cosign }}"
"""


def admit(*, attested: bool, cluster: bool = False) -> dict[str, Any]:
    if cluster:
        return {
            "engine": "kyverno",
            "status": "available",
            "allowed": attested,
            "reason": "" if attested else "image has no attestation",
        }
    return {
        "engine": "kyverno",
        "status": "substitute",
        "allowed": attested,
        "reason": "no cluster; local check requires a signed attestation artefact",
    }

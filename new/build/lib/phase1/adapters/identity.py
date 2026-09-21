"""Development-directory and OIDC identity adapters."""

from __future__ import annotations

from typing import Any

import gate_engine
import workflow_templates
from phase1 import directory


class DevDirectoryIdentity:
    def actor(self, actor_id: str) -> dict[str, Any]:
        return directory.actor(actor_id)

    def freeze_chain(self, template: str) -> dict[str, list[dict[str, Any]]]:
        return directory.freeze_chain(template)

    def validate_token(self, token: str) -> dict[str, Any]:
        return self.actor(token)


class OIDCIdentity:
    def __init__(
        self,
        issuer: str,
        audience: str,
        jwks_url: str,
        *,
        role_claim: str = "roles",
        team_claim: str = "team",
        algorithms: tuple[str, ...] = ("RS256",),
    ):
        try:
            import jwt
        except ImportError as exc:
            raise RuntimeError("OIDC adapter requires the 'identity' extra") from exc
        self.jwt = jwt
        self.issuer = issuer
        self.audience = audience
        self.role_claim = role_claim
        self.team_claim = team_claim
        self.algorithms = algorithms
        self.jwks = jwt.PyJWKClient(jwks_url)

    def validate_token(self, token: str) -> dict[str, Any]:
        key = self.jwks.get_signing_key_from_jwt(token)
        claims = self.jwt.decode(
            token,
            key.key,
            algorithms=list(self.algorithms),
            audience=self.audience,
            issuer=self.issuer,
        )
        subject = str(claims["sub"])
        roles = claims.get(self.role_claim) or []
        if isinstance(roles, str):
            roles = [roles]
        return {
            "id": subject,
            "name": claims.get("name") or subject,
            "email": claims.get("email") or claims.get("preferred_username") or "",
            "roles": list(roles),
            "team": claims.get(self.team_claim) or "",
        }

    def actor(self, actor_id: str) -> dict[str, Any]:
        raise KeyError(f"OIDC directory lookup is not configured for {actor_id}")

    def freeze_chain(self, template: str) -> dict[str, list[dict[str, Any]]]:
        gates = workflow_templates.for_name(template).gates
        roles = gate_engine.resolve_approval_chain({}, list(gates))
        return {
            gate: [{"role": role, "unresolved": True} for role in required]
            for gate, required in roles.items()
        }

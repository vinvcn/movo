"""Short-lived, scoped authentication for DSH-to-ASKAI model requests."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelGatewayClaims:
    tenant_id: str
    profile_version: str
    model_instance_id: str
    issued_at: int
    expires_at: int
    user_id: str = ""
    # Set only for digital-employee sessions; empty means a personal session.
    # Cost attribution reads this to stamp usage rows per employee
    # (docs/digital-employee.md, Cost attribution).
    digital_employee_id: str = ""


class ModelGatewayTokenService:
    def __init__(self, secret: str, *, ttl_seconds: int = 900) -> None:
        if len(secret) < 16:
            raise ValueError("Model Gateway token secret must contain at least 16 characters")
        if ttl_seconds < 30:
            raise ValueError("Model Gateway token TTL must be at least 30 seconds")
        self._key = hashlib.sha256(secret.encode("utf-8")).digest()
        self._ttl_seconds = ttl_seconds

    def issue(
        self,
        *,
        tenant_id: str,
        profile_version: str,
        model_instance_id: str,
        user_id: str = "",
        digital_employee_id: str = "",
    ) -> str:
        now = int(time.time())
        payload = {
            "aud": "askai-model-gateway",
            "tenant_id": tenant_id,
            "profile_version": profile_version,
            "model_instance_id": model_instance_id,
            "user_id": user_id,
            "digital_employee_id": digital_employee_id,
            "iat": now,
            "exp": now + self._ttl_seconds,
            "jti": secrets.token_hex(8),
        }
        encoded = self._encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        signature = self._encode(hmac.new(self._key, encoded.encode("ascii"), hashlib.sha256).digest())
        return f"{encoded}.{signature}"

    def verify(self, token: str) -> ModelGatewayClaims:
        try:
            encoded, signature = token.split(".", 1)
            expected = self._encode(hmac.new(self._key, encoded.encode("ascii"), hashlib.sha256).digest())
            if not hmac.compare_digest(expected, signature):
                raise ValueError("Model Gateway token signature is invalid")
            payload = json.loads(self._decode(encoded))
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Model Gateway token is invalid") from exc
        if payload.get("aud") != "askai-model-gateway":
            raise ValueError("Model Gateway token audience is invalid")
        now = int(time.time())
        if int(payload.get("exp") or 0) <= now:
            raise ValueError("Model Gateway token has expired")
        return ModelGatewayClaims(
            tenant_id=str(payload["tenant_id"]),
            profile_version=str(payload["profile_version"]),
            model_instance_id=str(payload["model_instance_id"]),
            issued_at=int(payload["iat"]),
            expires_at=int(payload["exp"]),
            user_id=str(payload.get("user_id") or ""),
            # Tokens minted before the employee claim existed carry no
            # digital_employee_id key; they verify as personal sessions.
            digital_employee_id=str(payload.get("digital_employee_id") or ""),
        )

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode(value: str) -> str:
        padded = value + "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")

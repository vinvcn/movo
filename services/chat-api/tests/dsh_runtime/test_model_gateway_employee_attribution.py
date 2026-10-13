"""Digital-employee cost attribution (ticket #45, docs/digital-employee.md).

The attribution chain, tested end to end at the unit seams:

- Token roundtrip: ``issue`` embeds ``digital_employee_id`` and ``verify``
  reads it; tokens minted before the claim existed still verify as "".
- Resolver: the model-gateway token minted from the runtime-profile snapshot
  carries the snapshot's employee id (``snapshot.digital_employee``).
- Gateway service: claims thread into the client factory and the usage
  ``output_spec``; ``_submit_record`` lands them on ``TokenUsageRecord``.

Per-user attribution stays unchanged: ``user_id`` keeps flowing beside the
new employee claim everywhere.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Any

import pytest

from app.dsh_runtime.model_gateway import service as model_gateway_service
from app.dsh_runtime.model_gateway.service import ModelGatewayRequest, ModelGatewayService
from app.dsh_runtime.model_gateway.token import ModelGatewayTokenService
from app.dsh_runtime.profile import InMemoryRuntimeProfileStore, ModelProfileCompiler, RuntimeProfileResolver
from app.llm.base import BaseLLMClient
from app.llm.instrumented_client import InstrumentedLLMClient
from app.llm.types import LLMResponse, Message, Role
from app.token_usage.models import TokenUsageRecord

SECRET = "employee-attribution-signing-secret"


def test_token_roundtrip_carries_digital_employee_id() -> None:
    tokens = ModelGatewayTokenService(SECRET)

    claims = tokens.verify(tokens.issue(
        tenant_id="tenant-a",
        profile_version="profile-a",
        model_instance_id="model-a",
        user_id="speaker-a",
        digital_employee_id="emp-1",
    ))
    assert claims.digital_employee_id == "emp-1"
    assert claims.user_id == "speaker-a"

    # Omitting the claim is still valid: a personal session.
    plain = tokens.verify(tokens.issue(
        tenant_id="tenant-a",
        profile_version="profile-a",
        model_instance_id="model-a",
        user_id="speaker-a",
    ))
    assert plain.digital_employee_id == ""


def test_token_minted_before_the_employee_claim_still_verifies() -> None:
    """A token signed by the previous payload shape (no digital_employee_id
    key) must keep verifying and read back as a personal session."""
    tokens = ModelGatewayTokenService(SECRET)
    now = int(time.time())
    legacy_payload = {
        "aud": "askai-model-gateway",
        "tenant_id": "tenant-a",
        "profile_version": "profile-a",
        "model_instance_id": "model-a",
        "user_id": "speaker-a",
        "iat": now,
        "exp": now + tokens._ttl_seconds,
        "jti": "legacy-token-1",
    }
    encoded = tokens._encode(json.dumps(legacy_payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = tokens._encode(hmac.new(tokens._key, encoded.encode("ascii"), hashlib.sha256).digest())
    legacy_token = f"{encoded}.{signature}"

    claims = tokens.verify(legacy_token)
    assert claims.tenant_id == "tenant-a"
    assert claims.user_id == "speaker-a"
    assert claims.digital_employee_id == ""


class FakeModelCatalog:
    async def resolve(self, tenant_id: str, model_instance_id: str | None):
        return (
            {
                "_id": "model-a",
                "main_id": tenant_id,
                "provider_id": "provider-a",
                "model_name": "deepseek-chat",
                "display_name": "Default A",
                "status": "active",
                "capabilities": ["chat"],
                "max_context_tokens": 64_000,
                "settings": {"max_output_tokens": 4096},
            },
            {
                "_id": "provider-a",
                "name": "ASKAI managed provider",
                "provider_type": "openai_compatible",
                "status": "active",
            },
        )


class FakeEmployeeCatalog:
    """Mirrors tests/dsh_runtime/test_digital_employee_session.py."""

    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self.rows = rows

    async def load_for_profile(self, tenant_id: str, employee_id: str) -> dict[str, Any]:
        return self.rows[employee_id]


def test_resolver_mints_employee_claim_from_snapshot() -> None:
    asyncio.run(_test_resolver_mints_employee_claim())


async def _test_resolver_mints_employee_claim() -> None:
    rows = {"emp-1": {"_id": "emp-1", "name": "小礼", "status": "active", "manifesto": "你是礼物助手。"}}
    compiler = ModelProfileCompiler(FakeModelCatalog(), employee_catalog=FakeEmployeeCatalog(rows))
    store = InMemoryRuntimeProfileStore()
    tokens = ModelGatewayTokenService(SECRET)
    resolver = RuntimeProfileResolver(store, tokens, gateway_url="http://127.0.0.1/internal/dsh/model/generate")

    plain = await compiler.compile(tenant_id="tenant-a", user_id="speaker-a")
    await store.publish(plain, actor_id="admin")
    payload = await resolver.resolve(plain.profile_version, tenant_id="tenant-a")
    assert tokens.verify(str(payload["accessToken"])).digital_employee_id == ""

    bound = await compiler.compile(tenant_id="tenant-a", user_id="speaker-a", digital_employee_id="emp-1")
    await store.publish(bound, actor_id="admin")
    payload = await resolver.resolve(bound.profile_version, tenant_id="tenant-a")
    claims = tokens.verify(str(payload["accessToken"]))
    assert claims.digital_employee_id == "emp-1"
    assert claims.user_id == "speaker-a"


class FakeClient(BaseLLMClient):
    async def ainvoke(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        return LLMResponse(
            message=Message(role=Role.ASSISTANT, content="answer"),
            usage={"prompt_tokens": 11, "completion_tokens": 7},
        )

    async def astream(self, messages: list[Message], **kwargs: Any):
        yield LLMResponse(message=Message(role=Role.ASSISTANT, content="answer"))

    async def ainvoke_structured(self, messages: list[Message], schema: Any, **kwargs: Any):
        raise NotImplementedError


def test_gateway_threads_employee_claim_into_output_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def get_config(model_id: str, tenant_id: str) -> dict[str, Any]:
        return {"id": model_id}

    def build_client(_config: dict[str, Any], **kwargs: Any) -> BaseLLMClient:
        captured.update(kwargs["output_spec"])
        return FakeClient()

    monkeypatch.setattr(model_gateway_service, "get_model_config", get_config)
    monkeypatch.setattr(model_gateway_service, "build_llm_client_from_config", build_client)

    tokens = ModelGatewayTokenService(SECRET)
    claims = tokens.verify(tokens.issue(
        tenant_id="tenant-a",
        profile_version="profile-a",
        model_instance_id="model-a",
        user_id="speaker-a",
        digital_employee_id="emp-1",
    ))
    request = ModelGatewayRequest(
        profileVersion="profile-a",
        modelInstanceId="model-a",
        provider="askai-model-gateway",
        model="deepseek-chat",
        messages=[{"role": "user", "content": "hello"}],
        sessionId="kernel-a",
    )
    asyncio.run(ModelGatewayService().generate(request, claims))

    # Per-user attribution is untouched; the employee id rides along.
    assert captured["user_id"] == "speaker-a"
    assert captured["main_id"] == "tenant-a"
    assert captured["digital_employee_id"] == "emp-1"


def test_token_usage_record_defaults_and_submit_record_population(monkeypatch: pytest.MonkeyPatch) -> None:
    # Default: a personal session carries an empty employee id, and the
    # persisted row includes the field (the per-employee index is partial
    # over non-empty values).
    record = TokenUsageRecord(request_id="llm_x")
    assert record.digital_employee_id == ""
    assert "digital_employee_id" in record.model_dump()

    from app.infrastructure.runtime_services import token_usage_dispatcher

    submitted: list[TokenUsageRecord] = []
    monkeypatch.setattr(token_usage_dispatcher, "submit", lambda record: submitted.append(record) or True)

    async def drive(output_spec: dict[str, Any]) -> None:
        client = InstrumentedLLMClient(
            FakeClient(),
            model_name="deepseek-chat",
            model_id="model-a",
            stage="dsh_agent_turn",
            intent="chat",
            output_spec=output_spec,
        )
        await client.ainvoke([Message(role=Role.USER, content="hello")])

    asyncio.run(drive({
        "main_id": "tenant-a",
        "user_id": "speaker-a",
        "digital_employee_id": "emp-1",
        "session_id": "kernel-a",
    }))
    asyncio.run(drive({"main_id": "tenant-a", "user_id": "speaker-b", "session_id": "kernel-b"}))

    assert [row.digital_employee_id for row in submitted] == ["emp-1", ""]
    assert [row.user_id for row in submitted] == ["speaker-a", "speaker-b"]

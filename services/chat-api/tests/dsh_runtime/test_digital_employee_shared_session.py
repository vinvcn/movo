"""Shared employee sessions: one employee, one bundle, every speaker (#44).

The semantic under test (docs/digital-employee.md, "Shared employee
sessions"): the employee binding belongs to the CONVERSATION, not to the
speaker. A participant who was never assigned the employee joins and
speaks, and both speakers compile the same employee segment and the same
exact bound bundle - while the profile still rotates with the speaker's
identity, and each speaker's own personal grants never leak into the
bundle.

Harness mirrors ``test_session_sharing_capabilities.py``: real Mongo,
real profile stack (compiler, publisher, store, synchronizer), real
coordinator with an in-memory gateway fake.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from bson import ObjectId

import app.dsh_runtime.chat_service as chat_service_module
import app.dsh_runtime.profile.catalog as profile_catalog_module
import app.dsh_runtime.profile.store as profile_store_module
import app.services.external_tools as external_tools_module
import app.services.org_skill_adapter as org_skill_adapter_module
import app.services.skills as skills_module

from app.dsh_runtime.bindings.repository import KernelBindingRepository
from app.dsh_runtime.chat_service import DshChatService
from app.dsh_runtime.conversation import ConversationRepository
from app.dsh_runtime.conversation.participants_repository import (
    SessionParticipantsRepository,
)
from app.dsh_runtime.contracts import CreateRuntimeRequest, CreateSessionRequest
from app.dsh_runtime.events import KernelEventRepository
from app.dsh_runtime.profile.catalog import MongoModelCatalog
from app.dsh_runtime.profile.compiler import ModelProfileCompiler, MongoEmployeeCatalog
from app.dsh_runtime.profile.service import RuntimeProfilePublisher
from app.dsh_runtime.profile.skills.catalog import MongoSkillCatalog
from app.dsh_runtime.profile.skills.compiler import SkillProfileCompiler
from app.dsh_runtime.profile.store import MongoRuntimeProfileStore
from app.dsh_runtime.profile.tools import MongoToolCatalog, ToolProfileCompiler
from app.dsh_runtime.runtime_coordinator import RuntimeCoordinator

TENANT = "tenant-employee-shared"
EMPLOYEE_ID = "emp-xiaoli"
ORG_SKILL_ID = "skill-gift-org"
ORG_TOOL_ID = "tool-gift-org"


class _FakeRuntime:
    def __init__(self, runtime_id: str) -> None:
        self.runtime_id = runtime_id
        self.kernel_version = "test-kernel"


class _FakeGateway:
    """In-memory gateway fake mirroring test_session_sharing_capabilities."""

    def __init__(self) -> None:
        self.created_sessions: list[tuple[CreateSessionRequest, str]] = []
        self.disposed_sessions: list[str] = []
        self._runtimes: dict[str, _FakeRuntime] = {}

    async def discover_runtime(self, *, tenant_id: str, profile_version: str, isolation_key: str):
        return self._runtimes.get(isolation_key)

    async def create_runtime(self, request: CreateRuntimeRequest):
        runtime = _FakeRuntime(f"rt-{len(self._runtimes) + 1}")
        self._runtimes[request.isolation_key] = runtime
        return runtime

    async def create_session(self, request: CreateSessionRequest):
        session_id = f"ks-{len(self.created_sessions) + 1}"
        self.created_sessions.append((request, session_id))
        return SimpleNamespace(session_id=session_id)

    def attach_session(self, **kwargs: Any) -> None:
        return None

    async def resume_session(self, session_id: str) -> None:
        return None

    async def dispose_session(self, session_id: str) -> None:
        self.disposed_sessions.append(str(session_id))

    async def cancel(self, request) -> dict[str, Any]:
        return {"turnPending": False, "jobsPending": False}

    async def events_once(self, session_id: str, cursor: int) -> list[dict[str, Any]]:
        return []


@pytest.fixture
def shared_employee_env(real_mongo_db, monkeypatch):
    harness = real_mongo_db
    harness.run(SessionParticipantsRepository(harness.db).ensure_indexes())
    harness.run(KernelBindingRepository(harness.db).ensure_indexes())
    harness.run(ConversationRepository(harness.db).ensure_indexes())
    monkeypatch.setattr(profile_catalog_module, "get_db", lambda: harness.db)
    monkeypatch.setattr(profile_store_module, "get_db", lambda: harness.db)
    monkeypatch.setattr(skills_module, "get_db", lambda: harness.db)
    monkeypatch.setattr(external_tools_module, "get_db", lambda: harness.db)
    monkeypatch.setattr(org_skill_adapter_module, "get_db", lambda: harness.db)
    monkeypatch.setattr(chat_service_module, "get_db", lambda: harness.db, raising=False)

    now = datetime.utcnow()
    provider_oid = ObjectId()
    harness.run(
        harness.db.admin_model_providers.insert_one(
            {
                "_id": provider_oid,
                "main_id": TENANT,
                "status": "active",
                "provider_type": "openai_compatible",
                "name": "Provider",
            }
        )
    )
    harness.run(
        harness.db.admin_model_instances.insert_one(
            {
                "_id": ObjectId(),
                "main_id": TENANT,
                "status": "active",
                "capabilities": ["chat"],
                "model_name": "model-a",
                "display_name": "Model A",
                "provider_id": provider_oid,
                "priority": 1,
                "max_context_tokens": 8192,
                "settings": {},
                "updated_at": now,
            }
        )
    )
    # The employee is deliberately NOT public and has no assignments: the
    # test creates the bound conversation directly, proving that SHARING is
    # governed by session membership alone, never by employee availability.
    harness.run(
        harness.db.digital_employees.insert_one(
            {
                "_id": EMPLOYEE_ID,
                "main_id": TENANT,
                "name": "小礼",
                "description": "礼品协作者",
                "manifesto": "你是礼品协作者，只聊礼品。",
                "status": "active",
                "is_public": False,
                "skill_ids": [ORG_SKILL_ID],
                "tool_bindings": [{"tool_id": ORG_TOOL_ID, "enabled_tool_names": []}],
            }
        )
    )
    harness.run(
        harness.db.skills.insert_one(
            {
                "_id": ORG_SKILL_ID,
                "main_id": TENANT,
                "name": "礼品话术",
                "description": "礼品沟通话术",
                "skill_markdown": "# 礼品话术\n按场合推荐礼品。",
                "type": "ordinary",
                "enabled": True,
                "updated_at": now,
            }
        )
    )
    harness.run(
        harness.db.external_tools.insert_one(
            {
                "_id": ORG_TOOL_ID,
                "scope": "organization",
                "main_id": TENANT,
                "name": "Gift tools",
                "type": "http",
                "description": "Organization gift tools",
                "config": {"method": "GET"},
                "status": "active",
            }
        )
    )
    ids = {name: str(ObjectId()) for name in ("owner", "participant")}
    for name, user_id in ids.items():
        harness.run(
            harness.db.end_users.insert_one(
                {"_id": ObjectId(user_id), "main_id": TENANT, "name": f"User {name}", "status": "active"}
            )
        )        # Personal grants that must NEVER leak into the employee bundle.
        harness.run(
            harness.db.user_skills.insert_one(
                {
                    "_id": f"personal-skill-{name}",
                    "user_id": user_id,
                    "main_id": TENANT,
                    "name": f"Personal skill {name}",
                    "skill_markdown": f"# Personal {name}\nPersonal only.",
                    "enabled": True,
                }
            )
        )
        harness.run(
            harness.db.external_tools.insert_one(
                {
                    "_id": f"personal-tool-{name}",
                    "scope": "user",
                    "owner_user_id": user_id,
                    "main_id": TENANT,
                    "name": f"Personal tool {name}",
                    "type": "http",
                    "description": "Personal only",
                    "config": {"method": "GET"},
                    "status": "active",
                }
            )
        )

    # A conversation already bound to the employee (created by the owner).
    session_id = str(
        harness.run(
            ConversationRepository(harness.db).create(
                tenant_id=TENANT, user_id=ids["owner"], title="员工共享会话",
                digital_employee_id=EMPLOYEE_ID,
            )
        )["_id"]
    )

    gateway = _FakeGateway()
    compiler = ModelProfileCompiler(
        MongoModelCatalog(),
        ToolProfileCompiler(MongoToolCatalog()),
        SkillProfileCompiler(MongoSkillCatalog()),
        employee_catalog=MongoEmployeeCatalog(harness.db),
    )
    profiles = RuntimeProfilePublisher(compiler, MongoRuntimeProfileStore())
    chat = DshChatService(
        gateway=gateway,
        coordinator=RuntimeCoordinator(gateway, KernelBindingRepository(harness.db)),
        conversations=ConversationRepository(harness.db),
        bindings=KernelBindingRepository(harness.db),
        events=KernelEventRepository(harness.db),
        profiles=profiles,
        kernel_version="test-kernel",
    )

    # The turn runner is replaced (as in test_session_sharing_capabilities):
    # the paths under test are admission, compile, publish and rotation, not
    # the DSH turn execution itself.
    async def _runner(*args: Any, binding: dict[str, Any] | None = None, message_id: str | None = None, **kwargs: Any) -> str:
        await chat._finalizer.finalize(binding=binding, message_id=message_id, status="completed")
        return "completed"

    chat._turn_runner.run = _runner
    return SimpleNamespace(
        harness=harness, chat=chat, ids=ids, session_id=session_id, gateway=gateway,
    )


def _send(env, *, user_id: str):
    turn = env.harness.run(
        env.chat.prepare_turn(
            tenant_id=TENANT,
            user_id=user_id,
            conversation_id=env.session_id,
            text="turn",
            model_instance_id=None,
            timezone_name="UTC",
            images=[],
            documents=[],
        )
    )
    outcome = env.harness.run(env.chat.wait_turn(turn.message_id))
    return turn, outcome


def _profile_version_for(env, user_id: str) -> str:
    # Rotation unsets "current" on the predecessor row, so resolve by speaker:
    # each speaker's own binding row carries the profile version their turn
    # compiled and ran under.
    async def _one():
        row = await env.harness.db.agent_kernel_bindings.find_one(
            {"conversation_id": env.session_id, "user_id": user_id}
        )
        return str((row or {}).get("profile_version") or "")

    return env.harness.run(_one())


def _snapshot(env, profile_version: str):
    return env.harness.run(MongoRuntimeProfileStore().get(profile_version))


def test_unassigned_participant_speaks_and_shares_the_same_bundle(shared_employee_env) -> None:
    env = shared_employee_env
    owner, participant = env.ids["owner"], env.ids["participant"]

    # Owner's first turn: the conversation has no binding yet, so this runs
    # the legacy first-binding path WITH the session's employee threaded in.
    _turn_a, outcome_a = _send(env, user_id=owner)
    assert outcome_a == "completed"

    # Share: membership only. The participant was never assigned the
    # employee (it is not even public) and still joins and speaks.
    env.harness.run(
        SessionParticipantsRepository(env.harness.db).add(
            tenant_id=TENANT, conversation_id=env.session_id, user_id=participant,
        )
    )
    _turn_b, outcome_b = _send(env, user_id=participant)
    assert outcome_b == "completed"

    version_a = _profile_version_for(env, owner)
    version_b = _profile_version_for(env, participant)
    assert version_a and version_b and version_a != version_b

    snap_a = _snapshot(env, version_a)
    snap_b = _snapshot(env, version_b)
    # One employee for every speaker: the segment is identical across the
    # speaker rotation even though the profile version itself rotated.
    assert snap_a.digital_employee is not None
    assert snap_a.digital_employee == snap_b.digital_employee
    assert snap_a.digital_employee.employee_id == EMPLOYEE_ID
    assert snap_a.digital_employee.manifesto == "你是礼品协作者，只聊礼品。"

    for label, snap in (("owner", snap_a), ("participant", snap_b)):
        # The EXACT bound bundle: organization skill + organization tool...
        assert [skill.source_id for skill in snap.skills] == [f"org_skill:{ORG_SKILL_ID}"], label
        assert {tool.external_tool_id for tool in snap.tools} == {ORG_TOOL_ID}, label
        # ...and neither speaker's personal grants leak into the bundle.
        personal_tools = {
            tool.external_tool_id
            for tool in snap.tools
            if str(tool.external_tool_id).startswith("personal-tool-")
        }
        assert personal_tools == set(), label
        personal_skills = {
            skill.source_id for skill in snap.skills
            if str(skill.source_id).startswith("personal-skill-")
        }
        assert personal_skills == set(), label

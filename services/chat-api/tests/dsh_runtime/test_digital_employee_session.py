"""Digital-employee session binding (ticket #42 core).

Three layers, one semantic (docs/digital-employee.md):

- Repository: the availability predicates the picker and the session
  admission share, including the existence-avoiding None results.
- Compiler: the employee segment participates in the immutable snapshot
  and its content hash, and reaches the host payload as employeeProfile.
- Chat service: a new conversation binds the employee at creation; an
  existing conversation ignores disagreeing requests loudly instead of
  switching identity.

The harness mirrors ``test_claim_token_readmission.py`` (real Mongo,
real repositories, the profile publish and coordinator stubbed, the turn
runner held open so the durable claim stays inspectable).
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.dsh_runtime.chat_service import DshChatService
from app.dsh_runtime import chat_service as chat_service_module
from app.dsh_runtime.bindings import KernelBindingRepository
from app.dsh_runtime.conversation import ConversationRepository
from app.dsh_runtime.digital_employees import (
    DigitalEmployeeDisabledError,
    DigitalEmployeeNotFoundError,
    DigitalEmployeeRepository,
)
from app.dsh_runtime.events import KernelEventRepository
from app.dsh_runtime.profile import InMemoryRuntimeProfileStore
from app.dsh_runtime.profile.compiler import ModelProfileCompiler
from app.dsh_runtime.profile.service import RuntimeProfilePublisher
from app.dsh_runtime.profile.synchronizer import ConversationProfileSynchronizer
from app.dsh_runtime.runtime_coordinator import RuntimeCoordinator

TENANT = "tenant-digital-employee"


class FakeModelCatalog:
    """Minimal model catalog mirroring tests/dsh_runtime/test_model_profile_step3.py."""

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
    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self.rows = rows

    async def load_for_profile(self, tenant_id: str, employee_id: str) -> dict[str, Any]:
        row = self.rows.get(employee_id)
        if row is None:
            raise DigitalEmployeeNotFoundError("digital_employee_not_found")
        if str(row.get("status") or "") != "active":
            raise DigitalEmployeeDisabledError("digital_employee_disabled")
        return row


def _employee_row(employee_id: str, *, status: str = "active", manifesto: str = "你是礼品协作者。") -> dict:
    return {
        "_id": employee_id,
        "name": f"员工-{employee_id}",
        "status": status,
        "manifesto": manifesto,
        "is_public": True,
    }


def test_employee_segment_participates_in_snapshot_hash_and_host_payload() -> None:
    asyncio.run(_test_employee_segment())


async def _test_employee_segment() -> None:
    rows = {"emp-1": _employee_row("emp-1")}
    compiler = ModelProfileCompiler(FakeModelCatalog(), employee_catalog=FakeEmployeeCatalog(rows))

    plain = await compiler.compile(tenant_id=TENANT)
    assert plain.digital_employee is None
    assert "employeeProfile" not in plain.host_payload(gateway_url="http://gw", access_token="tok")

    bound = await compiler.compile(tenant_id=TENANT, digital_employee_id="emp-1")
    assert bound.digital_employee is not None
    assert bound.digital_employee.employee_id == "emp-1"
    assert bound.digital_employee.manifesto == "你是礼品协作者。"
    payload = bound.host_payload(gateway_url="http://gw", access_token="tok")
    assert payload["employeeProfile"] == {
        "id": "emp-1",
        "name": "员工-emp-1",
        "manifesto": "你是礼品协作者。",
    }
    # Deterministic compilation: same inputs, same version.
    assert (await compiler.compile(tenant_id=TENANT, digital_employee_id="emp-1")) == bound
    # The segment is inside the content hash: a manifesto edit rotates.
    rows["emp-1"]["manifesto"] = "修订后的宣言。"
    edited = await compiler.compile(tenant_id=TENANT, digital_employee_id="emp-1")
    assert edited.profile_version != bound.profile_version


def test_employee_catalog_failure_modes() -> None:
    asyncio.run(_test_catalog_failures())


async def _test_catalog_failures() -> None:
    rows = {
        "emp-off": _employee_row("emp-off", status="disabled"),
        "emp-1": _employee_row("emp-1"),
    }
    catalog = FakeEmployeeCatalog(rows)
    compiler = ModelProfileCompiler(FakeModelCatalog(), employee_catalog=catalog)
    with pytest.raises(DigitalEmployeeDisabledError):
        await compiler.compile(tenant_id=TENANT, digital_employee_id="emp-off")
    with pytest.raises(DigitalEmployeeNotFoundError):
        await compiler.compile(tenant_id=TENANT, digital_employee_id="emp-missing")
    unconfigured = ModelProfileCompiler(FakeModelCatalog())
    with pytest.raises(ValueError, match="not configured"):
        await unconfigured.compile(tenant_id=TENANT, digital_employee_id="emp-1")


def test_repository_availability_predicates(real_mongo_db, monkeypatch) -> None:
    harness = real_mongo_db

    def insert(employee_id: str, *, status: str, is_public: bool, main_id: str = TENANT) -> None:
        harness.run(
            harness.db.digital_employees.insert_one(
                {
                    "_id": employee_id,
                    "main_id": main_id,
                    "name": f"员工-{employee_id}",
                    "description": "desc",
                    "avatar_url": None,
                    "manifesto": "m",
                    "status": status,
                    "is_public": is_public,
                    "skill_ids": [],
                    "tool_bindings": [],
                }
            )
        )

    insert("emp-public", status="active", is_public=True)
    insert("emp-private", status="active", is_public=False)
    insert("emp-disabled", status="disabled", is_public=True)
    insert("emp-foreign", status="active", is_public=True, main_id="tenant-b")
    insert("emp-role", status="active", is_public=False)
    insert("emp-direct", status="active", is_public=False)
    insert("emp-dangling", status="active", is_public=False)
    insert("emp-role-off", status="active", is_public=False)

    def assign(employee_id: str, subject_type: str, subject_id: str) -> None:
        harness.run(
            harness.db.digital_employee_assignments.insert_one(
                {
                    "main_id": TENANT,
                    "digital_employee_id": employee_id,
                    "subject_type": subject_type,
                    "subject_id": subject_id,
                }
            )
        )

    # user-1's position roles: one active, one deleted, one disabled - only
    # the active one may ever match (dangling assignments never surface).
    harness.run(
        harness.db.position_roles.insert_one(
            {"_id": "role-on", "main_id": TENANT, "status": "active", "name": "礼品角色"}
        )
    )
    harness.run(
        harness.db.position_roles.insert_one(
            {"_id": "role-off", "main_id": TENANT, "status": "disabled", "name": "旧角色"}
        )
    )
    for role_id in ("role-on", "role-gone", "role-off"):
        harness.run(
            harness.db.end_user_position_roles.insert_one(
                {"main_id": TENANT, "user_id": "user-1", "role_id": role_id}
            )
        )
    assign("emp-role", "role", "role-on")
    assign("emp-direct", "user", "user-1")
    assign("emp-dangling", "role", "role-gone")
    assign("emp-role-off", "role", "role-off")

    repo = DigitalEmployeeRepository(harness.db)
    listed = harness.run(repo.list_available(TENANT, "user-1"))
    # Public + role-assigned + directly assigned; dangling and disabled-role
    # assignments never surface. Sorted by name.
    assert [item["id"] for item in listed] == ["emp-direct", "emp-public", "emp-role"]
    assert listed[0]["name"] == "员工-emp-direct"

    assert harness.run(repo.admit_for_new_session(TENANT, "emp-public", "user-1")) is not None
    assert harness.run(repo.admit_for_new_session(TENANT, "emp-role", "user-1")) is not None
    assert harness.run(repo.admit_for_new_session(TENANT, "emp-direct", "user-1")) is not None
    # Not-public without a matching assignment, disabled, cross-tenant and
    # unknown ids are all None: indistinguishable from missing.
    assert harness.run(repo.admit_for_new_session(TENANT, "emp-private", "user-1")) is None
    assert harness.run(repo.admit_for_new_session(TENANT, "emp-dangling", "user-1")) is None
    assert harness.run(repo.admit_for_new_session(TENANT, "emp-role-off", "user-1")) is None
    assert harness.run(repo.admit_for_new_session(TENANT, "emp-disabled", "user-1")) is None
    assert harness.run(repo.admit_for_new_session(TENANT, "emp-foreign", "user-1")) is None
    assert harness.run(repo.admit_for_new_session(TENANT, "emp-missing", "user-1")) is None
    # Another user - and anonymous callers - see only the public employee.
    assert harness.run(repo.admit_for_new_session(TENANT, "emp-role", "user-2")) is None
    assert [item["id"] for item in harness.run(repo.list_available(TENANT, "user-2"))] == ["emp-public"]
    assert [item["id"] for item in harness.run(repo.list_available(TENANT, ""))] == ["emp-public"]

    loaded = harness.run(repo.load_for_profile(TENANT, "emp-public"))
    assert str(loaded["_id"]) == "emp-public"
    with pytest.raises(DigitalEmployeeDisabledError):
        harness.run(repo.load_for_profile(TENANT, "emp-disabled"))
    with pytest.raises(DigitalEmployeeNotFoundError):
        harness.run(repo.load_for_profile(TENANT, "emp-foreign"))


@pytest.fixture
def employee_env(real_mongo_db, monkeypatch):
    harness = real_mongo_db
    monkeypatch.setattr(chat_service_module, "get_db", lambda: harness.db, raising=False)
    harness.run(KernelBindingRepository(harness.db).ensure_indexes())
    harness.run(ConversationRepository(harness.db).ensure_indexes())

    async def fake_publish(*, tenant_id, actor_id, user_id="", model_instance_id=None,
                           digital_employee_id=None, activate=True):
        return SimpleNamespace(
            profile_version=f"pv-{digital_employee_id or 'none'}",
            model_instance_id="model-a",
        )

    chat = DshChatService(
        gateway=SimpleNamespace(),
        coordinator=SimpleNamespace(),
        conversations=ConversationRepository(harness.db),
        bindings=KernelBindingRepository(harness.db),
        events=KernelEventRepository(harness.db),
        profiles=SimpleNamespace(
            publish_model_profile=fake_publish,
            compile_model_profile=fake_publish,
        ),
        kernel_version="test-kernel",
    )

    async def fake_create_binding(**kwargs: Any) -> dict[str, Any]:
        # Mirror RuntimeCoordinator.create_binding's durable side effect: the
        # claim later resolves the binding row by id, so it must exist.
        import datetime as _dt

        binding = {
            "binding_id": f"bind-{uuid.uuid4().hex}",
            "conversation_id": kwargs["conversation_id"],
            "tenant_id": kwargs["tenant_id"],
            "user_id": kwargs["user_id"],
            "kernel_session_id": f"ks-{uuid.uuid4().hex}",
            "profile_version": "pv-x",
            "model_instance_id": "model-a",
            "execution_location": "server",
            "current": True,
            "status": "idle",
            "active_turn": None,
            "created_at": _dt.datetime.now(_dt.timezone.utc),
            "updated_at": _dt.datetime.now(_dt.timezone.utc),
        }
        # Inside the running loop: await the insert directly (harness.run
        # would nest run_until_complete inside a live loop).
        await harness.db.agent_kernel_bindings.insert_one(dict(binding))
        return binding

    chat._coordinator.create_binding = fake_create_binding

    release = asyncio.Event()

    async def hold_runner(*, binding, message_id, **_kwargs):
        await release.wait()
        await chat._finalizer.finalize(binding=binding, message_id=message_id, status="completed")
        return "completed"

    chat._turn_runner.run = hold_runner
    return SimpleNamespace(harness=harness, chat=chat, release=release)


def test_new_conversation_binds_employee_and_stores_it_on_the_session(employee_env) -> None:
    env = employee_env
    harness = env.harness
    harness.run(
        harness.db.digital_employees.insert_one(
            {
                "_id": "emp-xiaoli",
                "main_id": TENANT,
                "name": "小礼",
                "status": "active",
                "is_public": True,
                "manifesto": "你是小礼。",
            }
        )
    )
    turn = harness.run(
        env.chat.prepare_turn(
            tenant_id=TENANT, user_id="user-1", conversation_id=None,
            text="你好，帮我挑个礼品", model_instance_id="model-a", timezone_name="UTC",
            images=[], documents=[], digital_employee_id="emp-xiaoli",
        )
    )
    session_doc = harness.run(
        harness.db.chat_sessions.find_one({"_id": __import__("bson").ObjectId(turn.conversation_id)})
    )
    assert session_doc is not None
    assert session_doc.get("digital_employee_id") == "emp-xiaoli"
    env.release.set()
    assert harness.run(env.chat.wait_turn(turn.message_id)) == "completed"


def test_unavailable_employee_is_rejected_as_missing(employee_env) -> None:
    env = employee_env
    harness = env.harness
    harness.run(
        harness.db.digital_employees.insert_one(
            {
                "_id": "emp-private",
                "main_id": TENANT,
                "name": "不公开员工",
                "status": "active",
                "is_public": False,
                "manifesto": "m",
            }
        )
    )
    with pytest.raises(DigitalEmployeeNotFoundError):
        harness.run(
            env.chat.prepare_turn(
                tenant_id=TENANT, user_id="user-1", conversation_id=None,
                text="你好", model_instance_id="model-a", timezone_name="UTC",
                images=[], documents=[], digital_employee_id="emp-private",
            )
        )
    # Nothing was created: the rejection happened before any side effect.
    assert harness.run(harness.db.chat_sessions.count_documents({"main_id": TENANT})) == 0


@pytest.fixture
def bound_session_env(real_mongo_db, monkeypatch):
    """An existing conversation already bound to an employee, with a live binding."""
    harness = real_mongo_db
    monkeypatch.setattr(chat_service_module, "get_db", lambda: harness.db, raising=False)
    harness.run(KernelBindingRepository(harness.db).ensure_indexes())
    harness.run(SessionParticipantsRepositoryForTest(harness.db).ensure_indexes())
    owner_id = str(__import__("bson").ObjectId())
    session_id = str(
        harness.run(
            ConversationRepository(harness.db).create(
                tenant_id=TENANT, user_id=owner_id, title="bound",
                digital_employee_id="emp-xiaoli",
            )
        )["_id"]
    )
    import datetime as _dt

    now = _dt.datetime.now(_dt.timezone.utc)
    harness.run(
        harness.db.agent_kernel_bindings.insert_one(
            {
                "binding_id": f"bind-{uuid.uuid4().hex}",
                "conversation_id": session_id,
                "tenant_id": TENANT,
                "user_id": owner_id,
                "current": True,
                "status": "idle",
                "active_turn": None,
                "kernel_session_id": f"ks-{uuid.uuid4().hex}",
                "execution_location": "server",
                "profile_version": "pv-f2",
                "model_instance_id": "model-a",
                "created_at": now,
                "updated_at": now,
            }
        )
    )

    captured: dict[str, Any] = {}

    async def fake_publish(*, tenant_id, actor_id, user_id="", model_instance_id=None,
                           digital_employee_id=None, activate=True):
        return SimpleNamespace(
            profile_version=f"pv-{digital_employee_id or 'none'}",
            model_instance_id="model-a",
        )

    chat = DshChatService(
        gateway=SimpleNamespace(),
        coordinator=RuntimeCoordinator(SimpleNamespace(), KernelBindingRepository(harness.db)),
        conversations=ConversationRepository(harness.db),
        bindings=KernelBindingRepository(harness.db),
        events=KernelEventRepository(harness.db),
        profiles=SimpleNamespace(
            publish_model_profile=fake_publish,
            compile_model_profile=fake_publish,
        ),
        kernel_version="test-kernel",
    )

    async def passthrough(binding, *, tenant_id, user_id, model_instance_id=None,
                          digital_employee_id=None):
        captured["digital_employee_id"] = digital_employee_id
        return SimpleNamespace(binding=binding)

    chat._profile_sync.synchronize = passthrough
    release = asyncio.Event()

    async def hold_runner(*, binding, message_id, **_kwargs):
        await release.wait()
        await chat._finalizer.finalize(binding=binding, message_id=message_id, status="completed")
        return "completed"

    chat._turn_runner.run = hold_runner
    return SimpleNamespace(
        harness=harness, chat=chat, release=release, session_id=session_id,
        owner_id=owner_id, captured=captured,
    )


class SessionParticipantsRepositoryForTest:
    """Index-only stand-in: the fixture only needs the participants indexes."""

    def __init__(self, db) -> None:
        from app.dsh_runtime.conversation.participants_repository import (
            SessionParticipantsRepository,
        )

        self._inner = SessionParticipantsRepository(db)

    async def ensure_indexes(self) -> None:
        await self._inner.ensure_indexes()


def test_existing_conversation_rejects_conflicting_employee(bound_session_env) -> None:
    env = bound_session_env
    with pytest.raises(ValueError, match="digital_employee_mismatch"):
        env.harness.run(
            env.chat.prepare_turn(
                tenant_id=TENANT, user_id=env.owner_id, conversation_id=env.session_id,
                text="换个员工试试", model_instance_id="model-a", timezone_name="UTC",
                images=[], documents=[], digital_employee_id="emp-other",
            )
        )


def test_existing_conversation_threads_session_employee_into_sync(bound_session_env) -> None:
    env = bound_session_env
    turn = env.harness.run(
        env.chat.prepare_turn(
            tenant_id=TENANT, user_id=env.owner_id, conversation_id=env.session_id,
            text="继续聊", model_instance_id="model-a", timezone_name="UTC",
            images=[], documents=[], digital_employee_id="emp-xiaoli",
        )
    )
    # The session's employee - not the speaker's - reaches profile sync.
    assert env.captured["digital_employee_id"] == "emp-xiaoli"
    env.release.set()
    assert env.harness.run(env.chat.wait_turn(turn.message_id)) == "completed"


def test_disabled_employee_blocks_the_next_turn_through_profile_sync() -> None:
    """The hard block for continuing a bound session (docs/digital-employee.md,
    Errors): the next turn's profile compile raises the explicit disabled
    error from inside the rotation path - the endpoint maps it to the clear
    409 instead of the session silently degrading to a normal session."""
    class DisabledEmployeeCatalog:
        async def load_for_profile(self, tenant_id: str, employee_id: str) -> dict[str, Any]:
            raise DigitalEmployeeDisabledError("digital_employee_disabled")

    import asyncio as _asyncio

    compiler = ModelProfileCompiler(FakeModelCatalog(), employee_catalog=DisabledEmployeeCatalog())
    publisher = RuntimeProfilePublisher(compiler, InMemoryRuntimeProfileStore())

    class RestoreOnlyCoordinator:
        async def restore(self, binding: dict[str, Any]) -> dict[str, Any]:
            return binding

    synchronizer = ConversationProfileSynchronizer(publisher, RestoreOnlyCoordinator())
    binding = {
        "binding_id": "bind-1",
        "conversation_id": "conv-1",
        "profile_version": "pv-old",
        "model_instance_id": "model-a",
    }
    with pytest.raises(DigitalEmployeeDisabledError):
        asyncio.run(
            synchronizer.synchronize(
                binding,
                tenant_id=TENANT,
                user_id="user-1",
                model_instance_id="model-a",
                digital_employee_id="emp-xiaoli",
            )
        )

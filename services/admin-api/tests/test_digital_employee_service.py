from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import HTTPException
import pytest

from app.api.routes import analytics as analytics_routes
from app.digital_employees import repository as repository_module
from app.digital_employees.constants import MANIFESTO_MAX_LENGTH, NAME_MAX_LENGTH
from app.digital_employees.service import DigitalEmployeeService


def _matches(row, query):
    for key, condition in query.items():
        if key == "$or":
            if not any(_matches(row, sub) for sub in condition):
                return False
            continue
        value = row.get(key)
        if isinstance(condition, dict):
            for operator, operand in condition.items():
                if operator == "$options":
                    continue
                if operator == "$ne":
                    if value == operand:
                        return False
                elif operator == "$in":
                    if value not in operand:
                        return False
                elif operator == "$gt":
                    if value is None or not value > operand:
                        return False
                elif operator == "$exists":
                    if bool(operand) != (key in row):
                        return False
                elif operator == "$regex":
                    flags = re.IGNORECASE if "i" in str(condition.get("$options") or "") else 0
                    if value is None or not re.search(operand, str(value), flags):
                        return False
                else:
                    raise AssertionError(f"unsupported query operator: {operator}")
        elif value != condition:
            return False
    return True


def _agg_value(row, operand):
    if operand == 1:
        return 1
    if isinstance(operand, dict) and "$ifNull" in operand:
        field, fallback = operand["$ifNull"]
        name = str(field)[1:]
        value = row.get(name)
        return fallback if value is None else value
    if isinstance(operand, str) and operand.startswith("$"):
        return row.get(operand[1:])
    raise AssertionError(f"unsupported accumulator operand: {operand}")


def _aggregate_group(rows, spec):
    id_expr = spec["_id"]
    if not (isinstance(id_expr, str) and id_expr.startswith("$")):
        raise AssertionError(f"unsupported group _id expression: {id_expr}")
    grouped = {}
    for row in rows:
        grouped.setdefault(row.get(id_expr[1:]), []).append(row)
    result = []
    for key, members in grouped.items():
        out = {"_id": key}
        for name, accumulator in spec.items():
            if name == "_id":
                continue
            operator, operand = next(iter(accumulator.items()))
            values = [_agg_value(row, operand) for row in members]
            if operator == "$sum":
                out[name] = sum(values)
            elif operator == "$max":
                out[name] = max(values) if values else None
            else:
                raise AssertionError(f"unsupported accumulator operator: {operator}")
        result.append(out)
    return result


class Cursor:
    def __init__(self, rows): self.rows = rows
    def sort(self, key, direction): self.rows.sort(key=lambda row: row.get(key), reverse=direction < 0); return self
    async def to_list(self, length): return [dict(row) for row in self.rows[:length]]


class Collection:
    def __init__(self): self.rows = {}
    async def create_index(self, keys, **kwargs): return None
    async def find_one(self, query, projection=None):
        for row in self.rows.values():
            if _matches(row, query): return dict(row)
        return None
    async def insert_one(self, row): self.rows[row["_id"]] = dict(row)
    async def insert_many(self, rows):
        for row in rows: self.rows[row["_id"]] = dict(row)
    async def update_one(self, query, update):
        for row in self.rows.values():
            if _matches(row, query): row.update(update.get("$set") or {})
    async def delete_one(self, query):
        for key, row in list(self.rows.items()):
            if _matches(row, query):
                self.rows.pop(key)
                return
    async def delete_many(self, query):
        for key in [key for key, row in self.rows.items() if _matches(row, query)]:
            self.rows.pop(key)
    async def count_documents(self, query):
        return sum(1 for row in self.rows.values() if _matches(row, query))
    def find(self, query, projection=None): return Cursor([dict(row) for row in self.rows.values() if _matches(row, query)])
    def aggregate(self, pipeline):
        rows = [dict(row) for row in self.rows.values()]
        for stage in pipeline:
            if "$match" in stage:
                rows = [row for row in rows if _matches(row, stage["$match"])]
            elif "$group" in stage:
                rows = _aggregate_group(rows, stage["$group"])
            elif "$sort" in stage:
                key, direction = next(iter(stage["$sort"].items()))
                rows = sorted(rows, key=lambda row: row.get(key), reverse=direction < 0)
            else:
                raise AssertionError(f"unsupported pipeline stage: {stage}")
        return Cursor(rows)


class Database:
    def __init__(self):
        self.digital_employees = Collection()
        self.digital_employee_assignments = Collection()
        self.skills = Collection()
        self.external_tools = Collection()
        self.position_roles = Collection()
        self.end_users = Collection()
        self.chat_sessions = Collection()
        self.token_usage_logs = Collection()
        self.end_user_org_relations = Collection()
    def __getitem__(self, name): return getattr(self, name)


@pytest.fixture()
def db(monkeypatch):
    database = Database()
    monkeypatch.setattr(repository_module, "get_db", lambda: database)
    return database


def seed_skill(db, skill_id, *, main_id="tenant-a", name="技能", enabled=True, visibility="", user_id=""):
    db.skills.rows[skill_id] = {
        "_id": skill_id,
        "main_id": main_id,
        "name": name,
        "enabled": enabled,
        "visibility": visibility,
        "user_id": user_id,
    }


def seed_tool(
    db,
    tool_id,
    *,
    main_id="tenant-a",
    name="工具",
    tool_type="mcp",
    status="active",
    discovered=("echo",),
    scope="organization",
):
    row = {
        "_id": tool_id,
        "main_id": main_id,
        "name": name,
        "type": tool_type,
        "status": status,
        "discovered_tools": [{"name": item} for item in discovered],
    }
    if scope is not None:  # None keeps the legacy scope-less shape ($exists: False).
        row["scope"] = scope
        row["owner_user_id"] = ""
    db.external_tools.rows[tool_id] = row


# Valid 24-hex ObjectId strings, mirroring how assignment rows keep the string
# form of end_users' ObjectId ids.
USER_ONE = "507f1f77bcf86cd799439011"
USER_TWO = "507f1f77bcf86cd799439012"


def seed_role(db, role_id, *, main_id="tenant-a", name="岗位角色", status="active"):
    db.position_roles.rows[role_id] = {
        "_id": role_id,
        "main_id": main_id,
        "name": name,
        "status": status,
    }


def seed_user(db, user_id, *, main_id="tenant-a", name="用户"):
    object_id = ObjectId(user_id)
    db.end_users.rows[object_id] = {"_id": object_id, "main_id": main_id, "name": name}


def seed_session(db, session_id, *, main_id="tenant-a", employee_id=""):
    db.chat_sessions.rows[session_id] = {
        "_id": session_id,
        "main_id": main_id,
        "digital_employee_id": employee_id,
    }


def seed_usage(
    db,
    usage_id,
    *,
    main_id="tenant-a",
    employee_id="",
    user_id=USER_ONE,
    model="deepseek-v4-flash",
    prompt=0,
    completion=0,
    created_at=None,
):
    db.token_usage_logs.rows[usage_id] = {
        "_id": usage_id,
        "main_id": main_id,
        "digital_employee_id": employee_id,
        "user_id": user_id,
        "model_name": model,
        "total_tokens": prompt + completion,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "created_at": created_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
    }


def test_create_persists_storage_shape_with_defaults(db):
    seed_skill(db, "skill-1", name="礼物技能")
    seed_tool(db, "tool-1", name="礼物 MCP", tool_type="mcp", discovered=("echo",))
    employee = asyncio.run(DigitalEmployeeService().create_employee(
        "tenant-a",
        {"name": "小礼", "description": "礼物助手", "skillIds": ["skill-1"], "toolBindings": [{"toolId": "tool-1", "enabledToolNames": ["echo"]}]},
        "admin-a",
    ))

    row = db.digital_employees.rows[employee["id"]]
    assert row["main_id"] == "tenant-a"
    assert row["name"] == "小礼"
    assert row["description"] == "礼物助手"
    assert row["manifesto"] == ""
    assert row["avatar_url"] == ""
    assert row["skill_ids"] == ["skill-1"]
    assert row["tool_bindings"] == [{"tool_id": "tool-1", "enabled_tool_names": ["echo"]}]
    assert row["status"] == "active"
    assert row["is_public"] is False
    assert row["created_by"] == "admin-a"
    assert row["updated_by"] == "admin-a"
    assert row["created_at"] == row["updated_at"]
    assert employee["isPublic"] is False
    assert employee["status"] == "active"
    assert employee["toolBindings"] == [{"toolId": "tool-1", "enabledToolNames": ["echo"]}]


def test_duplicate_name_is_rejected_within_one_tenant_only(db):
    service = DigitalEmployeeService()
    asyncio.run(service.create_employee("tenant-a", {"name": "小礼"}, "admin-a"))

    with pytest.raises(HTTPException) as conflict:
        asyncio.run(service.create_employee("tenant-a", {"name": "小礼"}, "admin-a"))
    assert conflict.value.status_code == 409
    assert conflict.value.detail == "数字员工名称已存在"

    other_tenant = asyncio.run(service.create_employee("tenant-b", {"name": "小礼"}, "admin-b"))
    assert other_tenant["id"]
    assert len(db.digital_employees.rows) == 2


def test_manifesto_is_optional_but_capped_at_8192_characters(db):
    service = DigitalEmployeeService()

    with pytest.raises(HTTPException) as too_long:
        asyncio.run(service.create_employee("tenant-a", {"name": "小礼", "manifesto": "字" * (MANIFESTO_MAX_LENGTH + 1)}, "admin-a"))
    assert too_long.value.status_code == 400
    assert "宣言" in too_long.value.detail
    assert len(db.digital_employees.rows) == 0

    employee = asyncio.run(service.create_employee("tenant-a", {"name": "小礼", "manifesto": ""}, "admin-a"))
    assert employee["manifesto"] == ""


def test_name_is_required_and_limited_to_64_characters(db):
    service = DigitalEmployeeService()

    with pytest.raises(HTTPException) as missing:
        asyncio.run(service.create_employee("tenant-a", {}, "admin-a"))
    assert missing.value.status_code == 400
    assert "名称不能为空" in missing.value.detail

    with pytest.raises(HTTPException) as too_long:
        asyncio.run(service.create_employee("tenant-a", {"name": "字" * (NAME_MAX_LENGTH + 1)}, "admin-a"))
    assert too_long.value.status_code == 400
    assert str(NAME_MAX_LENGTH) in too_long.value.detail

    assert len(db.digital_employees.rows) == 0


def test_avatar_url_and_description_are_validated(db):
    service = DigitalEmployeeService()

    with pytest.raises(HTTPException) as bad_url:
        asyncio.run(service.create_employee("tenant-a", {"name": "小礼", "avatarUrl": "not-a-url"}, "admin-a"))
    assert bad_url.value.status_code == 400

    with pytest.raises(HTTPException) as long_description:
        asyncio.run(service.create_employee("tenant-a", {"name": "小礼", "description": "字" * 513}, "admin-a"))
    assert long_description.value.status_code == 400

    employee = asyncio.run(service.create_employee(
        "tenant-a", {"name": "小礼", "avatarUrl": "https://example.com/avatar.png", "description": "一句话说明"}, "admin-a",
    ))
    assert employee["avatarUrl"] == "https://example.com/avatar.png"
    assert employee["description"] == "一句话说明"


def test_tenants_cannot_see_or_touch_each_others_employees(db):
    service = DigitalEmployeeService()
    created = asyncio.run(service.create_employee("tenant-a", {"name": "小礼"}, "admin-a"))

    with pytest.raises(HTTPException) as missing:
        asyncio.run(service.get_employee("tenant-b", created["id"]))
    assert missing.value.status_code == 404

    assert asyncio.run(service.list_employees("tenant-b")) == []

    with pytest.raises(HTTPException) as foreign:
        asyncio.run(service.update_employee("tenant-b", created["id"], {"name": "被改写"}, "admin-b"))
    assert foreign.value.status_code == 404
    assert db.digital_employees.rows[created["id"]]["name"] == "小礼"


def test_update_persists_fields_status_and_audit_bump(db):
    seed_skill(db, "skill-1", name="技能一")
    seed_skill(db, "skill-2", name="技能二")
    seed_tool(db, "tool-1", name="礼物 MCP", tool_type="mcp", discovered=("echo",))
    service = DigitalEmployeeService()
    created = asyncio.run(service.create_employee("tenant-a", {"name": "小礼", "description": "旧说明"}, "admin-a"))
    previous_updated_at = db.digital_employees.rows[created["id"]]["updated_at"]

    updated = asyncio.run(service.update_employee("tenant-a", created["id"], {
        "name": "小礼",
        "description": "新说明",
        "manifesto": "你是礼物助手",
        "skillIds": ["skill-1", "skill-2"],
        "toolBindings": [{"toolId": "tool-1", "enabledToolNames": ["echo"]}],
        "status": "disabled",
        "isPublic": True,
    }, "admin-b"))

    row = db.digital_employees.rows[created["id"]]
    assert row["name"] == "小礼"
    assert row["description"] == "新说明"
    assert row["manifesto"] == "你是礼物助手"
    assert row["skill_ids"] == ["skill-1", "skill-2"]
    assert row["tool_bindings"] == [{"tool_id": "tool-1", "enabled_tool_names": ["echo"]}]
    assert row["status"] == "disabled"
    assert row["is_public"] is True
    assert row["created_by"] == "admin-a"
    assert row["updated_by"] == "admin-b"
    assert row["updated_at"] >= previous_updated_at
    assert updated["status"] == "disabled"
    assert updated["isPublic"] is True


def test_update_keeps_own_name_but_rejects_another_employees_name(db):
    service = DigitalEmployeeService()
    first = asyncio.run(service.create_employee("tenant-a", {"name": "小礼"}, "admin-a"))
    second = asyncio.run(service.create_employee("tenant-a", {"name": "小智"}, "admin-a"))

    kept = asyncio.run(service.update_employee("tenant-a", first["id"], {"name": "小礼", "description": "改名测试"}, "admin-a"))
    assert kept["description"] == "改名测试"

    with pytest.raises(HTTPException) as conflict:
        asyncio.run(service.update_employee("tenant-a", second["id"], {"name": "小礼"}, "admin-a"))
    assert conflict.value.status_code == 409


def test_list_filters_by_status_and_searches_name_or_description(db):
    service = DigitalEmployeeService()
    first = asyncio.run(service.create_employee("tenant-a", {"name": "小礼", "description": "礼物助手"}, "admin-a"))
    second = asyncio.run(service.create_employee("tenant-a", {"name": "小智", "description": "会议纪要"}, "admin-a"))
    third = asyncio.run(service.create_employee("tenant-a", {"name": "已停用", "status": "disabled"}, "admin-a"))
    db.digital_employees.rows[second["id"]]["updated_at"] = db.digital_employees.rows[first["id"]]["updated_at"].replace(year=2030)
    db.digital_employees.rows[third["id"]]["updated_at"] = db.digital_employees.rows[first["id"]]["updated_at"].replace(year=2029)

    listed = asyncio.run(service.list_employees("tenant-a"))
    assert [item["name"] for item in listed] == ["小智", "已停用", "小礼"]

    active_only = asyncio.run(service.list_employees("tenant-a", status="active"))
    assert [item["name"] for item in active_only] == ["小智", "小礼"]

    with pytest.raises(HTTPException) as bad_status:
        asyncio.run(DigitalEmployeeService().list_employees("tenant-a", status="bogus"))
    assert bad_status.value.status_code == 400

    by_name = asyncio.run(service.list_employees("tenant-a", q="小礼"))
    assert [item["name"] for item in by_name] == ["小礼"]

    by_description = asyncio.run(service.list_employees("tenant-a", q="纪要"))
    assert [item["name"] for item in by_description] == ["小智"]


def test_bindings_reference_only_existing_enabled_organization_skills(db):
    service = DigitalEmployeeService()
    seed_skill(db, "skill-live", name="在用技能")
    seed_skill(db, "skill-disabled", name="已停用技能", enabled=False)
    seed_skill(db, "skill-private", name="个人技能", visibility="private", user_id="user-1")

    for skill_id in ("skill-gone", "skill-disabled", "skill-private"):
        with pytest.raises(HTTPException) as invalid:
            asyncio.run(service.create_employee("tenant-a", {"name": "小礼", "skillIds": [skill_id]}, "admin-a"))
        assert invalid.value.status_code == 400
        assert skill_id in invalid.value.detail
        assert "无法绑定" in invalid.value.detail

    with pytest.raises(HTTPException) as listing:
        asyncio.run(service.create_employee(
            "tenant-a", {"name": "小礼", "skillIds": ["skill-gone", "skill-disabled"]}, "admin-a",
        ))
    assert listing.value.status_code == 400
    assert "skill-gone" in listing.value.detail
    assert "skill-disabled" in listing.value.detail
    assert len(db.digital_employees.rows) == 0

    created = asyncio.run(service.create_employee("tenant-a", {"name": "小礼", "skillIds": ["skill-live"]}, "admin-a"))
    assert created["skillIds"] == ["skill-live"]
    assert created["bindingHealth"] == {"missingSkillIds": [], "missingToolIds": [], "missingToolNames": []}

    with pytest.raises(HTTPException) as on_update:
        asyncio.run(service.update_employee(
            "tenant-a", created["id"], {"name": "小礼", "skillIds": ["skill-private"]}, "admin-a",
        ))
    assert on_update.value.status_code == 400
    assert db.digital_employees.rows[created["id"]]["skill_ids"] == ["skill-live"]


def test_tool_bindings_require_active_organization_tools(db):
    service = DigitalEmployeeService()
    seed_tool(db, "tool-live", name="在用 MCP", discovered=("echo",))
    seed_tool(db, "tool-disabled", name="已停用 MCP", status="disabled", discovered=("echo",))
    seed_tool(db, "tool-personal", name="个人工具", scope="user", discovered=("echo",))
    seed_tool(db, "tool-legacy", name="遗留工具", scope=None, discovered=("echo",))

    for tool_id in ("tool-gone", "tool-disabled", "tool-personal"):
        with pytest.raises(HTTPException) as invalid:
            asyncio.run(service.create_employee(
                "tenant-a", {"name": "小礼", "toolBindings": [{"toolId": tool_id, "enabledToolNames": []}]}, "admin-a",
            ))
        assert invalid.value.status_code == 400
        assert tool_id in invalid.value.detail
        assert "无法绑定" in invalid.value.detail
    assert len(db.digital_employees.rows) == 0

    # Legacy tools without an explicit scope count as organization tools.
    legacy = asyncio.run(service.create_employee(
        "tenant-a", {"name": "小礼", "toolBindings": [{"toolId": "tool-legacy", "enabledToolNames": ["echo"]}]}, "admin-a",
    ))
    assert db.digital_employees.rows[legacy["id"]]["tool_bindings"] == [
        {"tool_id": "tool-legacy", "enabled_tool_names": ["echo"]},
    ]

    created = asyncio.run(service.create_employee(
        "tenant-a", {"name": "小智", "toolBindings": [{"toolId": "tool-live", "enabledToolNames": ["echo"]}]}, "admin-a",
    ))
    with pytest.raises(HTTPException) as on_update:
        asyncio.run(service.update_employee(
            "tenant-a",
            created["id"],
            {"name": "小智", "toolBindings": [{"toolId": "tool-personal", "enabledToolNames": []}]},
            "admin-a",
        ))
    assert on_update.value.status_code == 400
    assert db.digital_employees.rows[created["id"]]["tool_bindings"] == [
        {"tool_id": "tool-live", "enabled_tool_names": ["echo"]},
    ]


def test_mcp_enabled_tool_names_must_be_currently_discovered(db):
    service = DigitalEmployeeService()
    seed_tool(db, "tool-mcp", name="礼物服务", tool_type="mcp", discovered=("echo", "search"))
    seed_tool(db, "tool-http", name="HTTP 服务", tool_type="http", discovered=())

    with pytest.raises(HTTPException) as unknown_name:
        asyncio.run(service.create_employee(
            "tenant-a",
            {"name": "小礼", "toolBindings": [{"toolId": "tool-mcp", "enabledToolNames": ["echo", "nope"]}]},
            "admin-a",
        ))
    assert unknown_name.value.status_code == 400
    assert "nope" in unknown_name.value.detail
    assert "礼物服务" in unknown_name.value.detail
    assert len(db.digital_employees.rows) == 0

    # An empty list means no narrowing and is accepted.
    narrowed_to_all = asyncio.run(service.create_employee(
        "tenant-a", {"name": "小礼", "toolBindings": [{"toolId": "tool-mcp", "enabledToolNames": []}]}, "admin-a",
    ))
    assert db.digital_employees.rows[narrowed_to_all["id"]]["tool_bindings"] == [
        {"tool_id": "tool-mcp", "enabled_tool_names": []},
    ]

    # HTTP tools ignore enabledToolNames during validation.
    http_employee = asyncio.run(service.create_employee(
        "tenant-a", {"name": "小智", "toolBindings": [{"toolId": "tool-http", "enabledToolNames": ["anything"]}]}, "admin-a",
    ))
    assert db.digital_employees.rows[http_employee["id"]]["tool_bindings"] == [
        {"tool_id": "tool-http", "enabled_tool_names": ["anything"]},
    ]


def test_binding_health_flags_stale_references_on_reads(db):
    service = DigitalEmployeeService()
    seed_skill(db, "skill-live", name="在用技能")
    seed_skill(db, "skill-doomed", name="将停用技能")
    seed_tool(db, "tool-live", name="在用 MCP", tool_type="mcp", discovered=("echo", "search"))
    seed_tool(db, "tool-doomed", name="将删除 MCP", tool_type="mcp", discovered=("ping",))
    seed_tool(db, "tool-halting", name="将停用 HTTP", tool_type="http", discovered=())

    dirty = asyncio.run(service.create_employee("tenant-a", {
        "name": "脏引用",
        "skillIds": ["skill-live", "skill-doomed"],
        "toolBindings": [
            {"toolId": "tool-live", "enabledToolNames": ["echo", "search"]},
            {"toolId": "tool-doomed", "enabledToolNames": []},
            {"toolId": "tool-halting", "enabledToolNames": []},
        ],
    }, "admin-a"))
    clean = asyncio.run(service.create_employee("tenant-a", {
        "name": "干净",
        "skillIds": ["skill-live"],
        "toolBindings": [{"toolId": "tool-live", "enabledToolNames": ["echo"]}],
    }, "admin-a"))

    # References go stale only after the employee was saved.
    db.skills.rows["skill-doomed"]["enabled"] = False
    db.external_tools.rows.pop("tool-doomed")
    db.external_tools.rows["tool-halting"]["status"] = "disabled"
    db.external_tools.rows["tool-live"]["discovered_tools"] = [{"name": "echo"}]  # re-discovery lost "search"

    listed_health = {item["name"]: item["bindingHealth"] for item in asyncio.run(service.list_employees("tenant-a"))}
    assert listed_health["脏引用"] == {
        "missingSkillIds": ["skill-doomed"],
        "missingToolIds": ["tool-doomed", "tool-halting"],
        "missingToolNames": [{"toolId": "tool-live", "names": ["search"]}],
    }
    assert listed_health["干净"] == {"missingSkillIds": [], "missingToolIds": [], "missingToolNames": []}

    fetched_dirty = asyncio.run(service.get_employee("tenant-a", dirty["id"]))
    assert fetched_dirty["bindingHealth"] == listed_health["脏引用"]
    fetched_clean = asyncio.run(service.get_employee("tenant-a", clean["id"]))
    assert fetched_clean["bindingHealth"] == {"missingSkillIds": [], "missingToolIds": [], "missingToolNames": []}

    # Stale references are also reported for personal-scope skills flipped after saving.
    db.skills.rows["skill-live"]["visibility"] = "private"
    db.skills.rows["skill-live"]["user_id"] = "user-1"
    assert asyncio.run(service.get_employee("tenant-a", clean["id"]))["bindingHealth"]["missingSkillIds"] == ["skill-live"]


def test_replace_assignments_swaps_subject_sets_and_is_idempotent(db):
    seed_role(db, "role-1", name="客服")
    seed_role(db, "role-2", name="运营")
    seed_user(db, USER_ONE, name="张三")
    seed_user(db, USER_TWO, name="李四")
    service = DigitalEmployeeService()
    employee = asyncio.run(service.create_employee("tenant-a", {"name": "小礼"}, "admin-a"))

    first = asyncio.run(service.replace_assignments(
        "tenant-a", employee["id"], {"roleIds": ["role-1", "role-1"], "userIds": [USER_ONE]}, "admin-a",
    ))
    # Duplicated subject ids collapse into one row.
    assert first == {"id": employee["id"], "roleIds": ["role-1"], "userIds": [USER_ONE]}
    rows = list(db.digital_employee_assignments.rows.values())
    assert {(row["subject_type"], row["subject_id"]) for row in rows} == {("role", "role-1"), ("user", USER_ONE)}
    assert all(row["main_id"] == "tenant-a" and row["digital_employee_id"] == employee["id"] for row in rows)
    assert all(row["created_by"] == "admin-a" for row in rows)
    created_at_by_id = {row["_id"]: row["created_at"] for row in rows}

    # Idempotent re-PUT with the same payload keeps the rows untouched.
    asyncio.run(service.replace_assignments(
        "tenant-a", employee["id"], {"roleIds": ["role-1"], "userIds": [USER_ONE]}, "admin-b",
    ))
    assert {row["_id"]: row["created_at"] for row in db.digital_employee_assignments.rows.values()} == created_at_by_id
    assert all(row["created_by"] == "admin-a" for row in db.digital_employee_assignments.rows.values())

    # REPLACE-ALL: only the newly requested subjects remain afterwards.
    asyncio.run(service.replace_assignments(
        "tenant-a", employee["id"], {"roleIds": ["role-2"], "userIds": [USER_TWO]}, "admin-b",
    ))
    rows = list(db.digital_employee_assignments.rows.values())
    assert {(row["subject_type"], row["subject_id"]) for row in rows} == {("role", "role-2"), ("user", USER_TWO)}

    # Empty lists clear the assignment set entirely.
    asyncio.run(service.replace_assignments("tenant-a", employee["id"], {"roleIds": [], "userIds": []}, "admin-b"))
    assert db.digital_employee_assignments.rows == {}


def test_replace_assignments_rejects_unknown_or_inactive_roles_and_unknown_users(db):
    seed_role(db, "role-live", name="在用角色")
    seed_role(db, "role-disabled", name="停用角色", status="disabled")
    seed_role(db, "role-other-tenant", name="其他企业角色", main_id="tenant-b")
    seed_user(db, USER_ONE, name="张三")
    service = DigitalEmployeeService()
    employee = asyncio.run(service.create_employee("tenant-a", {"name": "小礼"}, "admin-a"))

    with pytest.raises(HTTPException) as invalid_roles:
        asyncio.run(service.replace_assignments(
            "tenant-a", employee["id"],
            {"roleIds": ["role-gone", "role-disabled", "role-other-tenant"], "userIds": [USER_ONE]},
            "admin-a",
        ))
    assert invalid_roles.value.status_code == 400
    for role_id in ("role-gone", "role-disabled", "role-other-tenant"):
        assert role_id in invalid_roles.value.detail
    assert "无法指派" in invalid_roles.value.detail

    # Unknown and non-ObjectId user ids are both rejected (neither can exist in end_users).
    with pytest.raises(HTTPException) as invalid_users:
        asyncio.run(service.replace_assignments(
            "tenant-a", employee["id"], {"userIds": ["user-gone", USER_ONE[:8]]}, "admin-a",
        ))
    assert invalid_users.value.status_code == 400
    assert "user-gone" in invalid_users.value.detail
    assert "无法指派" in invalid_users.value.detail

    # Nothing was written by the rejected calls.
    assert db.digital_employee_assignments.rows == {}

    with pytest.raises(HTTPException) as missing:
        asyncio.run(service.replace_assignments("tenant-a", "no-such-employee", {"roleIds": ["role-live"]}, "admin-a"))
    assert missing.value.status_code == 404


def test_reads_carry_role_and_user_assignments(db):
    seed_role(db, "role-1", name="客服")
    seed_user(db, USER_ONE, name="张三")
    service = DigitalEmployeeService()
    assigned = asyncio.run(service.create_employee("tenant-a", {"name": "小礼"}, "admin-a"))
    bare = asyncio.run(service.create_employee("tenant-a", {"name": "小智"}, "admin-a"))
    asyncio.run(service.replace_assignments(
        "tenant-a", assigned["id"], {"roleIds": ["role-1"], "userIds": [USER_ONE]}, "admin-a",
    ))

    fetched = asyncio.run(service.get_employee("tenant-a", assigned["id"]))
    assert fetched["roleIds"] == ["role-1"]
    assert fetched["userIds"] == [USER_ONE]

    listed = {item["name"]: item for item in asyncio.run(service.list_employees("tenant-a"))}
    assert listed["小礼"]["roleIds"] == ["role-1"]
    assert listed["小礼"]["userIds"] == [USER_ONE]
    assert listed["小智"]["roleIds"] == []
    assert listed["小智"]["userIds"] == []


def test_delete_employee_is_blocked_by_chat_session_references(db):
    service = DigitalEmployeeService()
    employee = asyncio.run(service.create_employee("tenant-a", {"name": "小礼"}, "admin-a"))
    seed_session(db, "sess-foreign", main_id="tenant-a", employee_id="another-employee")
    seed_session(db, "sess-other-tenant", main_id="tenant-b", employee_id=employee["id"])

    # Rows that reference other employees or tenants do not block the delete.
    asyncio.run(service.delete_employee("tenant-a", employee["id"]))

    referenced = asyncio.run(service.create_employee("tenant-a", {"name": "小智"}, "admin-a"))
    seed_session(db, "sess-1", main_id="tenant-a", employee_id=referenced["id"])
    with pytest.raises(HTTPException) as conflict:
        asyncio.run(service.delete_employee("tenant-a", referenced["id"]))
    assert conflict.value.status_code == 409
    assert conflict.value.detail == "该数字员工仍有会话引用，请先停用"
    assert referenced["id"] in db.digital_employees.rows


def test_delete_employee_removes_the_row_and_its_assignments(db):
    seed_role(db, "role-1", name="客服")
    seed_user(db, USER_ONE, name="张三")
    service = DigitalEmployeeService()
    employee = asyncio.run(service.create_employee("tenant-a", {"name": "小礼"}, "admin-a"))
    asyncio.run(service.replace_assignments(
        "tenant-a", employee["id"], {"roleIds": ["role-1"], "userIds": [USER_ONE]}, "admin-a",
    ))
    assert db.digital_employee_assignments.rows

    asyncio.run(service.delete_employee("tenant-a", employee["id"]))

    assert employee["id"] not in db.digital_employees.rows
    assert db.digital_employee_assignments.rows == {}

    with pytest.raises(HTTPException) as missing:
        asyncio.run(service.delete_employee("tenant-a", employee["id"]))
    assert missing.value.status_code == 404


def test_employee_token_usage_groups_usage_per_employee(db, monkeypatch):
    monkeypatch.setattr(analytics_routes, "get_db", lambda: db)
    service = DigitalEmployeeService()
    used = asyncio.run(service.create_employee("tenant-a", {"name": "小礼"}, "admin-a"))
    asyncio.run(service.create_employee("tenant-a", {"name": "小智"}, "admin-a"))  # no usage at all

    seed_usage(db, "u1", employee_id=used["id"], prompt=100, completion=40, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    seed_usage(db, "u2", employee_id=used["id"], prompt=50, completion=10, model="gpt-5.2", created_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
    seed_usage(db, "u3", employee_id="")  # never an employee session, must not group
    seed_usage(db, "u4", employee_id="deletedemployee0")  # usage outlives a deleted employee
    seed_usage(db, "u5", main_id="tenant-b", employee_id=used["id"], prompt=999, completion=999)  # invisible cross-tenant

    payload = asyncio.run(analytics_routes.employee_token_usage(
        current_user={"main_id": "tenant-a"}, departmentId="", modelName="",
    ))
    items = payload["items"]
    # Sorted by total tokens; the no-usage employee, the empty-id rows and the
    # other tenant's rows never show up.
    assert [item["employeeId"] for item in items] == [used["id"], "deletedemployee0"]

    first = items[0]
    assert first["employeeName"] == "小礼"
    assert first["employeeStatus"] == "active"
    assert first["calls"] == 2
    assert first["totalTokens"] == 200
    assert first["promptTokens"] == 150
    assert first["completionTokens"] == 50
    assert first["lastCalledAt"] == "2026-01-02T00:00:00Z"

    deleted = items[1]
    assert deleted["employeeStatus"] == ""
    assert deleted["employeeName"] == f"已删除数字员工 {('deletedemployee0')[:8]}"
    assert deleted["totalTokens"] == 0


def test_employee_token_usage_supports_model_and_department_filters(db, monkeypatch):
    monkeypatch.setattr(analytics_routes, "get_db", lambda: db)
    service = DigitalEmployeeService()
    used = asyncio.run(service.create_employee("tenant-a", {"name": "小礼"}, "admin-a"))
    seed_usage(db, "u1", employee_id=used["id"], model="deepseek-v4-flash", prompt=100, completion=0)
    seed_usage(db, "u2", employee_id=used["id"], model="gpt-5.2", prompt=30, completion=0)

    by_model = asyncio.run(analytics_routes.employee_token_usage(
        current_user={"main_id": "tenant-a"}, departmentId="", modelName="gpt-5.2",
    ))
    assert [item["employeeId"] for item in by_model["items"]] == [used["id"]]
    assert by_model["items"][0]["totalTokens"] == 30

    db.end_user_org_relations.rows["rel-1"] = {"main_id": "tenant-a", "org_id": "dept-x", "user_id": USER_ONE}
    by_department = asyncio.run(analytics_routes.employee_token_usage(
        current_user={"main_id": "tenant-a"}, departmentId="dept-x", modelName="",
    ))
    assert [item["employeeId"] for item in by_department["items"]] == [used["id"]]

    # A department without members short-circuits to an empty grouping.
    empty = asyncio.run(analytics_routes.employee_token_usage(
        current_user={"main_id": "tenant-a"}, departmentId="dept-none", modelName="",
    ))
    assert empty == {"items": []}

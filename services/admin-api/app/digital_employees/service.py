from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
import uuid

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from .constants import (
    AVATAR_URL_MAX_LENGTH,
    DESCRIPTION_MAX_LENGTH,
    EMPLOYEE_STATUSES,
    MANIFESTO_MAX_LENGTH,
    NAME_MAX_LENGTH,
)
from .repository import DigitalEmployeeRepository, utcnow


def serialize_employee(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("_id") or ""),
        "name": str(row.get("name") or ""),
        "description": str(row.get("description") or ""),
        "avatarUrl": str(row.get("avatar_url") or ""),
        "manifesto": str(row.get("manifesto") or ""),
        "skillIds": [str(item) for item in (row.get("skill_ids") or [])],
        "toolBindings": [
            {
                "toolId": str(binding.get("tool_id") or ""),
                "enabledToolNames": [str(name) for name in (binding.get("enabled_tool_names") or [])],
            }
            for binding in (row.get("tool_bindings") or [])
            if isinstance(binding, dict)
        ],
        "bindingHealth": empty_binding_health(),
        "status": str(row.get("status") or "active"),
        "isPublic": bool(row.get("is_public")),
        # Assignment subjects (availability = 公开 或 指派); filled from
        # digital_employee_assignments rows by _attach_assignments.
        "roleIds": [],
        "userIds": [],
        "createdBy": str(row.get("created_by") or ""),
        "updatedBy": str(row.get("updated_by") or ""),
        "createdAt": _iso(row.get("created_at")),
        "updatedAt": _iso(row.get("updated_at")),
    }


def empty_binding_health() -> dict[str, Any]:
    return {"missingSkillIds": [], "missingToolIds": [], "missingToolNames": []}


def _skill_is_organization(row: dict[str, Any]) -> bool:
    """Mirror the runtime's skill scoping: explicit private visibility or a
    personal owner make a skill personal; organization skills carry
    `visibility: "organization"`, `user_id: "organization"`, or (the
    admin-console norm) no visibility/owner fields at all."""
    visibility = str(row.get("visibility") or "").strip().lower()
    if visibility == "organization":
        return True
    if visibility in {"private", "user"}:
        return False
    owner = str(row.get("user_id") or "").strip()
    return not owner or owner == "organization"


def _skill_is_bindable(row: dict[str, Any] | None) -> bool:
    """A skill may be bound when it exists, is organization-visible and enabled."""
    if not row or not _skill_is_organization(row):
        return False
    enabled = row.get("enabled")
    if enabled is None:
        enabled = row.get("is_active")
    if enabled is None:
        return True
    if isinstance(enabled, bool):
        return enabled
    return str(enabled).strip().lower() in {"true", "1", "yes", "on"}


def _tool_is_bindable(row: dict[str, Any] | None) -> bool:
    """A tool may be bound when it exists (organization scope, enforced by the
    repository query) and is active."""
    if not row:
        return False
    return str(row.get("status") or "").strip().lower() == "active"


def _tool_type(row: dict[str, Any]) -> str:
    return str(row.get("type") or "http").strip().lower()


def _discovered_tool_names(row: dict[str, Any]) -> set[str]:
    """Names the MCP server currently exposes.

    Stored discovered_tools entries keep their "name" key unescaped (only keys
    starting with $ or containing dots are escaped), so raw extraction is safe.
    """
    names: set[str] = set()
    for item in row.get("discovered_tools") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            names.add(name)
    return names


def _iso(value: Any) -> str:
    if not isinstance(value, datetime):
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _skill_ids(value: Any) -> list[str]:
    items = value if isinstance(value, list) else []
    return list(dict.fromkeys(str(item).strip() for item in items if str(item).strip()))


def _subject_ids(value: Any) -> list[str]:
    """Deduplicated, order-preserving assignment subject ids."""
    return _skill_ids(value)


def _tool_bindings(value: Any) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else []
    bindings: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        tool_id = str(item.get("toolId") or item.get("tool_id") or "").strip()
        if not tool_id:
            continue
        names = item.get("enabledToolNames")
        if names is None:
            names = item.get("enabled_tool_names")
        enabled = names if isinstance(names, list) else []
        bindings[tool_id] = {
            "tool_id": tool_id,
            "enabled_tool_names": list(dict.fromkeys(str(name).strip() for name in enabled if str(name).strip())),
        }
    return list(bindings.values())


class DigitalEmployeeService:
    def __init__(self, repository: DigitalEmployeeRepository | None = None) -> None:
        self.repository = repository or DigitalEmployeeRepository()

    async def list_employees(self, main_id: str, status: str = "", q: str = "") -> list[dict[str, Any]]:
        if status and status not in EMPLOYEE_STATUSES:
            raise HTTPException(status_code=400, detail="状态筛选不合法")
        rows = await self.repository.list(main_id, status=status, q=(q or "").strip())
        employees = [serialize_employee(row) for row in rows]
        await self._attach_binding_health(main_id, employees)
        await self._attach_assignments(main_id, employees)
        return employees

    async def create_employee(self, main_id: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        document = self._document(payload)
        await self._validate_bindings(main_id, document)
        await self._ensure_unique_name(main_id, document["name"])
        now = utcnow()
        document.update({
            "_id": uuid.uuid4().hex,
            "main_id": main_id,
            "created_by": actor,
            "updated_by": actor,
            "created_at": now,
            "updated_at": now,
        })
        try:
            await self.repository.insert(document)
        except DuplicateKeyError as exc:
            # The unique (main_id, name) index is the authority under races.
            raise HTTPException(status_code=409, detail="数字员工名称已存在") from exc
        return serialize_employee(document)

    async def get_employee(self, main_id: str, employee_id: str) -> dict[str, Any]:
        row = await self.repository.find_one(main_id, employee_id)
        if not row:
            raise HTTPException(status_code=404, detail="数字员工不存在")
        employee = serialize_employee(row)
        await self._attach_binding_health(main_id, [employee])
        await self._attach_assignments(main_id, [employee])
        return employee

    async def update_employee(self, main_id: str, employee_id: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        existing = await self.repository.find_one(main_id, employee_id)
        if not existing:
            raise HTTPException(status_code=404, detail="数字员工不存在")
        patch = self._document(payload)
        await self._validate_bindings(main_id, patch)
        await self._ensure_unique_name(main_id, patch["name"], exclude_employee_id=employee_id)
        patch["updated_by"] = actor
        patch["updated_at"] = utcnow()
        try:
            await self.repository.update(main_id, employee_id, patch)
        except DuplicateKeyError as exc:
            # The unique (main_id, name) index is the authority under races.
            raise HTTPException(status_code=409, detail="数字员工名称已存在") from exc
        return serialize_employee({**existing, **patch})

    async def replace_assignments(
        self, main_id: str, employee_id: str, payload: dict[str, Any], actor: str
    ) -> dict[str, Any]:
        """REPLACE-ALL assignment semantics (docs/digital-employee.md, Assign
        availability): the employee ends up assigned to exactly the requested
        position roles and users. Idempotent; the read side sees the change
        immediately (指派即时生效)."""
        existing = await self.repository.find_one(main_id, employee_id)
        if not existing:
            raise HTTPException(status_code=404, detail="数字员工不存在")
        role_ids = _subject_ids(payload.get("roleIds"))
        user_ids = _subject_ids(payload.get("userIds"))
        await self._validate_assignment_subjects(main_id, role_ids, user_ids)

        current_rows = await self.repository.list_assignments(main_id, [employee_id])
        current: dict[tuple[str, str], dict[str, Any]] = {
            (str(row.get("subject_type") or ""), str(row.get("subject_id") or "")): row
            for row in current_rows
            if row.get("subject_id")
        }
        wanted = [("role", role_id) for role_id in role_ids] + [("user", user_id) for user_id in user_ids]
        # Diff instead of rewrite: untouched rows keep their created_by/created_at.
        to_remove = [key for key in current if key not in set(wanted)]
        to_add = [key for key in wanted if key not in current]
        await self.repository.delete_assignments(main_id, employee_id, to_remove)
        if to_add:
            now = utcnow()
            await self.repository.insert_assignments([
                {
                    "_id": uuid.uuid4().hex,
                    "main_id": main_id,
                    "digital_employee_id": employee_id,
                    "subject_type": subject_type,
                    "subject_id": subject_id,
                    "created_by": actor,
                    "created_at": now,
                }
                for subject_type, subject_id in to_add
            ])
        return {"id": employee_id, "roleIds": role_ids, "userIds": user_ids}

    async def delete_employee(self, main_id: str, employee_id: str) -> None:
        """Physical deletion, guarded by session references
        (docs/digital-employee.md, Errors): a referenced employee must be
        disabled instead, a zero-reference one deletes together with its
        assignment rows."""
        existing = await self.repository.find_one(main_id, employee_id)
        if not existing:
            raise HTTPException(status_code=404, detail="数字员工不存在")
        sessions = await self.repository.count_employee_sessions(main_id, employee_id)
        if sessions > 0:
            raise HTTPException(status_code=409, detail="该数字员工仍有会话引用，请先停用")
        await self.repository.delete_all_assignments(main_id, employee_id)
        await self.repository.delete(main_id, employee_id)

    async def _ensure_unique_name(self, main_id: str, name: str, exclude_employee_id: str = "") -> None:
        if await self.repository.find_by_name(main_id, name, exclude_employee_id):
            raise HTTPException(status_code=409, detail="数字员工名称已存在")

    async def _validate_bindings(self, main_id: str, document: dict[str, Any]) -> None:
        """Save-time reference validation: every bound id must be usable by all
        future speakers of the employee, so personal or disabled references are
        rejected before anything is persisted."""
        skill_ids = document["skill_ids"]
        if skill_ids:
            rows = await self.repository.find_skills(main_id, skill_ids)
            by_id = {str(row.get("_id") or ""): row for row in rows}
            invalid = [skill_id for skill_id in skill_ids if not _skill_is_bindable(by_id.get(skill_id))]
            if invalid:
                raise HTTPException(
                    status_code=400,
                    detail=f"以下技能不存在、已停用或非企业技能，无法绑定：{'、'.join(invalid)}",
                )
        bindings = document["tool_bindings"]
        if not bindings:
            return
        tool_ids = [binding["tool_id"] for binding in bindings]
        rows = await self.repository.find_tools(main_id, tool_ids)
        by_id = {str(row.get("_id") or ""): row for row in rows}
        invalid_tools = [binding["tool_id"] for binding in bindings if not _tool_is_bindable(by_id.get(binding["tool_id"]))]
        if invalid_tools:
            raise HTTPException(
                status_code=400,
                detail=f"以下工具不存在、已停用或非企业工具，无法绑定：{'、'.join(invalid_tools)}",
            )
        for binding in bindings:
            tool = by_id[binding["tool_id"]]
            if _tool_type(tool) != "mcp":
                continue  # HTTP tools ignore enabledToolNames.
            names = binding["enabled_tool_names"]
            if not names:
                continue  # Empty list means no narrowing: all of the tool's enabled tools.
            unknown = [name for name in names if name not in _discovered_tool_names(tool)]
            if unknown:
                tool_name = str(tool.get("name") or binding["tool_id"])
                raise HTTPException(
                    status_code=400,
                    detail=f"工具「{tool_name}」不存在以下 MCP 子工具，无法绑定：{'、'.join(unknown)}",
                )

    async def _validate_assignment_subjects(self, main_id: str, role_ids: list[str], user_ids: list[str]) -> None:
        """Assignments are grants (docs/digital-employee.md, Availability
        rules): roles must exist and stay active, users must exist in the
        tenant; anything else is rejected with the offenders listed."""
        if role_ids:
            rows = await self.repository.find_active_roles(main_id, role_ids)
            active_ids = {str(row.get("_id") or "") for row in rows}
            invalid = [role_id for role_id in role_ids if role_id not in active_ids]
            if invalid:
                raise HTTPException(
                    status_code=400,
                    detail=f"以下岗位角色不存在或已停用，无法指派：{'、'.join(invalid)}",
                )
        if user_ids:
            rows = await self.repository.find_users(main_id, user_ids)
            found_ids = {str(row.get("_id") or "") for row in rows}
            invalid = [user_id for user_id in user_ids if user_id not in found_ids]
            if invalid:
                raise HTTPException(
                    status_code=400,
                    detail=f"以下用户不存在，无法指派：{'、'.join(invalid)}",
                )

    async def _attach_assignments(self, main_id: str, employees: list[dict[str, Any]]) -> None:
        """Fill roleIds/userIds from the assignment rows with one batched
        query per page; employees without assignments keep the empty lists
        without touching the database."""
        employee_ids = [str(employee.get("id") or "") for employee in employees if employee.get("id")]
        if not employee_ids:
            return
        rows = await self.repository.list_assignments(main_id, employee_ids)
        grouped: dict[str, dict[str, list[str]]] = {}
        for row in rows:
            employee_id = str(row.get("digital_employee_id") or "")
            subject_type = str(row.get("subject_type") or "")
            subject_id = str(row.get("subject_id") or "")
            if not subject_id:
                continue
            bucket = grouped.setdefault(employee_id, {"roleIds": [], "userIds": []})
            key = "roleIds" if subject_type == "role" else "userIds"
            if subject_id not in bucket[key]:
                bucket[key].append(subject_id)
        for employee in employees:
            bucket = grouped.get(str(employee.get("id") or ""), {})
            employee["roleIds"] = list(bucket.get("roleIds") or [])
            employee["userIds"] = list(bucket.get("userIds") or [])

    async def _attach_binding_health(self, main_id: str, employees: list[dict[str, Any]]) -> None:
        """Fill in bindingHealth for serialized employees with at most one batched
        query per referenced collection; employees without bindings keep the
        default empty health without touching the database."""
        skill_ids: list[str] = []
        tool_ids: list[str] = []
        for employee in employees:
            for skill_id in employee.get("skillIds") or []:
                if skill_id not in skill_ids:
                    skill_ids.append(skill_id)
            for binding in employee.get("toolBindings") or []:
                tool_id = str(binding.get("toolId") or "")
                if tool_id and tool_id not in tool_ids:
                    tool_ids.append(tool_id)
        if not skill_ids and not tool_ids:
            return
        skill_rows = await self.repository.find_skills(main_id, skill_ids) if skill_ids else []
        tool_rows = await self.repository.find_tools(main_id, tool_ids) if tool_ids else []
        skills_by_id = {str(row.get("_id") or ""): row for row in skill_rows}
        tools_by_id = {str(row.get("_id") or ""): row for row in tool_rows}
        for employee in employees:
            employee["bindingHealth"] = self._binding_health(employee, skills_by_id, tools_by_id)

    @staticmethod
    def _binding_health(
        employee: dict[str, Any],
        skills_by_id: dict[str, dict[str, Any]],
        tools_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        missing_skill_ids = [
            skill_id for skill_id in employee.get("skillIds") or []
            if not _skill_is_bindable(skills_by_id.get(skill_id))
        ]
        missing_tool_ids: list[str] = []
        missing_tool_names: list[dict[str, Any]] = []
        for binding in employee.get("toolBindings") or []:
            tool_id = str(binding.get("toolId") or "")
            tool = tools_by_id.get(tool_id)
            if not _tool_is_bindable(tool):
                missing_tool_ids.append(tool_id)
                continue
            if _tool_type(tool) != "mcp":
                continue
            discovered = _discovered_tool_names(tool)
            names = [name for name in binding.get("enabledToolNames") or [] if name not in discovered]
            if names:
                missing_tool_names.append({"toolId": tool_id, "names": names})
        return {
            "missingSkillIds": missing_skill_ids,
            "missingToolIds": missing_tool_ids,
            "missingToolNames": missing_tool_names,
        }

    @staticmethod
    def _document(payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="数字员工名称不能为空")
        if len(name) > NAME_MAX_LENGTH:
            raise HTTPException(status_code=400, detail=f"数字员工名称不能超过 {NAME_MAX_LENGTH} 个字符")
        description = str(payload.get("description") or "").strip()
        if len(description) > DESCRIPTION_MAX_LENGTH:
            raise HTTPException(status_code=400, detail=f"描述不能超过 {DESCRIPTION_MAX_LENGTH} 个字符")
        avatar_url = str(payload.get("avatarUrl") or "").strip()
        if avatar_url:
            if len(avatar_url) > AVATAR_URL_MAX_LENGTH:
                raise HTTPException(status_code=400, detail=f"头像 URL 不能超过 {AVATAR_URL_MAX_LENGTH} 个字符")
            parsed = urlparse(avatar_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise HTTPException(status_code=400, detail="头像 URL 必须是合法的 http(s) 地址")
        manifesto = payload.get("manifesto")
        manifesto = "" if manifesto is None else str(manifesto)
        if len(manifesto) > MANIFESTO_MAX_LENGTH:
            raise HTTPException(status_code=400, detail=f"宣言不能超过 {MANIFESTO_MAX_LENGTH} 个字符")
        employee_status = str(payload.get("status") or "active")
        if employee_status not in EMPLOYEE_STATUSES:
            raise HTTPException(status_code=400, detail="状态只能是启用或停用")
        return {
            "name": name,
            "description": description,
            "avatar_url": avatar_url,
            "manifesto": manifesto,
            "skill_ids": _skill_ids(payload.get("skillIds")),
            "tool_bindings": _tool_bindings(payload.get("toolBindings")),
            "status": employee_status,
            "is_public": bool(payload.get("isPublic")),
        }

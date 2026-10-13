from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import re

from bson import ObjectId

from app.core.db import get_db
from app.services.organization_tools import organization_tool_query
from .constants import (
    ASSIGNMENT_COLLECTION,
    CHAT_SESSIONS_COLLECTION,
    DIGITAL_EMPLOYEE_COLLECTION,
    END_USERS_COLLECTION,
    EXTERNAL_TOOLS_COLLECTION,
    POSITION_ROLES_COLLECTION,
    SKILLS_COLLECTION,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DigitalEmployeeRepository:
    def __init__(self, db: Any | None = None) -> None:
        self.db = db or get_db()

    async def ensure_indexes(self) -> None:
        await self.db[DIGITAL_EMPLOYEE_COLLECTION].create_index(
            [("main_id", 1), ("name", 1)], unique=True, name="unique_main_employee_name"
        )
        await self.db[DIGITAL_EMPLOYEE_COLLECTION].create_index(
            [("main_id", 1), ("status", 1)], name="employees_by_main_status"
        )
        # Assignment indexes: the names and keys must stay identical to
        # chat-api's (app/dsh_runtime/digital_employees.py) so both services
        # idempotently ensure the same indexes against the shared database
        # (docs/digital-employee.md, Operator notes, Indexes).
        await self.db[ASSIGNMENT_COLLECTION].create_index(
            [("main_id", 1), ("digital_employee_id", 1), ("subject_type", 1), ("subject_id", 1)],
            unique=True,
            name="unique_employee_assignment",
        )
        await self.db[ASSIGNMENT_COLLECTION].create_index(
            [("main_id", 1), ("subject_type", 1), ("subject_id", 1)],
            name="assignments_by_subject",
        )
        await self.db[ASSIGNMENT_COLLECTION].create_index(
            [("main_id", 1), ("digital_employee_id", 1)],
            name="assignments_by_employee",
        )

    async def list(self, main_id: str, status: str = "", q: str = "") -> list[dict[str, Any]]:
        query: dict[str, Any] = {"main_id": main_id}
        if status:
            query["status"] = status
        if q:
            pattern = {"$regex": re.escape(q), "$options": "i"}
            query["$or"] = [{"name": pattern}, {"description": pattern}]
        cursor = self.db[DIGITAL_EMPLOYEE_COLLECTION].find(query).sort("updated_at", -1)
        return await cursor.to_list(length=1000)

    async def find_one(self, main_id: str, employee_id: str) -> dict[str, Any] | None:
        row = await self.db[DIGITAL_EMPLOYEE_COLLECTION].find_one({"_id": employee_id, "main_id": main_id})
        return dict(row) if row else None

    async def find_by_name(self, main_id: str, name: str, exclude_employee_id: str = "") -> dict[str, Any] | None:
        query: dict[str, Any] = {"main_id": main_id, "name": name}
        if exclude_employee_id:
            query["_id"] = {"$ne": exclude_employee_id}
        row = await self.db[DIGITAL_EMPLOYEE_COLLECTION].find_one(query, {"_id": 1})
        return dict(row) if row else None

    async def insert(self, document: dict[str, Any]) -> None:
        await self.db[DIGITAL_EMPLOYEE_COLLECTION].insert_one(document)

    async def update(self, main_id: str, employee_id: str, patch: dict[str, Any]) -> None:
        await self.db[DIGITAL_EMPLOYEE_COLLECTION].update_one(
            {"_id": employee_id, "main_id": main_id}, {"$set": patch}
        )

    async def find_skills(self, main_id: str, skill_ids: list[str]) -> list[dict[str, Any]]:
        """One batched $in lookup for bound skill rows (organization skills collection)."""
        if not skill_ids:
            return []
        cursor = self.db[SKILLS_COLLECTION].find({"main_id": main_id, "_id": {"$in": skill_ids}})
        return await cursor.to_list(length=len(skill_ids))

    async def find_tools(self, main_id: str, tool_ids: list[str]) -> list[dict[str, Any]]:
        """One batched $in lookup for bound external tools, organization scope only.

        Personal (scope=user) tools intentionally do not match, so a binding that
        points at one reads back as a missing reference.
        """
        if not tool_ids:
            return []
        cursor = self.db[EXTERNAL_TOOLS_COLLECTION].find(organization_tool_query(main_id, _id={"$in": tool_ids}))
        return await cursor.to_list(length=len(tool_ids))

    async def list_assignments(self, main_id: str, employee_ids: list[str]) -> list[dict[str, Any]]:
        """Assignment rows for the given employees, oldest first for stable UI order."""
        if not employee_ids:
            return []
        cursor = (
            self.db[ASSIGNMENT_COLLECTION]
            .find({"main_id": main_id, "digital_employee_id": {"$in": employee_ids}})
            .sort("created_at", 1)
        )
        return await cursor.to_list(length=2000)

    async def insert_assignments(self, documents: list[dict[str, Any]]) -> None:
        if documents:
            await self.db[ASSIGNMENT_COLLECTION].insert_many(documents)

    async def delete_assignments(self, main_id: str, employee_id: str, keys: list[tuple[str, str]]) -> None:
        """Delete the (subject_type, subject_id) pairs listed for one employee."""
        if not keys:
            return
        await self.db[ASSIGNMENT_COLLECTION].delete_many({
            "main_id": main_id,
            "digital_employee_id": employee_id,
            "$or": [{"subject_type": subject_type, "subject_id": subject_id} for subject_type, subject_id in keys],
        })

    async def delete_all_assignments(self, main_id: str, employee_id: str) -> None:
        await self.db[ASSIGNMENT_COLLECTION].delete_many(
            {"main_id": main_id, "digital_employee_id": employee_id}
        )

    async def find_active_roles(self, main_id: str, role_ids: list[str]) -> list[dict[str, Any]]:
        """Position roles that exist and are active; assignments to anything
        else are dangling grants the runtime would never match."""
        if not role_ids:
            return []
        cursor = self.db[POSITION_ROLES_COLLECTION].find(
            {"main_id": main_id, "_id": {"$in": role_ids}, "status": "active"}
        )
        return await cursor.to_list(length=len(role_ids))

    async def find_users(self, main_id: str, user_ids: list[str]) -> list[dict[str, Any]]:
        """One batched lookup of tenant end users.

        end_users stores ObjectId _id values while assignments keep the string
        form (the id space chat-api matches against), so valid hex strings are
        converted; an id that is not a valid ObjectId cannot exist.
        """
        object_ids = [ObjectId(user_id) for user_id in user_ids if ObjectId.is_valid(user_id)]
        if not object_ids:
            return []
        cursor = self.db[END_USERS_COLLECTION].find({"main_id": main_id, "_id": {"$in": object_ids}})
        return await cursor.to_list(length=len(object_ids))

    async def count_employee_sessions(self, main_id: str, employee_id: str) -> int:
        """Sessions bound to the employee in the shared tenant database."""
        return int(await self.db[CHAT_SESSIONS_COLLECTION].count_documents(
            {"main_id": main_id, "digital_employee_id": employee_id}
        ))

    async def delete(self, main_id: str, employee_id: str) -> None:
        await self.db[DIGITAL_EMPLOYEE_COLLECTION].delete_one({"_id": employee_id, "main_id": main_id})

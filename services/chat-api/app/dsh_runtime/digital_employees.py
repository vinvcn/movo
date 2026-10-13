"""Tenant-scoped reads of admin-managed digital employees.

The ``digital_employees`` and ``digital_employee_assignments`` collections
are written by admin-api (profile CRUD and assignments). This module is
chat-api's read side: the user-side availability list, session-creation
admission, and the runtime-profile compile source. Field names are frozen
by ``docs/digital-employee.md`` (Operator notes, Storage surface) and later
tickets consume them, so they must not drift.
"""

from __future__ import annotations

from typing import Any


class DigitalEmployeeNotFoundError(LookupError):
    """Existence-avoiding denial (the endpoint maps this to 404).

    Carries no detail on purpose: an unknown id, a cross-tenant id, and an
    id the caller may not use are indistinguishable, mirroring the shared
    session denial semantics.
    """


class DigitalEmployeeDisabledError(RuntimeError):
    """A bound session's employee is disabled; the next send is refused.

    The session's history stays readable; only continuing the conversation
    is blocked (docs/digital-employee.md, Errors).
    """


class DigitalEmployeeRepository:
    def __init__(self, db: Any) -> None:
        self._db = db
        self._employees = db.digital_employees
        self._assignments = db.digital_employee_assignments

    async def ensure_indexes(self) -> None:
        """Assignment indexes (docs/digital-employee.md, Operator notes).

        The unique index makes a duplicate assignment (same employee and
        the same subject) impossible even under admin races.
        """
        await self._assignments.create_index(
            [("main_id", 1), ("digital_employee_id", 1), ("subject_type", 1), ("subject_id", 1)],
            unique=True,
            name="unique_employee_assignment",
        )
        await self._assignments.create_index(
            [("main_id", 1), ("subject_type", 1), ("subject_id", 1)],
            name="assignments_by_subject",
        )
        await self._assignments.create_index(
            [("main_id", 1), ("digital_employee_id", 1)],
            name="assignments_by_employee",
        )

    async def get(self, tenant_id: str, employee_id: str) -> dict[str, Any] | None:
        if not employee_id:
            return None
        return await self._employees.find_one({"_id": employee_id, "main_id": tenant_id})

    async def load_for_profile(self, tenant_id: str, employee_id: str) -> dict[str, Any]:
        """Load an employee for runtime-profile compilation.

        Unknown or cross-tenant ids raise the existence-avoiding NotFound.
        A disabled employee raises the explicit Disabled error instead:
        the session is already bound to it, and the sender deserves the
        real reason rather than a misleading 404.
        """
        row = await self.get(tenant_id, employee_id)
        if row is None:
            raise DigitalEmployeeNotFoundError("digital_employee_not_found")
        if str(row.get("status") or "") != "active":
            raise DigitalEmployeeDisabledError("digital_employee_disabled")
        return row

    async def _active_role_ids(self, tenant_id: str, user_id: str) -> list[str]:
        """The user's position roles that still exist and are active.

        Dangling assignments - a role that was deleted or disabled - never
        match (docs/digital-employee.md, Availability rules).
        """
        if not user_id:
            return []
        rows = await self._db.end_user_position_roles.find(
            {"main_id": tenant_id, "user_id": user_id}
        ).to_list(length=100)
        role_ids = [str(row.get("role_id") or "") for row in rows if row.get("role_id")]
        if not role_ids:
            return []
        active = await self._db.position_roles.find(
            {"main_id": tenant_id, "_id": {"$in": role_ids}, "status": "active"},
            {"_id": 1},
        ).to_list(length=100)
        return [str(row.get("_id") or "") for row in active]

    @staticmethod
    def _assignment_or_clause(user_id: str, role_ids: list[str]) -> list[dict[str, Any]]:
        clause: list[dict[str, Any]] = [{"subject_type": "user", "subject_id": user_id}]
        if role_ids:
            clause.append({"subject_type": "role", "subject_id": {"$in": role_ids}})
        return clause

    async def admit_for_new_session(
        self, tenant_id: str, employee_id: str, user_id: str
    ) -> dict[str, Any] | None:
        """Availability at session creation; None means "not usable".

        docs/digital-employee.md, Availability rules: active AND (public OR
        an assignment matching the user directly or through one of their
        active position roles). A non-usable employee stays
        indistinguishable from a missing one.
        """
        row = await self.get(tenant_id, employee_id)
        if row is None:
            return None
        if str(row.get("status") or "") != "active":
            return None
        if bool(row.get("is_public")):
            return row
        if not user_id:
            return None
        role_ids = await self._active_role_ids(tenant_id, user_id)
        assignment = await self._assignments.find_one(
            {
                "main_id": tenant_id,
                "digital_employee_id": employee_id,
                "$or": self._assignment_or_clause(user_id, role_ids),
            }
        )
        return row if assignment is not None else None

    async def list_available(self, tenant_id: str, user_id: str = "") -> list[dict[str, Any]]:
        """The user-side picker list; mirrors admit_for_new_session exactly."""
        rows = await self._employees.find(
            {"main_id": tenant_id, "status": "active"},
            {"name": 1, "description": 1, "avatar_url": 1, "is_public": 1},
        ).to_list(length=200)
        allowed_private: set[str] = set()
        private_ids = [
            str(row.get("_id") or "") for row in rows if not bool(row.get("is_public"))
        ]
        if private_ids and user_id:
            role_ids = await self._active_role_ids(tenant_id, user_id)
            cursor = self._assignments.find(
                {
                    "main_id": tenant_id,
                    "digital_employee_id": {"$in": private_ids},
                    "$or": self._assignment_or_clause(user_id, role_ids),
                },
                {"digital_employee_id": 1},
            )
            async for row in cursor:
                allowed_private.add(str(row.get("digital_employee_id") or ""))
        visible = [
            row
            for row in rows
            if bool(row.get("is_public")) or str(row.get("_id") or "") in allowed_private
        ]
        visible.sort(key=lambda row: str(row.get("name") or ""))
        return [
            {
                "id": str(row.get("_id") or ""),
                "name": str(row.get("name") or ""),
                "description": str(row.get("description") or ""),
                "avatar_url": str(row.get("avatar_url") or "") or None,
            }
            for row in visible
        ]

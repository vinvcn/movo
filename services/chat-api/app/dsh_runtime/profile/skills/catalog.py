"""Read mutable Skill control-plane rows without using the legacy executor."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from app.services.org_skill_adapter import organization_skill_adapter
from app.services.skills import user_skill_service
from app.core.db import get_db
from app.product.resource_access import filter_allowed_resource_ids

logger = logging.getLogger(__name__)


class SkillCatalog(Protocol):
    async def list_enabled(self, tenant_id: str, user_id: str) -> list[dict[str, Any]]: ...
    async def list_bound_organization(
        self, tenant_id: str, skill_ids: list[str]
    ) -> list[dict[str, Any]]: ...


class MongoSkillCatalog:
    async def list_enabled(self, tenant_id: str, user_id: str) -> list[dict[str, Any]]:
        personal = await user_skill_service.list_skills(user_id, main_id=tenant_id)
        organization = await organization_skill_adapter.list_runtime_skills(main_id=tenant_id)
        allowed_organization_ids = await filter_allowed_resource_ids(
            "skill",
            main_id=tenant_id,
            user_id=user_id,
            resource_ids=(str(item.get("id") or "") for item in organization if isinstance(item, dict)),
        )
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in [*personal, *organization]:
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("id") or "").strip()
            if not source_id or source_id in seen:
                continue
            if item.get("enabled", item.get("is_active", True)) is False:
                continue
            is_organization = str(item.get("visibility") or "").lower() == "organization" or str(item.get("source") or "") == "org_db"
            if is_organization and source_id not in allowed_organization_ids:
                continue
            seen.add(source_id)
            rows.append(dict(item))
        await self._attach_runtime_bundles(tenant_id, rows)
        return rows

    async def list_bound_organization(
        self, tenant_id: str, skill_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Employee bundle mode (docs/digital-employee.md): EXACTLY these
        organization skills, granted by assignment - no personal skills, no
        per-user visibility narrowing. The control plane stores raw
        ``skills._id`` values while the runtime adapter exposes
        ``org_skill:<mongo-id>``, so both forms are accepted.

        Missing or disabled ids are skipped with a warning (reference
        hygiene), never a hard failure.
        """
        wanted = {str(item).strip() for item in skill_ids if str(item).strip()}
        if not wanted:
            return []
        organization = await organization_skill_adapter.list_runtime_skills(main_id=tenant_id)
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        matched: set[str] = set()
        for item in organization:
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("id") or "").strip()
            raw_id = source_id.removeprefix("org_skill:")
            if raw_id not in wanted:
                continue
            if not source_id or source_id in seen:
                continue
            if item.get("enabled", item.get("is_active", True)) is False:
                continue
            matched.add(raw_id)
            seen.add(source_id)
            rows.append(dict(item))
        missing = wanted - matched
        if missing:
            logger.warning(
                "employee_bundle_skill_references_missing tenant_id=%s missing=%s",
                tenant_id,
                ",".join(sorted(missing)),
            )
        await self._attach_runtime_bundles(tenant_id, rows)
        return rows

    @staticmethod
    async def _attach_runtime_bundles(tenant_id: str, rows: list[dict[str, Any]]) -> None:
        package_ids = [str(item.get("package_id") or "") for item in rows if item.get("package_id")]
        if package_ids:
            packages = await get_db().skill_packages.find({
                "_id": {"$in": package_ids}, "main_id": tenant_id,
            }).to_list(length=len(package_ids))
            by_id = {str(item.get("_id") or ""): item for item in packages}
            for row in rows:
                package = by_id.get(str(row.get("package_id") or ""))
                if package:
                    resource_files = [
                        item for item in package.get("files") or []
                        if str(item.get("path") or "") not in {"SKILL.md", "_meta.json"}
                    ]
                    if resource_files:
                        row["runtime_bundle_base64"] = str(package.get("archive_base64") or "")
                        row["runtime_bundle_root"] = str(package.get("root_prefix") or "")

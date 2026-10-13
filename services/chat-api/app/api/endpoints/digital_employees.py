"""User-side digital-employee availability surface (session picker).

Read-only. The list mirrors the session-creation admission predicate
exactly (docs/digital-employee.md, Availability rules), so what the
picker offers is what the server accepts.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.principal import ApiPrincipal, require_api_principal
from app.core.tenant import resolve_main_id
from app.core.db import get_db
from app.dsh_runtime.digital_employees import DigitalEmployeeRepository

router = APIRouter(dependencies=[Depends(require_api_principal)])


@router.get("/digital-employees/available")
async def available_digital_employees(
    main_id: str = Query(default="default"),
    principal: ApiPrincipal = Depends(require_api_principal),
) -> dict[str, Any]:
    is_end_user = isinstance(principal, ApiPrincipal) and principal.kind == "end_user"
    if is_end_user:
        tenant_id = principal.main_id
    else:
        tenant_id = resolve_main_id(main_id)
    user_id = str(principal.user_id) if is_end_user else ""
    employees = await DigitalEmployeeRepository(get_db()).list_available(tenant_id, user_id)
    return {"code": 0, "data": employees}

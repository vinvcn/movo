from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_current_admin_user
from app.digital_employees.repository import DigitalEmployeeRepository
from app.digital_employees.service import DigitalEmployeeService

router = APIRouter()


class ToolBindingPayload(BaseModel):
    toolId: str = ""
    enabledToolNames: list[str] = Field(default_factory=list)


class DigitalEmployeePayload(BaseModel):
    # Field constraints are validated in the service layer so admins get a
    # clear Chinese 400 message instead of FastAPI's generic 422 response.
    name: str = ""
    description: str = ""
    avatarUrl: str = ""
    manifesto: str = ""
    skillIds: list[str] = Field(default_factory=list)
    toolBindings: list[ToolBindingPayload] = Field(default_factory=list)
    status: str = "active"
    isPublic: bool = False


class AssignmentPayload(BaseModel):
    # REPLACE-ALL semantics for this employee; validated in the service layer.
    roleIds: list[str] = Field(default_factory=list)
    userIds: list[str] = Field(default_factory=list)


def _main_id(user: dict[str, Any]) -> str:
    return str(user.get("main_id") or "default")


def _actor(user: dict[str, Any]) -> str:
    return str(user.get("username") or user.get("_id") or "admin")


@router.get("")
async def list_employees(
    status: str = "",
    q: str = "",
    current_user: dict = Depends(get_current_admin_user),
) -> list[dict[str, Any]]:
    return await DigitalEmployeeService().list_employees(_main_id(current_user), status=status, q=q)


@router.post("", status_code=201)
async def create_employee(
    payload: DigitalEmployeePayload,
    current_user: dict = Depends(get_current_admin_user),
) -> dict[str, Any]:
    return await DigitalEmployeeService().create_employee(_main_id(current_user), payload.model_dump(), _actor(current_user))


@router.get("/{employee_id}")
async def get_employee(employee_id: str, current_user: dict = Depends(get_current_admin_user)) -> dict[str, Any]:
    return await DigitalEmployeeService().get_employee(_main_id(current_user), employee_id)


@router.put("/{employee_id}")
async def update_employee(
    employee_id: str,
    payload: DigitalEmployeePayload,
    current_user: dict = Depends(get_current_admin_user),
) -> dict[str, Any]:
    return await DigitalEmployeeService().update_employee(
        _main_id(current_user), employee_id, payload.model_dump(), _actor(current_user)
    )


@router.put("/{employee_id}/assignments")
async def replace_assignments(
    employee_id: str,
    payload: AssignmentPayload,
    current_user: dict = Depends(get_current_admin_user),
) -> dict[str, Any]:
    return await DigitalEmployeeService().replace_assignments(
        _main_id(current_user), employee_id, payload.model_dump(), _actor(current_user)
    )


@router.delete("/{employee_id}", status_code=204)
async def delete_employee(
    employee_id: str,
    current_user: dict = Depends(get_current_admin_user),
) -> None:
    await DigitalEmployeeService().delete_employee(_main_id(current_user), employee_id)


async def ensure_indexes() -> None:
    await DigitalEmployeeRepository().ensure_indexes()

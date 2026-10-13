"""Employee bundle mode: the exact skills and tools of a bound session (#43).

The semantics under test (docs/digital-employee.md, "The bundle at runtime"):

- In an employee session the external tools and skills are EXACTLY the
  employee's bound set - never intersected with the speaker's visibility,
  never unioned with their personal resources.
- The five platform capability gates stay attached to the speaker: an
  employee cannot grant account powers (the capability floor).
- Dangling references are skipped with a warning, not a hard failure.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.dsh_runtime.profile.compiler import ModelProfileCompiler
from app.dsh_runtime.profile.skills import catalog as skills_catalog_module
from app.dsh_runtime.profile.skills.catalog import MongoSkillCatalog
from app.dsh_runtime.profile.skills.compiler import SkillProfileCompiler
from app.dsh_runtime.profile.tools import ToolProfileCompiler

TENANT = "tenant-employee-bundle"


def _mcp_descriptor(tool_id: str, names: list[str]) -> dict[str, Any]:
    return {
        "id": tool_id,
        "name": f"mcp-{tool_id}",
        "type": "mcp",
        "description": "MCP tool",
        "usageHint": "",
        "tags": [],
        "config": {"approvalRequired": False},
        "inputSchema": [],
        "outputSchema": [],
        "resultPath": "",
        "discoveredTools": [
            {"name": name, "description": f"{name} tool", "inputSchema": {"type": "object"}}
            for name in names
        ],
    }


class RecordingToolCatalog:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.enabled_rows: list[dict[str, Any]] = []
        self.bound_rows: list[dict[str, Any]] = []

    async def list_enabled(self, tenant_id: str, user_id: str) -> list[dict[str, Any]]:
        self.calls.append(("list_enabled", tenant_id, user_id))
        return self.enabled_rows

    async def list_bound(
        self, tenant_id: str, bindings: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        self.calls.append(("list_bound", tenant_id, tuple(map(str, bindings))))
        return self.bound_rows


class _Policy:
    def __init__(self, denied: set[str]) -> None:
        self.denied = denied

    async def resolve(self, tenant_id: str, user_id: str) -> Any:
        denied = self.denied
        return SimpleNamespace(allows_internal=lambda ref: ref not in denied)


class FakeInternalCatalog:
    """One internal capability: the browser tool."""

    async def list_enabled(self, tenant_id: str, user_id: str) -> list[Any]:
        return [
            SimpleNamespace(
                capability_ref="browser.task@v1", tool_name="browser_task", version="v1",
                display_name="Browser", description="Browse the web",
                input_schema={"type": "object"}, output_schema={}, output_validation="none",
                risk_level="write", approval_required=False, approval_argument="",
                approval_values=(), required_scopes=(), timeout_ms=1000, timeout_mode="fixed",
                inactivity_timeout_ms=0, cancellable=True, idempotent=True,
                delivery_mode="model_synthesized", consumes_execution_evidence=False,
            )
        ]


def _tool_names(tools: tuple[Any, ...]) -> set[str]:
    return {tool.name for tool in tools}


def test_bundle_mode_compiles_exactly_the_bound_external_tools() -> None:
    catalog = RecordingToolCatalog()
    catalog.enabled_rows = [_mcp_descriptor("tool-personal", ["personal_tool"])]
    catalog.bound_rows = [_mcp_descriptor("tool-gift", ["gift_search", "gift_supplier"])]
    compiler = ToolProfileCompiler(
        catalog, FakeInternalCatalog(), _Policy(denied=set()),
    )

    tools = asyncio.run(
        compiler.compile(
            tenant_id=TENANT, user_id="speaker-a",
            employee_tool_bindings=[{"tool_id": "tool-gift", "enabled_tool_names": []}],
        )
    )
    # Exactly the bound MCP tools plus the speaker's allowed internal
    # capability - the speaker's personal tool never appears.
    assert "askai_mcp_tool_gift_search" not in _tool_names(tools)  # names are hashed below
    names = _tool_names(tools)
    assert len([n for n in names if "gift_search" in n]) == 1
    assert len([n for n in names if "gift_supplier" in n]) == 1
    assert len([n for n in names if "personal_tool" in n]) == 0
    assert "browser_task" in names
    assert catalog.calls[0][0] == "list_bound"


def test_bundle_mode_keeps_the_capability_floor_per_speaker() -> None:
    catalog = RecordingToolCatalog()
    catalog.bound_rows = [_mcp_descriptor("tool-gift", ["gift_search"])]
    compiler = ToolProfileCompiler(
        catalog, FakeInternalCatalog(), _Policy(denied={"browser.task@v1"}),
    )
    tools = asyncio.run(
        compiler.compile(
            tenant_id=TENANT, user_id="speaker-a",
            employee_tool_bindings=[{"tool_id": "tool-gift", "enabled_tool_names": []}],
        )
    )
    names = _tool_names(tools)
    # The employee's bound MCP tool is granted by assignment...
    assert len([n for n in names if "gift_search" in n]) == 1
    # ...but it cannot grant the speaker a capability their role denies.
    assert "browser_task" not in names


def test_non_employee_mode_is_unchanged() -> None:
    catalog = RecordingToolCatalog()
    catalog.enabled_rows = [_mcp_descriptor("tool-personal", ["personal_tool"])]
    compiler = ToolProfileCompiler(
        catalog, FakeInternalCatalog(), _Policy(denied=set()),
    )
    tools = asyncio.run(compiler.compile(tenant_id=TENANT, user_id="speaker-a"))
    names = _tool_names(tools)
    assert len([n for n in names if "personal_tool" in n]) == 1
    assert "browser_task" in names
    assert catalog.calls[0][0] == "list_enabled"


class RecordingSkillCatalog:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.enabled_rows: list[dict[str, Any]] = []
        self.bound_rows: list[dict[str, Any]] = []

    async def list_enabled(self, tenant_id: str, user_id: str) -> list[dict[str, Any]]:
        self.calls.append(("list_enabled", tenant_id, user_id))
        return self.enabled_rows

    async def list_bound_organization(
        self, tenant_id: str, skill_ids: list[str]
    ) -> list[dict[str, Any]]:
        self.calls.append(("list_bound_organization", tenant_id, tuple(skill_ids)))
        return self.bound_rows


def _skill_row(source_id: str, name: str) -> dict[str, Any]:
    return {
        "id": source_id,
        "name": name,
        "skill_markdown": f"Instructions for {name}.",
        "description": f"{name} skill",
        "visibility": "organization",
        "source": "org_db",
        "enabled": True,
    }


def test_skill_bundle_mode_compiles_only_bound_organization_skills() -> None:
    catalog = RecordingSkillCatalog()
    catalog.enabled_rows = [_skill_row("personal-1", "我的私有大纲")]
    catalog.bound_rows = [_skill_row("org_skill:skill-gift", "礼品话术")]
    compiler = SkillProfileCompiler(catalog)

    profile = asyncio.run(
        compiler.compile(
            tenant_id=TENANT, user_id="speaker-a", tools=(),
            employee_skill_ids=["skill-gift"],
        )
    )
    assert [skill.display_name for skill in profile.skills] == ["礼品话术"]
    assert profile.writing_styles == ()
    assert catalog.calls[0][0] == "list_bound_organization"
    # Empty bundle = pure-persona employee: no skills at all.
    catalog.bound_rows = []
    empty = asyncio.run(
        compiler.compile(
            tenant_id=TENANT, user_id="speaker-a", tools=(),
            employee_skill_ids=[],
        )
    )
    assert empty.skills == ()
    assert empty.writing_styles == ()


def test_skill_non_employee_mode_is_unchanged() -> None:
    catalog = RecordingSkillCatalog()
    catalog.enabled_rows = [_skill_row("personal-1", "我的私有大纲")]
    compiler = SkillProfileCompiler(catalog)
    profile = asyncio.run(
        compiler.compile(tenant_id=TENANT, user_id="speaker-a", tools=())
    )
    assert [skill.display_name for skill in profile.skills] == ["我的私有大纲"]
    assert catalog.calls[0][0] == "list_enabled"


class FakeEmployeeCatalog:
    def __init__(self, employee: dict[str, Any]) -> None:
        self.employee = employee

    async def load_for_profile(self, tenant_id: str, employee_id: str) -> dict[str, Any]:
        return self.employee


class RecordingSubToolCompiler:
    def __init__(self) -> None:
        self.seen: dict[str, Any] = {}

    async def compile(self, *, tenant_id: str, user_id: str,
                      employee_tool_bindings=None) -> tuple:
        self.seen["employee_tool_bindings"] = employee_tool_bindings
        return ()


class RecordingSubSkillCompiler:
    def __init__(self) -> None:
        self.seen: dict[str, Any] = {}

    async def compile(self, *, tenant_id: str, user_id: str, tools,
                      employee_skill_ids=None):
        self.seen["employee_skill_ids"] = employee_skill_ids
        return None


class StaticModelCatalog:
    async def resolve(self, tenant_id: str, model_instance_id: str | None):
        return (
            {
                "_id": "model-a", "main_id": tenant_id, "provider_id": "provider-a",
                "model_name": "deepseek-chat", "display_name": "Model", "status": "active",
                "capabilities": ["chat"], "settings": {"max_output_tokens": 4096},
            },
            {"_id": "provider-a", "name": "Provider", "provider_type": "openai_compatible",
             "status": "active"},
        )


def test_model_compiler_threads_the_bundle_into_sub_compilers() -> None:
    tools = RecordingSubToolCompiler()
    skills = RecordingSubSkillCompiler()
    employee = {
        "_id": "emp-1",
        "name": "小礼",
        "manifesto": "你是礼品协作者。",
        "status": "active",
        "skill_ids": ["skill-gift", "skill-wrap"],
        "tool_bindings": [
            {"tool_id": "tool-gift", "enabled_tool_names": ["gift_search"]},
            {"tool_id": "tool-supplier", "enabled_tool_names": []},
        ],
    }
    compiler = ModelProfileCompiler(
        StaticModelCatalog(), tools, skills,
        employee_catalog=FakeEmployeeCatalog(employee),
    )
    snapshot = asyncio.run(
        compiler.compile(tenant_id=TENANT, user_id="speaker-a", digital_employee_id="emp-1")
    )
    assert snapshot.digital_employee is not None
    assert tools.seen["employee_tool_bindings"] == employee["tool_bindings"]
    assert skills.seen["employee_skill_ids"] == ["skill-gift", "skill-wrap"]

    # Without an employee the sub-compilers see explicit None (legacy mode).
    plain = asyncio.run(
        compiler.compile(tenant_id=TENANT, user_id="speaker-a")
    )
    assert plain.digital_employee is None
    assert tools.seen["employee_tool_bindings"] is None
    assert skills.seen["employee_skill_ids"] is None


def test_mongo_skill_catalog_maps_org_ids_and_warns_on_missing(monkeypatch, caplog) -> None:
    class FakeAdapter:
        async def list_runtime_skills(self, *, main_id: str) -> list[dict[str, Any]]:
            return [
                _skill_row("org_skill:skill-gift", "礼品话术"),
                {**_skill_row("org_skill:skill-dead", "已停用技能"), "enabled": False},
                _skill_row("org_skill:skill-other", "未绑定技能"),
            ]

    monkeypatch.setattr(
        skills_catalog_module, "organization_skill_adapter", FakeAdapter(),
    )
    catalog = MongoSkillCatalog()
    rows = asyncio.run(
        catalog.list_bound_organization(TENANT, ["skill-gift", "skill-dead", "skill-missing"])
    )
    assert [row["id"] for row in rows] == ["org_skill:skill-gift"]
    # Reference hygiene: the disabled and the unknown ids are reported, not fatal.
    assert "skill-dead" in caplog.text
    assert "skill-missing" in caplog.text
    assert "skill-other" not in caplog.text

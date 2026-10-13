# Digital-employee acceptance walkthrough — 小礼 (ticket #46, 2026-10-09)

Evidence has two layers: the pytest suites that pin each step's semantics
(run green in this worktree), and the Playwright UI walkthrough
(`qa/digital-employee.spec.ts`) that drives the same script through the
real admin console and the real user console against the QA stack.

## How the UI walkthrough was run

```bash
# from the repo root; images tagged :qa-employee are built from THIS worktree
docker compose -p qa-employee -f docker-compose.yml -f docker-compose.override.yml \
  -f qa/docker-compose.qa-stub.yml -f qa/docker-compose.qa-employee.yml up -d --build

# one-time seed: the setup wizard. `model.providerId` must be the id of an
# ACTIVE provider of THIS install (provider ids are per-install), so resolve it
# from the pre-auth setup endpoint and inject it before posting.
PROVIDER_ID=$(curl -s http://127.0.0.1:3101/admin-api/api/setup/model-providers | jq -r '.[0].id')
jq --arg id "$PROVIDER_ID" '.model.providerId=$id' qa/digital-employee-setup.json > /tmp/qa-setup.json
curl -X POST http://127.0.0.1:3101/admin-api/api/setup/initialize \
  -H 'Content-Type: application/json' --data @/tmp/qa-setup.json

cd qa && npm install && npx playwright install chromium
EVIDENCE_ROOT=<absolute dir outside the worktree> \
MOVO_QA_BASE_URL=http://127.0.0.1:3101 \
  npx playwright test digital-employee.spec.ts
```

Stack: gateway on `127.0.0.1:3101`, model = `qa/qa_model_stub.py` SSE stub.
Data is isolated in the dedicated `movo_qa_employee` logical database plus
Redis db 1 (`qa/docker-compose.qa-employee.yml`); the dev `gragentic`
database is never touched. The spec is idempotent (duplicate-create 409s are
treated as success), so it re-runs against the same stack.

> 本仓库基线说明：上面的命令是本记录最初运行时用的栈（私有开发库，那时已有
> `docker-compose.override.yml` 与共享中间件拓扑）。本仓库基线没有该文件，
> mongo/redis/weaviate 由本仓库 compose 自带，因此复跑时去掉
> `-f docker-compose.override.yml`，其余不变；overlay 的专用数据库与 Redis db 1
> 变量照常生效。

## Result (2026-10-09, this worktree)

**1 passed (42.4s).** Evidence captures (written under EVIDENCE_ROOT, outside
the worktree): `digital-employee-admin-console.png`,
`digital-employee-picker.png`, `digital-employee-shared-session.png`,
`digital-employee-disabled.png`, `digital-employee-delete-conflict.png`.

## The script, step by step

| # | 步骤 | 实际 | 证据 |
| --- | --- | --- | --- |
| 1 | 建小礼：宣言 + 绑定技能 + 绑定工具 + 指派角色、不公开 | 管理台创建成功，字段校验/引用健康检查在位 | UI: spec 管理台段 + `digital-employee-admin-console.png`；pytest: `services/admin-api/tests/test_digital_employee_service.py` |
| 2 | 角色成员可见、未指派不可见 | picker 对 qa-emp-assigned 显示小礼、对 qa-emp-outsider 隐藏（UI + API 双探针） | UI: `digital-employee-picker.png`；pytest: `test_digital_employee_session.py::test_repository_availability_predicates` |
| 3 | 员工会话：身份与精确 bundle | 首条消息带 `digitalEmployeeId` 落库，会话头显示小礼身份徽标，工具/技能恰为绑定集合 | UI: spec 会话段；pytest: `test_digital_employee_session.py`（快照段/哈希/host payload）+ `test_digital_employee_bundle.py`（精确集合） |
| 4 | 分享给未指派用户：可读可说、同一员工 | 分享链接加入后发言成功，双方看到同一身份与历史 | UI: `digital-employee-shared-session.png`；pytest: `test_digital_employee_shared_session.py::test_unassigned_participant_speaks_and_shares_the_same_bundle` |
| 5 | 能力门按发言者生效 | 编译层断言：员工不能授予浏览器等内建能力 | pytest: `test_digital_employee_bundle.py::test_bundle_mode_keeps_the_capability_floor_per_speaker` |
| 6 | 改宣言下一轮生效 | 员工段进 content_hash，manifesto 变更产生新 profile 版本并轮换 | pytest: `test_employee_segment_participates_in_snapshot_hash_and_host_payload` + 共享会话轮换断言 |
| 7 | 停用/启用 | 停用后下一发送 409 呈现文案「该数字员工已停用，本会话暂无法继续；历史记录仍可查看」，历史可读；重新启用恢复 | UI: `digital-employee-disabled.png`；pytest: `test_disabled_employee_blocks_the_next_turn_through_profile_sync` |
| 8 | 删除约束 | 有会话引用时删除 409 冲突在对话框呈现、行保留 | UI: `digital-employee-delete-conflict.png`；pytest: admin delete tests（409 有引用 / 零引用可删） |
| 9 | is_public 全员可见 | 公开员工出现在无指派用户的可用列表 | pytest: `test_repository_availability_predicates` |
| 10 | 成本按员工归属 | 网关 token 携带 employee id → token_usage_logs 记录 → 管理端员工用量卡片 | pytest: `test_model_gateway_employee_attribution.py` + admin `employee-token-usage` tests；UI: 管理台分析页卡片 |

## Environment boundary

The QA model is the SSE stub: it cannot follow the manifesto, so model
**self-identification**（小礼自述）is deliberately NOT asserted by the spec —
it is the one manual checklist item that needs a live model. Everything
else is asserted at the UI/transport level, plus the pytest layers above.

## Cleanup

```bash
docker compose -p qa-employee down        # stops the QA project only
# optional: drop the dedicated QA database; use the $MOVO_TEST_MONGODB_URI
# exported for the test run above (never paste credentials into docs)
#   mongosh "$MOVO_TEST_MONGODB_URI" --eval 'db.getSiblingDB("movo_qa_employee").dropDatabase()'
```

The `:qa-employee` images stay in the local docker cache; they are QA-only
tags and never used by the shipped compose files.

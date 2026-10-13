/**
 * Ticket #46 — digital-employee end-to-end acceptance walkthrough, 小礼 scenario.
 *
 * Runs against the seeded QA stack (`qa-employee`, gateway on 127.0.0.1:3101):
 *   CONTEXT ADMIN      = seeded admin from `qa/digital-employee-setup.json`
 *   CONTEXT ASSIGNED   = a member of the internal gift role (fixture user)
 *   CONTEXT UNASSIGNED = a tenant user with no assignment for the employee
 *
 * The scenario walks the 小礼 story end to end through the REAL consoles:
 *   1. the admin console creates the digital employee 小礼 (manifesto, one
 *      bound organization skill, one bound HTTP organization tool, not
 *      public, assigned to the gift position role);
 *   2. the new-chat composer's digital-employee picker offers 小礼 to the
 *      role member and hides it from the unassigned user (UI + API probe);
 *   3. a session started with 小礼 carries the employee id on the wire
 *      (`digitalEmployeeId`), replies through the stub model, and shows the
 *      小礼 identity chip in the session header;
 *   4. the session is shared; the unassigned user joins and talks to the SAME
 *      employee (chip + both speakers' turns on both sides);
 *   5. 小礼 is disabled in the admin console: the bound session's NEXT send is
 *      refused with the pinned 409 copy 该数字员工已停用… while the history
 *      stays readable; re-enabling restores sending;
 *   6. deleting the referenced employee surfaces the 409 conflict message in
 *      the delete dialog and the row survives.
 *
 * Environment boundary (same class of note as the T12 spec): the QA model is
 * the `qa_model_stub.py` SSE stub, which cannot follow the manifesto. Model
 * SELF-identification (小礼自述) is therefore deliberately NOT asserted here —
 * that needs a live model and stays a manual checklist item. This spec keeps
 * every assertion at the UI/transport level: the identity chip, the request
 * contract, the surfaced error copy, and the session behavior.
 *
 * Hygiene contract (mirrors `qa/session-sharing-realtime.spec.ts`): every
 * artifact path is an ABSOLUTE `path.join(process.env.EVIDENCE_ROOT, ...)`
 * value, so nothing Playwright writes from this file can land in
 * `qa/test-results/` or `qa/playwright-report/`.
 *
 * Idempotency (re-runnable against the same stack, T12 precedent): the
 * fixture seeding (roles, skill, tool, users) and the create-employee step
 * all treat a duplicate 409 as SUCCESS and resolve the existing row's id
 * through the real admin API, so the walkthrough can be re-run as-is. The
 * disabled → re-enable → delete-refused tail is likewise repeatable.
 *
 * Fixture split (T12 precedent: fixtures through the real admin API, the
 * FEATURE surface through the real console): the gift role, the counter-role
 * user, the organization skill and the HTTP organization tool are created
 * through the real admin API with the 409-as-success rule; the digital
 * employee 小礼 itself — the feature under walkthrough — is created, edited,
 * disabled, re-enabled and delete-attempted through the REAL admin console.
 */
import { test, expect, type Browser, type Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

import { ADMIN_LOCALE_RESET } from './playwright.config';

const EVIDENCE_ROOT = process.env.EVIDENCE_ROOT || '';
if (!EVIDENCE_ROOT) {
  throw new Error('EVIDENCE_ROOT must be exported before Playwright loads this spec (T12 pinned step 0)');
}

const BASE_URL = process.env.MOVO_QA_BASE_URL || 'http://127.0.0.1:3101';
const ADMIN_URL = process.env.ADMIN_URL || `${BASE_URL}/admin`;
const RAW_API = `${BASE_URL}/askai-api/api`;
const ADMIN_API = `${BASE_URL}/admin-api/api`;

/** Stack seed (qa/digital-employee-setup.json): the setup-wizard admin account. */
const ADMIN = { username: 'qa-employee-admin', password: 'qa-employee-admin-pass-2026' };

/** Holds the gift role → sees 小礼. */
const ASSIGNED = {
  username: 'qa-emp-assigned',
  password: 'qa-emp-assigned-pass-2026',
  name: 'QA 礼物岗位成员',
  mobile: '13911000001',
  email: 'qa.emp.assigned@example.com',
};
/** Holds only the seeded system role → must NOT see 小礼. */
const UNASSIGNED = {
  username: 'qa-emp-outsider',
  password: 'qa-emp-outsider-pass-2026',
  name: 'QA 未指派成员',
  mobile: '13911000002',
  email: 'qa.emp.outsider@example.com',
};

const EMPLOYEE_NAME = '小礼';
const EMPLOYEE_DESCRIPTION = 'QA 验收数字员工（礼物场景）';
const MANIFESTO = '你是礼物挑选助理小礼。收到请求后先确认预算与对象，再给出三档礼物建议。';
const ROLE_NAME = 'QA 礼物岗位角色';
const SKILL_NAME = 'QA 礼物技能';
const TOOL_NAME = 'QA 礼物清单工具';

const COMPOSER = 'textarea[placeholder="有问题，尽管问"]';
const STUB_REPLY = 'qa-stub stream ok';
const MSG_A = '#46 小礼验收：第一句来自岗位成员';
const MSG_B = '#46 小礼验收：共享成员发言';
const MSG_DISABLED = '#46 小礼验收：停用后的发送';
const MSG_RESTORED = '#46 小礼验收：重新启用后的发送';
/** The pinned server copy for a bound session whose employee was disabled (dsh_chat.py). */
const DISABLED_ERROR = '该数字员工已停用，本会话暂无法继续；历史记录仍可查看';
/** The pinned admin-api copy for deleting a referenced employee (service.py). */
const DELETE_CONFLICT = '该数字员工仍有会话引用，请先停用';

function log(message: string): void {
  console.log(`[#46] ${message}`);
}

function evidencePath(name: string): string {
  return path.join(EVIDENCE_ROOT, name);
}

/**
 * Compose several real browser captures into ONE evidence file (side by side).
 * Pure Playwright: the captures are rendered as data URLs and re-screenshotted,
 * so no image-processing dependency is introduced. (T12 helper, verbatim.)
 */
async function captureSideBySide(
  browser: Browser,
  name: string,
  panels: { buffer: Buffer; label: string }[],
  viewport: { width: number; height: number },
): Promise<number> {
  const composer = await browser.newPage({ viewport });
  try {
    const cards = panels
      .map(
        (panel) => `
      <figure>
        <figcaption>${panel.label}</figcaption>
        <img src="data:image/png;base64,${panel.buffer.toString('base64')}" />
      </figure>`,
      )
      .join('');
    await composer.setContent(
      `<!doctype html><html><head><meta charset="utf-8" />
      <style>
        body { margin: 0; background: #0f172a; font: 13px/1.4 system-ui, sans-serif; color: #e2e8f0; }
        .row { display: flex; gap: 8px; padding: 8px; align-items: flex-start; }
        figure { margin: 0; flex: 1 1 0; min-width: 0; }
        figcaption { padding: 4px 0; font-weight: 600; }
        img { display: block; width: 100%; height: auto; border: 1px solid #334155; }
      </style></head><body><div class="row">${cards}</div></body></html>`,
      { waitUntil: 'load' },
    );
    const target = evidencePath(name);
    await composer.screenshot({ path: target, fullPage: true });
    const size = fs.statSync(target).size;
    log(`captured ${name} -> ${size} bytes (side-by-side)`);
    expect(size, `${name} must be non-trivial`).toBeGreaterThan(10_000);
    return size;
  } finally {
    await composer.close();
  }
}

// ---------------------------------------------------------------------------
// Real-API helpers (Node side, T12 precedent: the REAL admin API for fixtures)
// ---------------------------------------------------------------------------

type JsonObject = { [key: string]: unknown };

function asRecord(value: unknown): JsonObject {
  return typeof value === 'object' && value !== null ? (value as JsonObject) : {};
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

async function apiFetch(
  url: string,
  options: { method?: string; token?: string; body?: unknown } = {},
): Promise<Response> {
  const headers: Record<string, string> = {};
  if (options.token) headers.Authorization = `Bearer ${options.token}`;
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  return await fetch(url, {
    method: options.method ?? (options.body !== undefined ? 'POST' : 'GET'),
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });
}

async function apiAdminLogin(): Promise<string> {
  const response = await apiFetch(`${ADMIN_API}/auth/login`, { body: ADMIN });
  expect(response.ok, `admin login -> HTTP ${response.status}`).toBe(true);
  const token = asString(asRecord(await response.json()).token);
  expect(token, 'admin login must return a token').not.toBe('');
  return token;
}

async function apiEndUserLogin(username: string, password: string): Promise<string> {
  const response = await apiFetch(`${RAW_API}/auth/login`, { body: { username, password } });
  expect(response.ok, `end-user login for ${username} -> HTTP ${response.status}`).toBe(true);
  const payload = asRecord(await response.json());
  expect(asNumber(payload.code), `login ${username} code`).toBe(0);
  const token = asString(asRecord(payload.data).token);
  expect(token, `login ${username} must return a token`).not.toBe('');
  return token;
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' ? value : null;
}

/**
 * Create-or-reuse a fixture through the real admin API. LOOKUP FIRST: unlike
 * users and roles, skills and tools do not enforce unique names, so a blind
 * create would mint a duplicate row on every re-run. When no row matches the
 * create runs (a 409 duplicate, e.g. a role name, is idempotent SUCCESS), and
 * the row's id is then resolved from the collection listing.
 */
async function ensureFixture(
  label: string,
  create: { url: string; body: unknown },
  listUrl: string,
  matches: (row: JsonObject) => boolean,
): Promise<JsonObject> {
  const token = await apiAdminLogin();
  const listResponse = await apiFetch(listUrl, { token });
  expect(listResponse.ok, `list for ${label} -> HTTP ${listResponse.status}`).toBe(true);
  const existing = asArray(await listResponse.json()).map(asRecord).find(matches);
  if (existing) {
    log(`fixture ${label}: already exists (reused)`);
    return existing;
  }
  const created = await apiFetch(create.url, { token, body: create.body });
  expect([201, 409], `create ${label} -> HTTP ${created.status}`).toContain(created.status);
  if (created.status === 201) log(`fixture ${label}: created`);
  else log(`fixture ${label}: create answered 409 (idempotent success)`);
  const relistResponse = await apiFetch(listUrl, { token });
  expect(relistResponse.ok, `relist for ${label} -> HTTP ${relistResponse.status}`).toBe(true);
  const rows = asArray(await relistResponse.json()).map(asRecord);
  const row = rows.find(matches);
  expect(row, `${label} must resolve to one row after create-or-reuse`).toBeTruthy();
  return row as JsonObject;
}

type OrgFixtures = {
  departmentId: string;
  giftRoleId: string;
  otherRoleId: string;
  skillId: string;
  toolId: string;
};

/**
 * Seed the walkthrough fixtures through the real admin API (roles, users,
 * organization skill, HTTP organization tool). Everything is 409-idempotent,
 * so the spec re-runs cleanly against the same stack.
 */
async function seedOrgFixtures(): Promise<OrgFixtures> {
  const token = await apiAdminLogin();

  // A root department must exist for user creation (the setup wizard seeds one).
  const treeResponse = await apiFetch(`${ADMIN_API}/directory/departments/tree`, { token });
  expect(treeResponse.ok, `department tree -> HTTP ${treeResponse.status}`).toBe(true);
  let departments = asArray(await treeResponse.json()).map(asRecord);
  if (departments.length === 0) {
    const created = await apiFetch(`${ADMIN_API}/directory/departments`, {
      token,
      body: { name: 'QA 数字员工部', status: 'active' },
    });
    expect(created.ok, `department create -> HTTP ${created.status}`).toBe(true);
    departments = asArray(await (await apiFetch(`${ADMIN_API}/directory/departments/tree`, { token })).json()).map(
      asRecord,
    );
  }
  const departmentId = asString(departments[0]?.id);
  expect(departmentId, 'a root department must exist').not.toBe('');

  // The gift role: full platform capabilities, so the capability floor does not
  // interfere with the stub turn (the floor itself is pytest-covered).
  const giftRole = await ensureFixture(
    `role ${ROLE_NAME}`,
    {
      url: `${ADMIN_API}/position-roles`,
      body: {
        name: ROLE_NAME,
        description: 'QA 验收：可使用数字员工小礼的岗位角色',
        status: 'active',
        capabilities: {
          content_generation: true,
          image_generation: true,
          code_generation: true,
          browser_automation: true,
          internal_knowledge: true,
        },
      },
    },
    `${ADMIN_API}/position-roles`,
    (row) => asString(row.name) === ROLE_NAME,
  );

  // The counter-role for the unassigned user: the seeded system role (never the
  // gift role), so the user is a normal tenant member without 小礼 access.
  const rolesResponse = await apiFetch(`${ADMIN_API}/position-roles`, { token });
  expect(rolesResponse.ok, `position roles -> HTTP ${rolesResponse.status}`).toBe(true);
  const roles = asArray(await rolesResponse.json()).map(asRecord);
  const otherRole =
    roles.find((role) => asString(role.status) === 'active' && asString(role.id) !== asString(giftRole.id)) ??
    roles[0];
  expect(otherRole, 'an active non-gift position role must exist').toBeTruthy();

  // Organization skill (binding validation only allows enabled org skills).
  const skill = await ensureFixture(
    `skill ${SKILL_NAME}`,
    {
      url: `${ADMIN_API}/skills`,
      body: {
        name: SKILL_NAME,
        description: 'QA 验收：小礼绑定的企业技能',
        scenario: '礼物挑选',
        type: 'ordinary',
        config: {},
        enabled: true,
      },
    },
    `${ADMIN_API}/skills`,
    (row) => asString(row.name) === SKILL_NAME,
  );

  // Organization HTTP tool: created disabled by the API, then activated.
  const tool = await ensureFixture(
    `tool ${TOOL_NAME}`,
    {
      url: `${ADMIN_API}/tools`,
      body: {
        name: TOOL_NAME,
        type: 'http',
        description: 'QA 验收：小礼绑定的企业 HTTP 工具',
        usageHint: '查询礼物清单',
        tags: ['qa'],
        status: 'active',
        config: { baseUrl: 'http://qa-model-stub:8200', timeoutSeconds: 15 },
      },
    },
    `${ADMIN_API}/tools`,
    (row) => asString(row.name) === TOOL_NAME,
  );
  const toolId = asString(tool.id);
  if (asString(tool.status) !== 'active') {
    const activated = await apiFetch(`${ADMIN_API}/tools/${toolId}`, {
      token,
      method: 'PATCH',
      body: { status: 'active' },
    });
    expect(activated.ok, `activate tool -> HTTP ${activated.status}`).toBe(true);
  }

  // The two test users: one holding the gift role, one holding only the
  // counter-role (409 on a duplicate loginName is idempotent success, T12 rule).
  for (const [user, roleId] of [
    [ASSIGNED, asString(giftRole.id)],
    [UNASSIGNED, asString(otherRole.id)],
  ] as const) {
    const response = await apiFetch(`${ADMIN_API}/directory/users`, {
      token,
      body: {
        name: user.name,
        mobile: user.mobile,
        email: user.email,
        loginName: user.username,
        initialPassword: user.password,
        primaryDepartmentId: departmentId,
        departmentIds: [departmentId],
        primaryRoleId: roleId,
        roleIds: [roleId],
      },
    });
    expect([201, 409], `create user ${user.username} -> HTTP ${response.status}`).toContain(response.status);
    log(`fixture user ${user.username}: ${response.status === 201 ? 'created' : 'already existed (409 ok)'}`);
  }

  return {
    departmentId,
    giftRoleId: asString(giftRole.id),
    otherRoleId: asString(otherRole.id),
    skillId: asString(skill.id),
    toolId,
  };
}

/** GET one admin-api collection row as a JSON record. */
async function apiGetRecord(url: string, token: string): Promise<JsonObject> {
  const response = await apiFetch(url, { token });
  expect(response.ok, `GET ${url} -> HTTP ${response.status}`).toBe(true);
  return asRecord(await response.json());
}

// ---------------------------------------------------------------------------
// Real-UI helpers (selectors verified against the feat/employee tree)
// ---------------------------------------------------------------------------

async function loginAdmin(page: Page): Promise<void> {
  await page.goto(`${ADMIN_URL}/login`);
  await page.getByPlaceholder('请输入账号').fill(ADMIN.username);
  await page.getByPlaceholder('输入密码').fill(ADMIN.password);
  await page.getByRole('button', { name: '登录后台' }).click();
  // Single-tenant deployment: straight to the dashboard; tenant picker handled defensively.
  const tenantOption = page.locator('.tenant-option').first();
  await Promise.race([
    page.waitForURL('**/admin/dashboard', { timeout: 30_000 }),
    tenantOption.waitFor({ state: 'visible', timeout: 30_000 }).catch(() => undefined),
  ]);
  if (await tenantOption.isVisible().catch(() => false)) {
    await tenantOption.click();
  }
  await page.waitForURL('**/admin/dashboard', { timeout: 30_000 });
  log('admin signed in at /admin/dashboard');
}

/** Fill the login modal WITHOUT navigating (the share token lives in memory only). */
async function fillUserWebLogin(page: Page, username: string, password: string): Promise<void> {
  const usernameInput = page.locator('#movo-login-username');
  await expect(usernameInput).toBeVisible({ timeout: 30_000 });
  await usernameInput.fill(username);
  await page.locator('#movo-login-password').fill(password);
  await page.locator('button[type="submit"]').click();
  await expect(usernameInput).toBeHidden({ timeout: 30_000 });
}

async function loginUser(page: Page, username: string, password: string): Promise<void> {
  await page.goto('/');
  await fillUserWebLogin(page, username, password);
  await expect(page.getByRole('button', { name: '新建对话' })).toBeVisible({ timeout: 30_000 });
  log(`user-web signed in as ${username}`);
}

/**
 * CONTEXT UNASSIGNED redeems the share link through the real UI. An anonymous
 * visitor gets the login modal on the SAME URL (the token lives in memory
 * only); an ALREADY-AUTHENTICATED context (this spec logs the unassigned user
 * in earlier for the picker-absence check) auto-joins at startup with no modal
 * (App.vue: "or on an already-logged-in startup"). Both paths must end with
 * the joined session open under 分享给我.
 */
async function redeemShare(page: Page, shareUrl: string, username: string, password: string): Promise<void> {
  await page.goto(shareUrl);
  const usernameInput = page.locator('#movo-login-username');
  const loginModal = await usernameInput
    .waitFor({ state: 'visible', timeout: 15_000 })
    .then(() => true)
    .catch(() => false);
  if (loginModal) {
    await fillUserWebLogin(page, username, password);
  } else {
    log(`share link opened by the already-authenticated ${username}; joining without a login modal`);
  }
  await expect(page.getByText('分享给我').first()).toBeVisible({ timeout: 30_000 });
  log('share link redeemed; session joined and opened');
}

/** Open the share dialog via the header and create the link through the real UI (T12 helper). */
async function createShareLink(page: Page): Promise<string> {
  await page.locator('[aria-label="分享会话"]').click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible({ timeout: 15_000 });
  const responsePromise = page.waitForResponse(
    (response) =>
      /\/askai-api\/api\/sessions\/[^/]+\/share$/.test(response.url()) &&
      response.request().method() === 'POST',
    { timeout: 30_000 },
  );
  await dialog.getByRole('button', { name: '生成分享链接' }).click();
  const response = await responsePromise;
  const linkInput = dialog.locator('input[readonly]').first();
  await expect(linkInput).toHaveValue(/session-share=/, { timeout: 15_000 });
  const shareUrl = await linkInput.inputValue();
  expect(shareUrl).toContain('session-share=');
  await dialog.getByRole('button', { name: '关闭' }).click();
  await expect(dialog).toBeHidden({ timeout: 15_000 });
  log('share link created through the UI');
  return shareUrl;
}

/** Scope a drawer form item by its exact label text. */
function drawerFormItem(page: Page, label: string) {
  return page
    .locator('.n-drawer')
    .locator(`.n-form-item:has(.n-form-item-label__text:text-is("${label}"))`);
}

/** The composer's digital-employee picker trigger (label 普通 before any selection). */
function employeePickerTrigger(page: Page) {
  return page.locator('.composer-footer').getByRole('button', { name: '普通' });
}

/** The session header identity chip (#42): the chip span's title IS the
 * employee's name (the neighboring title span carries the session title,
 * which may also mention the employee, so match the title attribute exactly). */
function sessionIdentityChip(page: Page) {
  return page.locator(`header span[title="${EMPLOYEE_NAME}"]`);
}

/** The composer textarea on the ACTIVE pane. */
function composer(page: Page) {
  return page.locator(COMPOSER).first();
}

/** Send a message into the ACTIVE pane and wait for one more completed assistant row. */
async function sendMessage(page: Page, text: string): Promise<void> {
  const before = await page.locator('.assistant-content').count();
  const input = composer(page);
  await input.fill(text);
  await input.press('Enter');
  await expect(page.locator('.assistant-content')).toHaveCount(before + 1, { timeout: 60_000 });
  await expect(page.getByText(STUB_REPLY).last()).toBeVisible({ timeout: 60_000 });
  await expect(input).toBeEnabled({ timeout: 60_000 });
  log(`message sent on active pane: "${text}"`);
}

/** Scope message text to a rendered conversation row (T12 helper). */
function messageRow(page: Page, text: string) {
  return page.locator('[class*="group/user-message"]').filter({ hasText: text });
}

// ---------------------------------------------------------------------------
// The walkthrough
// ---------------------------------------------------------------------------

/** Change 小礼's status through the REAL admin console edit drawer. */
async function setEmployeeStatusViaConsole(page: Page, status: 'active' | 'disabled'): Promise<void> {
  const drawer = page.locator('.n-drawer');
  const employeeRow = page.locator('tr').filter({ hasText: EMPLOYEE_NAME });
  await employeeRow.getByRole('button', { name: '编辑' }).click();
  await expect(drawer).toBeVisible({ timeout: 15_000 });
  const radioLabel = status === 'active' ? '启用' : '停用';
  await drawer.locator('.n-radio').filter({ hasText: radioLabel }).click();
  await drawer.getByRole('button', { name: '保存' }).click();
  await expect(page.locator('.n-message--success-type', { hasText: '数字员工已保存' })).toBeVisible({
    timeout: 30_000,
  });
  await expect(drawer).toBeHidden({ timeout: 15_000 });
  await expect(employeeRow).toContainText(radioLabel);
  log(`小礼 set to ${status} through the admin console`);
}

test.describe.configure({ retries: 0 });

test('#46 小礼 walkthrough: console create → picker visibility → session identity → shared session → disable/restore → delete conflict', async ({
  browser,
}) => {
  test.setTimeout(600_000);
  const shotSizes: Record<string, number> = {};

  // ------------------------------------------------ fixtures (real admin API)
  const fixtures = await seedOrgFixtures();
  log(`fixtures ready: role=${fixtures.giftRoleId} skill=${fixtures.skillId} tool=${fixtures.toolId}`);

  // ------------------------------------------- CONTEXT ADMIN: create 小礼
  const adminContext = await browser.newContext();
  await adminContext.addInitScript(ADMIN_LOCALE_RESET);
  const adminPage = await adminContext.newPage();
  await loginAdmin(adminPage);
  await adminPage.goto(`${ADMIN_URL}/digital-employees`);
  await adminPage.getByRole('button', { name: '新建数字员工', exact: true }).click();
  const drawer = adminPage.locator('.n-drawer');
  await expect(drawer).toBeVisible({ timeout: 15_000 });

  await drawerFormItem(adminPage, '名字').locator('input').fill(EMPLOYEE_NAME);
  await drawerFormItem(adminPage, '描述').locator('textarea').fill(EMPLOYEE_DESCRIPTION);
  await drawerFormItem(adminPage, '宣言').locator('textarea').fill(MANIFESTO);
  // NOT public: leave the 是否公开 switch untouched (the draft defaults to
  // isPublic=false); the persisted value is asserted via the admin API below.

  // One bound organization skill.
  await drawerFormItem(adminPage, '绑定技能').locator('.n-select').click();
  await adminPage.locator('.n-base-select-option').filter({ hasText: SKILL_NAME }).first().click();
  // One bound HTTP organization tool.
  await drawer.getByRole('button', { name: '添加工具绑定' }).click();
  await drawer.locator('.binding-row .n-select').first().click();
  await adminPage.locator('.n-base-select-option').filter({ hasText: TOOL_NAME }).first().click();
  // Assigned to the gift position role.
  await drawerFormItem(adminPage, '岗位角色').locator('.n-select').click();
  await adminPage.locator('.n-base-select-option').filter({ hasText: ROLE_NAME }).first().click();
  // Dismiss the still-open multi-select menu (click on neutral drawer chrome).
  await drawer.locator('.n-drawer-header').click();

  await drawer.getByRole('button', { name: '保存' }).click();
  const savedMessage = adminPage.locator('.n-message--success-type', { hasText: '数字员工已保存' });
  const duplicateMessage = adminPage.locator('.n-message--error-type', { hasText: '数字员工名称已存在' });
  const saveOutcome = await Promise.race([
    savedMessage
      .waitFor({ state: 'visible', timeout: 30_000 })
      .then(() => 'created' as const)
      .catch(() => 'timeout' as const),
    duplicateMessage
      .waitFor({ state: 'visible', timeout: 30_000 })
      .then(() => 'duplicate' as const)
      .catch(() => 'timeout' as const),
  ]);
  if (saveOutcome === 'duplicate') {
    // Re-run against the same stack: the employee already exists; 409 is success.
    log('小礼 already exists — 数字员工名称已存在 treated as idempotent success');
    await drawer.getByRole('button', { name: '取消' }).click();
    await expect(drawer).toBeHidden({ timeout: 15_000 });
  } else if (saveOutcome === 'created') {
    log('小礼 created through the real admin console');
    await expect(drawer).toBeHidden({ timeout: 15_000 });
  } else {
    throw new Error('admin console save produced neither 数字员工已保存 nor 数字员工名称已存在');
  }

  const employeeRow = adminPage.locator('tr').filter({ hasText: EMPLOYEE_NAME });
  await expect(employeeRow).toBeVisible({ timeout: 15_000 });
  await expect(employeeRow).toContainText('指派');

  // Transport-level verification of what the console persisted. Bindings are
  // verified BY NAME (resolving each bound id through the collection listing)
  // so the assertion does not depend on which same-named fixture row a past
  // run happened to create.
  const adminToken = await apiAdminLogin();
  const resolveEmployee = async (): Promise<JsonObject> => {
    const rows = asArray(await (await apiFetch(`${ADMIN_API}/digital-employees`, { token: adminToken })).json())
      .map(asRecord)
      .filter((row) => asString(row.name) === EMPLOYEE_NAME);
    expect(rows.length, 'exactly one 小礼 row').toBe(1);
    return rows[0] as JsonObject;
  };
  let xiaoLi = await resolveEmployee();
  expect(xiaoLi.isPublic, '小礼 must NOT be public').toBe(false);

  const skillRows = asArray(await (await apiFetch(`${ADMIN_API}/skills`, { token: adminToken })).json()).map(
    asRecord,
  );
  const skillNameById = new Map(skillRows.map((row) => [asString(row.id), asString(row.name)]));
  const boundSkillNames = asArray(xiaoLi.skillIds)
    .map(asString)
    .map((id) => skillNameById.get(id) ?? '');
  expect(boundSkillNames, '小礼 must carry the bound organization skill').toContain(SKILL_NAME);

  const toolRows = asArray(await (await apiFetch(`${ADMIN_API}/tools`, { token: adminToken })).json()).map(
    asRecord,
  );
  const toolNameById = new Map(toolRows.map((row) => [asString(row.id), asString(row.name)]));
  const boundToolNames = asArray(xiaoLi.toolBindings)
    .map(asRecord)
    .map((binding) => toolNameById.get(asString(binding.toolId)) ?? '');
  expect(boundToolNames, '小礼 must carry the bound organization tool').toContain(TOOL_NAME);

  const roleRows = asArray(await (await apiFetch(`${ADMIN_API}/position-roles`, { token: adminToken })).json()).map(
    asRecord,
  );
  const roleNameById = new Map(roleRows.map((row) => [asString(row.id), asString(row.name)]));
  const assignedRoleNames = asArray(xiaoLi.roleIds)
    .map(asString)
    .map((id) => roleNameById.get(id) ?? '');
  expect(assignedRoleNames, '小礼 must be assigned to the gift role').toContain(ROLE_NAME);

  const employeeId = asString(xiaoLi.id);
  log(`小礼 resolved: id=${employeeId} skill+tool+role bindings verified via admin API`);

  // Re-run normalization: a previous run may have ended with 小礼 disabled (the
  // disable step is part of this very walkthrough). Re-enable through the real
  // console so the picker and the fresh bound session below behave as on run 1.
  if (asString(xiaoLi.status) !== 'active') {
    log('normalizing: 小礼 was left disabled by an earlier run; re-enabling first');
    await setEmployeeStatusViaConsole(adminPage, 'active');
    xiaoLi = await resolveEmployee();
  }
  expect(asString(xiaoLi.status), '小礼 must be active at the start of the walkthrough').toBe('active');
  await expect(employeeRow).toContainText('启用');
  shotSizes['digital-employee-admin-console.png'] = await captureSideBySide(
    browser,
    'digital-employee-admin-console.png',
    [{ buffer: await adminPage.screenshot({ fullPage: true }), label: 'CONTEXT ADMIN — 数字员工列表：小礼（指派 / 启用）' }],
    { width: 1600, height: 1000 },
  );

  // ------------------------------------- CONTEXT ASSIGNED: picker shows 小礼
  const assignedContext = await browser.newContext();
  const pageAssigned = await assignedContext.newPage();
  await loginUser(pageAssigned, ASSIGNED.username, ASSIGNED.password);
  await pageAssigned.getByRole('button', { name: '新建对话' }).click();
  await expect(composer(pageAssigned)).toBeVisible({ timeout: 15_000 });
  const assignedTrigger = employeePickerTrigger(pageAssigned);
  await expect(assignedTrigger, 'the assigned user must see the digital-employee picker').toBeVisible({
    timeout: 15_000,
  });
  await assignedTrigger.click();
  // The picker dropdown (ChatComposer: `absolute bottom-9`, aligned to the
  // chip's left edge since the employee chip moved to the toolbar's left side).
  // Several popovers share that offset, so anchor on the employee row itself.
  const dropdown = pageAssigned.locator('div.absolute.bottom-9').filter({ hasText: EMPLOYEE_NAME });
  await expect(dropdown.first()).toBeVisible({ timeout: 15_000 });
  await expect(dropdown.getByRole('button').filter({ hasText: EMPLOYEE_NAME }).first()).toBeVisible({ timeout: 15_000 });
  const pickerOpen = await pageAssigned.screenshot({ fullPage: true });

  // --------------------------- CONTEXT UNASSIGNED: picker hides 小礼 (UI + API)
  const unassignedContext = await browser.newContext();
  const pageOutsider = await unassignedContext.newPage();
  await loginUser(pageOutsider, UNASSIGNED.username, UNASSIGNED.password);
  await pageOutsider.getByRole('button', { name: '新建对话' }).click();
  await expect(composer(pageOutsider)).toBeVisible({ timeout: 15_000 });
  await expect(employeePickerTrigger(pageOutsider), 'the unassigned user must not get the picker').toHaveCount(0);
  const outsiderComposer = await pageOutsider.screenshot({ fullPage: true });
  shotSizes['digital-employee-picker.png'] = await captureSideBySide(
    browser,
    'digital-employee-picker.png',
    [
      { buffer: pickerOpen, label: 'CONTEXT ASSIGNED — 新建对话选择器列出小礼' },
      { buffer: outsiderComposer, label: 'CONTEXT UNASSIGNED — 选择器整体不可见（无可用数字员工）' },
    ],
    { width: 2560, height: 1400 },
  );

  // Availability mirrors the session-creation admission predicate; probe it on
  // the transport level with both users' real tokens. No main_id query param:
  // an end_user principal carries its tenant in the token, and the scope check
  // rejects a mismatched claimed main_id with 403 tenant_scope_mismatch.
  const AVAILABLE_URL = `${RAW_API}/digital-employees/available`;
  const assignedToken = await apiEndUserLogin(ASSIGNED.username, ASSIGNED.password);
  const outsiderToken = await apiEndUserLogin(UNASSIGNED.username, UNASSIGNED.password);
  const availableToAssigned = asRecord(await (await apiFetch(AVAILABLE_URL, { token: assignedToken })).json());
  const availableToOutsider = asRecord(await (await apiFetch(AVAILABLE_URL, { token: outsiderToken })).json());
  expect(
    asArray(availableToAssigned.data)
      .map(asRecord)
      .some((row) => asString(row.name) === EMPLOYEE_NAME),
    'available list (assigned) must contain 小礼',
  ).toBe(true);
  expect(
    asArray(availableToOutsider.data)
      .map(asRecord)
      .some((row) => asString(row.name) === EMPLOYEE_NAME),
    'available list (unassigned) must NOT contain 小礼',
  ).toBe(false);
  log('availability probe: assigned sees 小礼, unassigned does not');

  // --------------------------- CONTEXT ASSIGNED: select 小礼, send, identity chip
  await dropdown.getByRole('button').filter({ hasText: EMPLOYEE_NAME }).click();
  // The trigger now shows the selected employee's name (its label was 普通
  // before), so re-locate it by text instead of by the old accessible name.
  await expect(
    pageAssigned.locator('.composer-footer').getByRole('button').filter({ hasText: EMPLOYEE_NAME }),
    'the picker trigger must show the selected employee',
  ).toBeVisible({ timeout: 15_000 });
  const createResponsePromise = pageAssigned.waitForResponse(
    (response) =>
      response.url().includes('/askai-api/api/chat/completions') && response.request().method() === 'POST',
    { timeout: 60_000 },
  );
  await composer(pageAssigned).fill(MSG_A);
  await composer(pageAssigned).press('Enter');
  const createResponse = await createResponsePromise;
  expect(createResponse.status(), 'employee session creation -> HTTP 200').toBe(200);
  const requestBody = createResponse.request().postDataJSON() as JsonObject | null;
  expect(asString(requestBody?.digitalEmployeeId), 'the wire request must carry digitalEmployeeId').toBe(employeeId);
  const sessionId = createResponse.headers()['x-session-id'];
  expect(sessionId, 'chat completion response must carry X-Session-Id').toBeTruthy();
  await expect(pageAssigned.locator('.assistant-content')).toHaveCount(1, { timeout: 60_000 });
  await expect(pageAssigned.getByText(STUB_REPLY).last()).toBeVisible({ timeout: 60_000 });
  await expect(sessionIdentityChip(pageAssigned), 'session header identity chip must show 小礼').toBeVisible({
    timeout: 30_000,
  });
  log(`employee session created: ${sessionId} (digitalEmployeeId=${employeeId} on the wire)`);
  const ownerSessionShot = await pageAssigned.screenshot({ fullPage: true });

  // ------------------------------------------------ share with the unassigned user
  const shareUrl = await createShareLink(pageAssigned);
  await redeemShare(pageOutsider, shareUrl, UNASSIGNED.username, UNASSIGNED.password);
  await expect(messageRow(pageOutsider, MSG_A)).toBeVisible({ timeout: 30_000 });
  await sendMessage(pageOutsider, MSG_B);
  await expect(sessionIdentityChip(pageOutsider), 'the participant sees the SAME employee identity').toBeVisible({
    timeout: 30_000,
  });
  await expect(messageRow(pageAssigned, MSG_B)).toBeVisible({ timeout: 30_000 });
  // The owner's pane saw a FOREIGN run (the participant's turn); the composer
  // stays blocked until the run notice is refreshed (intended T10 behavior),
  // so click the notice's refresh before any later owner turn.
  const refreshButton = pageAssigned.getByRole('button', { name: '刷新会话' });
  await expect(refreshButton, 'the finished-foreign-run notice must appear').toBeVisible({ timeout: 30_000 });
  await refreshButton.click();
  await expect(refreshButton).toBeHidden({ timeout: 30_000 });
  log('owner pane refreshed after the participant turn; composer unblocked');
  log('shared session: unassigned participant talks to the same employee');
  shotSizes['digital-employee-shared-session.png'] = await captureSideBySide(
    browser,
    'digital-employee-shared-session.png',
    [
      {
        buffer: ownerSessionShot,
        label: 'CONTEXT ASSIGNED (owner) — 小礼身份徽标 + 第一句',
      },
      {
        buffer: await pageOutsider.screenshot({ fullPage: true }),
        label: 'CONTEXT UNASSIGNED (participant) — 同一小礼徽标 + 双方发言',
      },
    ],
    { width: 2560, height: 1400 },
  );

  // ------------------------------------- disable 小礼 in the admin console
  await setEmployeeStatusViaConsole(adminPage, 'disabled');
  const adminDisabledShot = await adminPage.screenshot({ fullPage: true });

  // New sessions cannot select it either: the assigned user's availability probe
  // must now hide 小礼 (existence-hiding semantics).
  const availableAfterDisable = asRecord(
    await (await apiFetch(AVAILABLE_URL, { token: assignedToken })).json(),
  );
  expect(
    asArray(availableAfterDisable.data)
      .map(asRecord)
      .some((row) => asString(row.name) === EMPLOYEE_NAME),
    'a disabled employee must leave the availability list',
  ).toBe(false);

  // --------- bound session: the NEXT send surfaces the pinned disabled error
  const historyRows = await pageAssigned.locator('.assistant-content').count();
  const disabledResponsePromise = pageAssigned.waitForResponse(
    (response) =>
      response.url().includes('/askai-api/api/chat/completions') && response.request().method() === 'POST',
    { timeout: 60_000 },
  );
  await composer(pageAssigned).fill(MSG_DISABLED);
  await composer(pageAssigned).press('Enter');
  const disabledResponse = await disabledResponsePromise;
  expect(disabledResponse.status(), 'send on a disabled-employee session -> HTTP 409').toBe(409);
  await expect(pageAssigned.getByText(DISABLED_ERROR), 'the disabled error copy must surface in the UI').toBeVisible({
    timeout: 30_000,
  });
  // History stays readable and the composer stays usable.
  await expect(pageAssigned.locator('.assistant-content')).toHaveCount(historyRows, { timeout: 15_000 });
  await expect(pageAssigned.getByText(STUB_REPLY).first()).toBeVisible({ timeout: 15_000 });
  await expect(composer(pageAssigned)).toBeEnabled({ timeout: 15_000 });
  log('disabled: 409 surfaced with the pinned copy; history stays readable');
  shotSizes['digital-employee-disabled.png'] = await captureSideBySide(
    browser,
    'digital-employee-disabled.png',
    [
      {
        buffer: await pageAssigned.screenshot({ fullPage: true }),
        label: 'CONTEXT ASSIGNED — 停用后的发送：409 文案浮出，历史仍可读',
      },
      { buffer: adminDisabledShot, label: 'CONTEXT ADMIN — 小礼已停用（列表状态：停用）' },
    ],
    { width: 2560, height: 1400 },
  );

  // ------------------------------------- re-enable in the admin console
  await setEmployeeStatusViaConsole(adminPage, 'active');

  // Restored: a NEW send completes through the stub model.
  await sendMessage(pageAssigned, MSG_RESTORED);
  log('re-enable restored sending');

  // ------------------------------------- delete attempt → referenced-employee 409
  const deleteResponsePromise = adminPage.waitForResponse(
    (response) =>
      /\/admin-api\/api\/digital-employees\/[^/]+$/.test(response.url()) &&
      response.request().method() === 'DELETE',
    { timeout: 30_000 },
  );
  await employeeRow.getByRole('button', { name: '删除' }).click();
  const deleteDialog = adminPage.locator('.n-dialog');
  await expect(deleteDialog).toBeVisible({ timeout: 15_000 });
  await expect(deleteDialog).toContainText(EMPLOYEE_NAME);
  await deleteDialog.getByRole('button', { name: '删除', exact: true }).click();
  const deleteResponse = await deleteResponsePromise;
  expect(deleteResponse.status(), 'deleting the referenced employee -> HTTP 409').toBe(409);
  await expect(
    adminPage.locator('.n-message--error-type', { hasText: DELETE_CONFLICT }),
    'the 409 conflict message must surface in the dialog flow',
  ).toBeVisible({ timeout: 15_000 });
  await expect(employeeRow, '小礼 must survive the refused delete').toBeVisible({ timeout: 15_000 });
  shotSizes['digital-employee-delete-conflict.png'] = await captureSideBySide(
    browser,
    'digital-employee-delete-conflict.png',
    [
      {
        buffer: await adminPage.screenshot({ fullPage: true }),
        label: 'CONTEXT ADMIN — 删除被拒：409 该数字员工仍有会话引用，请先停用',
      },
    ],
    { width: 1600, height: 1000 },
  );
  log('delete refused with 409 and surfaced in the console; row survives');

  for (const [name, size] of Object.entries(shotSizes)) {
    log(`evidence ${name}: ${size} bytes`);
  }
  log(`capture summary: ${JSON.stringify(shotSizes)}`);

  // Evidence-root hygiene (T12 tail): the evidence root must live OUTSIDE the
  // implementation worktree and must not contain the spec working directory.
  const evidenceRoot = path.resolve(EVIDENCE_ROOT);
  expect(evidenceRoot.includes(`${path.sep}worktrees${path.sep}`), 'evidence root must not live under a worktree').toBe(
    false,
  );
  expect(process.cwd().startsWith(evidenceRoot), 'evidence root must not contain the spec working directory').toBe(
    false,
  );
  log(`evidence root ${evidenceRoot} is outside the implementation worktree`);

  await adminContext.close();
  await assignedContext.close();
  await unassignedContext.close();
});

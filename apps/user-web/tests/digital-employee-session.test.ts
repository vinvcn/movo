import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import {
  DIGITAL_EMPLOYEE_NONE,
  digitalEmployeeInitial,
  newSessionDigitalEmployeeId,
} from '../src/composables/digitalEmployeeSession'
import { startChatStream } from '../src/composables/useChatStream'

// Bundling axios into ESM needs a require shim, installed before the api
// module's dynamic import (same idiom as tests/session-live-contract.test.ts).
;(globalThis as { require?: unknown }).require = createRequire(import.meta.url)

// (a) The request payload builder includes digitalEmployeeId only for the
//     first message of a new pane (no session id yet).
assert.equal(newSessionDigitalEmployeeId(null, 'emp-1'), 'emp-1', 'a new pane with a selection binds the employee')
assert.equal(newSessionDigitalEmployeeId(undefined, 'emp-1'), 'emp-1')
assert.equal(newSessionDigitalEmployeeId(null, ' emp-1 '), 'emp-1', 'the selection is trimmed')

// (b) The payload omits the field once a session id exists (immutable binding)
//     and for the 普通 default.
assert.equal(newSessionDigitalEmployeeId('sess-1', 'emp-1'), undefined, 'an existing session never re-sends the field')
assert.equal(newSessionDigitalEmployeeId('sess-1', DIGITAL_EMPLOYEE_NONE), undefined)
assert.equal(newSessionDigitalEmployeeId(null, DIGITAL_EMPLOYEE_NONE), undefined, '普通 sends nothing')
assert.equal(newSessionDigitalEmployeeId(null, undefined), undefined)
assert.equal(newSessionDigitalEmployeeId(null, '   '), undefined, 'a blank selection never binds')

// Avatar fallback: the employee name's first letter.
assert.equal(digitalEmployeeInitial('客服小艾'), '客')
assert.equal(digitalEmployeeInitial('Aria'), 'A')
assert.equal(digitalEmployeeInitial('  '), '?')
assert.equal(digitalEmployeeInitial(null), '?')

// 员工头像 URL 的 origin 解析：种子按安装端口把绝对地址烧进库里，同一份 Mongo
// 被不同端口的栈共用时，只有回环地址的 origin 需要按当前栈改写。
const { resolveEmployeeAvatarUrl } = await import('../src/composables/digitalEmployeeSession')

// 无 window（本测试就是这种环境）：原样返回
assert.equal(
  resolveEmployeeAvatarUrl('http://localhost:3000/admin-api/static/employee-avatars/xiaoli.png'),
  'http://localhost:3000/admin-api/static/employee-avatars/xiaoli.png',
  'outside a browser the value is untouched',
)
assert.equal(resolveEmployeeAvatarUrl(''), null)
assert.equal(resolveEmployeeAvatarUrl(null), null)
assert.equal(resolveEmployeeAvatarUrl('   '), null)

// 有 window：回环地址按当前 origin 改写，外部主机与相对路径不动
;(globalThis as { window?: unknown }).window = { location: { origin: 'http://localhost:3001' } }
try {
  assert.equal(
    resolveEmployeeAvatarUrl('http://localhost:3000/admin-api/static/employee-avatars/xiaoli.png'),
    'http://localhost:3001/admin-api/static/employee-avatars/xiaoli.png',
    'a loopback avatar from another port moves to the current origin',
  )
  assert.equal(
    resolveEmployeeAvatarUrl('http://127.0.0.1:9999/a.png?v=2#frag'),
    'http://localhost:3001/a.png?v=2#frag',
    '127.0.0.1 counts as loopback and search/hash survive the rewrite',
  )
  assert.equal(
    resolveEmployeeAvatarUrl('http://localhost:3001/same.png'),
    'http://localhost:3001/same.png',
    'an already-correct origin is left alone',
  )
  assert.equal(
    resolveEmployeeAvatarUrl('https://cdn.example.com/employee.png'),
    'https://cdn.example.com/employee.png',
    'a genuinely external avatar is never rewritten',
  )
  assert.equal(
    resolveEmployeeAvatarUrl('/admin-api/static/employee-avatars/xiaoli.png'),
    '/admin-api/static/employee-avatars/xiaoli.png',
    'a relative path already resolves against the current origin',
  )
} finally {
  delete (globalThis as { window?: unknown }).window
}

// (c) The available-employees API module maps the {code, data} envelope.
const { normalizeAvailableDigitalEmployees } = await import('../src/api/digitalEmployees')
assert.deepEqual(normalizeAvailableDigitalEmployees({
  code: 0,
  data: [
    { id: 'emp-1', name: '客服小艾', description: '官方客服助手', avatar_url: 'https://example.test/a.png' },
    { id: 'emp-2', name: 'Aria', description: '', avatar_url: '' },
    { id: '', name: 'broken', description: '', avatar_url: null },
  ],
}), [
  { id: 'emp-1', name: '客服小艾', description: '官方客服助手', avatar_url: 'https://example.test/a.png' },
  { id: 'emp-2', name: 'Aria', description: '', avatar_url: null },
], 'id-less rows are dropped and empty avatars collapse to null')
assert.deepEqual(
  normalizeAvailableDigitalEmployees([{ id: 'emp-1', name: 'X' }]),
  [{ id: 'emp-1', name: 'X', description: '', avatar_url: null }],
  'a bare array response also unwraps',
)
assert.deepEqual(normalizeAvailableDigitalEmployees({ code: 0, data: null }), [])
assert.deepEqual(normalizeAvailableDigitalEmployees(null), [])

// Transport contract: startChatStream posts the field top-level on
// POST /chat/completions, and JSON.stringify drops it when undefined.
await (async () => {
  const captured: Array<{ url: string; body: Record<string, unknown> }> = []
  const previousFetch = globalThis.fetch
  ;(globalThis as { fetch: typeof fetch }).fetch = (async (input: unknown, init?: RequestInit) => {
    captured.push({ url: String(input), body: JSON.parse(String(init?.body || '{}')) as Record<string, unknown> })
    return new Response('', { status: 200, headers: { 'Content-Type': 'text/plain' } })
  }) as typeof fetch
  try {
    const bound = startChatStream(
      { messages: [{ role: 'user', content: 'hi' }], digitalEmployeeId: 'emp-1' },
      () => { },
    )
    await bound.done
    const ordinary = startChatStream(
      { messages: [{ role: 'user', content: 'hi' }], digitalEmployeeId: undefined },
      () => { },
    )
    await ordinary.done
    assert.equal(captured.length, 2)
    assert.equal(captured[0].url, '/askai-api/api/chat/completions')
    assert.equal(captured[0].body.digitalEmployeeId, 'emp-1', 'the first message posts the top-level digitalEmployeeId')
    assert.equal('digitalEmployeeId' in captured[1].body, false, 'the 普通 default omits the field entirely')
  } finally {
    ;(globalThis as { fetch: typeof fetch }).fetch = previousFetch
  }
})()

// Wiring contract: the store owns the per-pane selection, attaches it through
// the immutability guard, and the composer/header surface it.
const api = readFileSync('src/api/digitalEmployees.ts', 'utf8')
const stream = readFileSync('src/composables/useChatStream.ts', 'utf8')
const store = readFileSync('src/composables/useChatRuntimeStore.ts', 'utf8')
const composer = readFileSync('src/components/chat/ChatComposer.vue', 'utf8')
const chatWindow = readFileSync('src/components/ChatWindow.vue', 'utf8')
const appShell = readFileSync('src/App.vue', 'utf8')
const header = readFileSync('src/components/chat/ChatSessionHeader.vue', 'utf8')

assert.match(api, /\/api\/digital-employees\/available/, 'the api module calls the availability endpoint')
assert.match(stream, /digitalEmployeeId\?: string/, 'ChatStreamRequest carries the top-level field')
assert.match(store, /digitalEmployeeId: '',/, 'a fresh pane starts unbound')
assert.match(store, /function selectPaneDigitalEmployee\(/, 'the store owns the per-pane picker selection')
assert.match(store, /if \(!pane \|\| pane\.sessionId\) return/, 'the selection is immutable once the session exists')
assert.match(
  store,
  /digitalEmployeeId: newSessionDigitalEmployeeId\(pane\.sessionId, pane\.digitalEmployeeId\)/,
  'sendMessage attaches the pane selection through the immutability guard',
)
assert.match(composer, /'普通'/, 'the picker offers the default 普通 option')
assert.match(composer, /select-digital-employee/, 'the composer emits picker selections upward')
assert.match(chatWindow, /:digital-employee-selectable="!props\.sessionId"/, 'the picker shows only for panes without a session')
assert.match(appShell, /selectPaneDigitalEmployee\(pane\.key, employeeId\)/, 'App.vue routes selections into the runtime store')
assert.match(header, /digital_employee/, 'the session header renders the bound employee identity')

// (d) 数字员工货架：召唤走「新建 pane + 预选员工」，员工芯片摆在输入框
//     工具条左侧，并且已绑定会话也能显示只读身份。
const shelfPage = readFileSync('src/components/digital-employees/DigitalEmployeeShelfPage.vue', 'utf8')
const employeeShelf = readFileSync('src/components/digital-employees/DigitalEmployeeShelf.vue', 'utf8')

assert.match(appShell, /'digital-employees'/, 'App.vue owns the shelf view')
assert.match(appShell, /summonDigitalEmployee\(employeeId: string\)/, 'App.vue owns the summon handler')
assert.match(
  appShell,
  /chatRuntime\.selectPaneDigitalEmployee\(pane\.key, target\)/,
  'summon 复用既有的预选原语，不新造绑定路径',
)
assert.match(
  employeeShelf,
  /fetchAvailableDigitalEmployees/,
  '货架只读服务端准入列表，前端不二次筛选',
)
assert.match(shelfPage, /\(event: 'summon'/, '货架把召唤事件交给 App.vue 处理')
assert.match(appShell, /@summon="summonDigitalEmployee"/, 'App.vue 接住货架的召唤事件')
assert.doesNotMatch(shelfPage, /SkillShelf|ConnectorShelf/, '模块只有数字员工一个货架，无技能/连接器 Tab')

// 员工芯片：新建会话可切换，已绑定会话退化为只读身份。
assert.match(composer, /const digitalEmployeeChipInteractive = computed/, 'the composer splits interactive vs read-only chip states')
assert.match(composer, /props\.digitalEmployeeSelectable === true/, 'only a session-less pane may switch the employee')
assert.match(composer, /digitalEmployeeChipVisible/, 'the chip is shown for a bound session too')
assert.match(composer, /v-if="digitalEmployeeChipInteractive && digitalEmployeeDropdownOpen"/, 'a bound session never opens the picker')

// App.vue 侧栏入口与桌面主区归属。
assert.match(appShell, /app\.sidebar\.digital_employees/, 'the sidebar exposes the digital-employee shelf entry')
assert.match(appShell, /currentView\.value !== 'digital-employees'/, 'desktop collapses the secondary nav on the shelf view')
assert.match(appShell, /function sidebarItemActive\(icon: string\)/, '顶部动作区的选中态集中在一个函数里')

// (e) 侧栏其余部分保持基线原样：本 PR 不做侧栏重排、不撤「我的配置」。
assert.match(
  appShell,
  /<div v-if="!capabilities\.isDesktop && \(canUseSkills \|\| canUseTools\)"/,
  '「我的配置」块保持基线原样',
)
assert.match(appShell, /icon: 'clock'[\s\S]{0,240}icon: 'search'/, '定时任务与搜索的相对位置不变')

// 模型下拉文案保持基线原样（本 PR 不改这部分）。
assert.match(composer, /model\.providerName/, '模型下拉保留基线的 provider/model 文案')

// 助手行头像跟随本会话绑定的数字员工，未绑定时是「普通员工」。
const avatarModule = readFileSync('src/components/chat/messageRowAvatar.ts', 'utf8')
const avatarComponent = readFileSync('src/components/chat/MessageAvatar.vue', 'utf8')
assert.match(avatarModule, /employeeName: string \| null/, '头像解析接受绑定的员工身份')
assert.match(avatarModule, /fallbackGlyph: 'person'/, '未绑定时退化为「普通员工」通用人像')
assert.match(avatarModule, /standardEmployeeName/, '「普通员工」文案随界面语言切换')
assert.doesNotMatch(avatarModule, /panda-mark/, '助手行不再统一落品牌熊猫')
assert.match(chatWindow, /employeeName: selectedDigitalEmployee\.value\?\.name \?\? null/, 'ChatWindow 把绑定员工喂给头像解析')
assert.match(avatarComponent, /fallbackGlyph\?: 'person' \| null/, 'MessageAvatar 会渲染兜底人像')
assert.match(avatarComponent, /props\.fallbackGlyph === 'person'/, '人像优先于首字兜底')

// (f) 员工头像 URL 不绑死端口：两个消费点都要按当前 origin 解析。
assert.match(api, /resolveEmployeeAvatarUrl\(item\.avatar_url/, '可用员工列表的头像按当前 origin 解析')
assert.match(header, /resolveEmployeeAvatarUrl\(digitalEmployee\.value\?\.avatar_url\)/, '会话头的员工头像同样解析')
assert.doesNotMatch(header, /:src="digitalEmployee\.avatar_url"/, '会话头不再直接使用库里烧死的地址')

console.log('digital-employee session tests passed')

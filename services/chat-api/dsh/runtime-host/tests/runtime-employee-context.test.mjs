import assert from 'node:assert/strict'
import test from 'node:test'

import { normalizeModelProfile } from '../src/model-profile.mjs'
import { renderEmployeeContext } from '../src/runtime-employee-context.mjs'
import { ASKAI_RUNTIME_PERSONA } from '../src/runtime-persona.mjs'

const valid = {
  profileVersion: 'profile-a',
  modelInstanceId: 'model-a',
  modelName: 'deepseek-chat',
  gatewayUrl: 'http://127.0.0.1/model',
  accessToken: 'ephemeral-token',
}

test('model profile accepts a well-formed employeeProfile', () => {
  const profile = normalizeModelProfile({
    ...valid,
    employeeProfile: { id: 'employee-1', name: '小礼', manifesto: '你是礼品协作者。' },
  }, 'profile-a')
  assert.equal(profile.employeeProfile.name, '小礼')
  assert.equal(profile.employeeProfile.manifesto, '你是礼品协作者。')
  const withoutManifesto = normalizeModelProfile({
    ...valid,
    employeeProfile: { id: 'employee-1', name: '小礼' },
  }, 'profile-a')
  assert.equal(withoutManifesto.employeeProfile.manifesto, undefined)
})

test('model profile rejects malformed employeeProfile payloads', () => {
  assert.throws(
    () => normalizeModelProfile({ ...valid, employeeProfile: '小礼' }, 'profile-a'),
    /employeeProfile must be an object/,
  )
  assert.throws(
    () => normalizeModelProfile({ ...valid, employeeProfile: { id: 'employee-1', manifesto: 'x' } }, 'profile-a'),
    /employeeProfile is missing name/,
  )
  assert.throws(
    () => normalizeModelProfile({
      ...valid,
      employeeProfile: { id: 'employee-1', name: '小礼', secret: 'leak' },
    }, 'profile-a'),
    /forbidden fields: secret/,
  )
  assert.throws(
    () => normalizeModelProfile({
      ...valid,
      employeeProfile: { id: 'employee-1', name: '小礼', manifesto: 'x'.repeat(8193) },
    }, 'profile-a'),
    /8192/,
  )
  assert.throws(
    () => normalizeModelProfile({
      ...valid,
      employeeProfile: { id: 'employee-1', name: '小礼', manifesto: 42 },
    }, 'profile-a'),
    /manifesto must be a string/,
  )
})

test('employee context renders the identity override and manifesto', () => {
  assert.equal(renderEmployeeContext(undefined), '')
  const nameOnly = renderEmployeeContext({ id: 'employee-1', name: '小礼' })
  assert.match(nameOnly, /exclusively as 小礼/)
  assert.match(nameOnly, /never as MOVO/)
  assert.doesNotMatch(nameOnly, /manifesto \(trusted, admin-authored\)/)
  const full = renderEmployeeContext({ id: 'employee-1', name: '小礼', manifesto: '你是礼品协作者。' })
  assert.match(full, /你是礼品协作者。/)
  assert.match(full, /manifesto \(trusted, admin-authored\)/)
  assert.match(full, /does not widen permissions/)
})

test('runtime persona carries the digital-employee identity exception', () => {
  assert.match(ASKAI_RUNTIME_PERSONA, /unless this session is configured with an admin-authored digital employee/)
  assert.match(ASKAI_RUNTIME_PERSONA, /then identify yourself exclusively as that employee and never as MOVO/)
  // The pre-existing persona contracts must survive the amendment.
  assert.match(ASKAI_RUNTIME_PERSONA, /web_search at most once/)
  assert.match(ASKAI_RUNTIME_PERSONA, /Write ordinary short content directly/)
})

import assert from 'node:assert/strict'
import test from 'node:test'

import { rowAvatarIdentity, type RowAvatarContext } from '../src/components/chat/messageRowAvatar'

function ctx(overrides: Partial<RowAvatarContext> = {}): RowAvatarContext {
  return {
    shared: false,
    ownMessage: true,
    authorName: null,
    authorAvatarUrl: null,
    userDisplayName: '张三',
    userAvatarUrl: '/askai-api/api/files/avatars_u1/avatar.png',
    employeeName: null,
    employeeAvatarUrl: null,
    locale: 'zh',
    ...overrides,
  }
}

test('assistant rows fall back to the generic staff avatar when no employee is bound', () => {
  assert.deepEqual(rowAvatarIdentity('assistant', ctx()), {
    displayName: '普通员工',
    avatarUrl: null,
    fallbackIcon: null,
    fallbackGlyph: 'person',
  })
  assert.equal(
    rowAvatarIdentity('assistant', ctx({ locale: 'en' })).displayName,
    'Staff',
    'the generic staff label follows the interface language',
  )
})

test('assistant rows take the bound digital employee identity', () => {
  assert.deepEqual(rowAvatarIdentity('assistant', ctx({
    employeeName: '小礼',
    employeeAvatarUrl: 'http://localhost:3000/admin-api/static/employee-avatars/xiaoli.png',
  })), {
    displayName: '小礼',
    avatarUrl: 'http://localhost:3000/admin-api/static/employee-avatars/xiaoli.png',
    fallbackIcon: null,
    fallbackGlyph: null,
  })
})

test('a bound employee without an avatar degrades to their own initial, not the staff glyph', () => {
  assert.deepEqual(rowAvatarIdentity('assistant', ctx({ employeeName: '小礼' })), {
    displayName: '小礼',
    avatarUrl: null,
    fallbackIcon: null,
    fallbackGlyph: null,
  })
})

test('a bound employee with an avatar but no name still renders as that employee', () => {
  assert.deepEqual(rowAvatarIdentity('assistant', ctx({ employeeAvatarUrl: '  /x.png  ' })), {
    displayName: null,
    avatarUrl: '/x.png',
    fallbackIcon: null,
    fallbackGlyph: null,
  })
})

test('own user rows resolve to the current user identity', () => {
  assert.deepEqual(rowAvatarIdentity('user', ctx()), {
    displayName: '张三',
    avatarUrl: '/askai-api/api/files/avatars_u1/avatar.png',
    fallbackIcon: null,
    fallbackGlyph: null,
  })
})

test('shared rows from other members resolve to the author identity', () => {
  assert.deepEqual(rowAvatarIdentity('user', ctx({
    shared: true,
    ownMessage: false,
    authorName: 'Li Si',
    authorAvatarUrl: 'https://cdn.example.com/li.png',
  })), {
    displayName: 'Li Si',
    avatarUrl: 'https://cdn.example.com/li.png',
    fallbackIcon: null,
    fallbackGlyph: null,
  })
})

test('shared rows authored by the viewer still use the viewer identity', () => {
  assert.deepEqual(rowAvatarIdentity('user', ctx({
    shared: true,
    ownMessage: true,
    authorName: 'Li Si',
    authorAvatarUrl: 'https://cdn.example.com/li.png',
  })), {
    displayName: '张三',
    avatarUrl: '/askai-api/api/files/avatars_u1/avatar.png',
    fallbackIcon: null,
    fallbackGlyph: null,
  })
})

test('an employee binding never leaks into user rows', () => {
  assert.deepEqual(rowAvatarIdentity('user', ctx({
    employeeName: '小礼',
    employeeAvatarUrl: '/x.png',
  })), {
    displayName: '张三',
    avatarUrl: '/askai-api/api/files/avatars_u1/avatar.png',
    fallbackIcon: null,
    fallbackGlyph: null,
  })
})

console.log('message row avatar tests passed')

import { type Locale } from '../../composables/i18n'

export type RowAvatarContext = {
  shared: boolean
  ownMessage: boolean
  authorName: string | null
  authorAvatarUrl: string | null
  userDisplayName: string | null
  userAvatarUrl: string | null
  /** 本会话绑定的数字员工；未绑定时为空，助手行退化为「普通员工」。 */
  employeeName: string | null
  employeeAvatarUrl: string | null
  locale: Locale
}

export type RowAvatarIdentity = {
  displayName: string | null
  avatarUrl: string | null
  fallbackIcon: string | null
  /** 无图可依时的兜底字形；`person` 即「普通员工」通用人像。 */
  fallbackGlyph?: 'person' | null
}

/** 未绑定数字员工时的助手身份名。纯函数不读响应式语言，沿用 brandName 的做法。 */
function standardEmployeeName(locale: Locale): string {
  return locale === 'en' ? 'Staff' : '普通员工'
}

/**
 * 消息行头像的纯解析：
 * - 助手行取本会话绑定的数字员工身份；未绑定时是「普通员工」通用人像 ——
 *   不再统一落品牌熊猫，否则每条回复都像同一个「人」在答。
 * - 共享会话中他人消息取作者身份，其余取当前用户身份。
 * 头像缺失时由展示层退化为首字。
 */
export function rowAvatarIdentity(role: string, ctx: RowAvatarContext): RowAvatarIdentity {
  if (role === 'assistant') {
    const employeeName = (ctx.employeeName || '').trim()
    const employeeAvatarUrl = (ctx.employeeAvatarUrl || '').trim()
    if (employeeName || employeeAvatarUrl) {
      return {
        displayName: employeeName || null,
        avatarUrl: employeeAvatarUrl || null,
        fallbackIcon: null,
        fallbackGlyph: null,
      }
    }
    return {
      displayName: standardEmployeeName(ctx.locale),
      avatarUrl: null,
      fallbackIcon: null,
      fallbackGlyph: 'person',
    }
  }
  if (ctx.shared && !ctx.ownMessage) {
    return { displayName: ctx.authorName, avatarUrl: ctx.authorAvatarUrl, fallbackIcon: null, fallbackGlyph: null }
  }
  return { displayName: ctx.userDisplayName, avatarUrl: ctx.userAvatarUrl, fallbackIcon: null, fallbackGlyph: null }
}

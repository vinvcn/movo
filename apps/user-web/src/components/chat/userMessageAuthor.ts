/**
 * T9: pure author-presentation resolver for shared user-message rows.
 *
 * `UserMessageAuthor.vue` is a thin template delegation to this module, so the
 * headless contract suite can assert the return values without a DOM and
 * `typecheck` proves the wiring. The helper never returns a raw avatar value:
 * unsafe paths degrade to deterministic initials; same-origin /paths pass
 * through (the backend signs avatar URLs that way).
 */

export type AuthorIdentityInput = {
  display_name: string | null | undefined
  avatar_url: string | null | undefined
}

export type AuthorPresentation =
  | { kind: 'image'; src: string; alt: string }
  | { kind: 'initials'; text: string; ariaLabel: string }

const FALLBACK_NAME = 'User'
const FALLBACK_IMAGE_ALT = 'User avatar'
const FALLBACK_INITIALS = '?'

function normalizedName(displayName: string | null | undefined): string | null {
  const name = (displayName ?? '').trim().replace(/\s+/g, ' ')
  return name.length > 0 ? name : null
}

function initialsFromName(name: string | null): string {
  if (!name) return FALLBACK_INITIALS
  const letters: string[] = []
  for (const word of name.split(/\s+/)) {
    const first = Array.from(word)[0]
    if (!first) continue
    letters.push(first.toUpperCase())
    if (letters.length === 2) break
  }
  return letters.join('') || FALLBACK_INITIALS
}

/** Allows absolute http(s) URLs and same-origin paths (/...); rejects protocols, object paths, and whitespace. */
function safeAvatarUrl(avatarUrl: string | null | undefined): string | null {
  const raw = (avatarUrl ?? '').trim()
  if (!raw) return null
  if (/[\u0000-\u0020\u007f]/.test(raw)) return null
  // 后端签名的头像地址是同源相对路径，直接交给浏览器解析。
  if (raw.startsWith('/') && !raw.startsWith('//')) return raw
  let parsed: URL
  try {
    parsed = new URL(raw)
  } catch {
    return null
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null
  return parsed.href
}

export function resolveAuthorPresentation(input: AuthorIdentityInput): AuthorPresentation {
  const name = normalizedName(input.display_name)
  const src = safeAvatarUrl(input.avatar_url)
  if (src !== null) return { kind: 'image', src, alt: name ?? FALLBACK_IMAGE_ALT }
  return { kind: 'initials', text: initialsFromName(name), ariaLabel: name ?? FALLBACK_NAME }
}

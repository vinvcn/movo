// Ticket #42 (员工会话核心): pure rules for binding a NEW conversation to a
// digital employee. The binding is created by the first message and is
// immutable afterwards (docs/digital-employee.md), so the request field may
// exist only while the pane has no session id yet — never on an existing
// session, where the server rejects a different id with a 400.

export type DigitalEmployeeIdentity = {
  id: string
  name: string
  avatar_url?: string | null
}

/** The "普通" picker selection: no employee, no request field, today's behavior. */
export const DIGITAL_EMPLOYEE_NONE = ''

/**
 * The top-level `digitalEmployeeId` field for POST /chat/completions.
 * Defined only when this request CREATES the conversation (the pane has no
 * session id) and the user picked an employee; `undefined` once a session
 * exists (immutable binding) and for the 普通 default.
 */
export function newSessionDigitalEmployeeId(
  sessionId: string | null | undefined,
  selectedDigitalEmployeeId?: string | null,
): string | undefined {
  if (sessionId) return undefined
  const employeeId = String(selectedDigitalEmployeeId || '').trim()
  return employeeId || undefined
}

/** Avatar fallback for employees without an avatar_url: the name's first letter. */
export function digitalEmployeeInitial(name?: string | null): string {
  const first = String(name || '').trim().charAt(0)
  return first || '?'
}

/** Hosts that mean "this deployment" rather than "some other host". */
const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '[::1]'])

/**
 * The URL the browser can actually load for a digital employee's avatar.
 *
 * An employee avatar URL is written by the seed script from the `base_url` it
 * was installed with, and admin-api requires it to be an absolute http(s) URL
 * (`digital_employees/service.py` validates with `urlparse`). A local install
 * therefore bakes its port into the database, and one shared database cannot be
 * right for two stacks on different ports — the stale origin just fails to load.
 *
 * Only a loopback URL whose origin differs from the current one is rewritten to
 * the current origin; a genuinely external avatar (another host) is returned
 * untouched, and so is a relative path. Outside a browser (unit tests) the value
 * is returned as-is.
 */
export function resolveEmployeeAvatarUrl(value?: string | null): string | null {
  const raw = String(value || '').trim()
  if (!raw) return null
  if (typeof window === 'undefined') return raw
  try {
    const parsed = new URL(raw, window.location.origin)
    if (!LOOPBACK_HOSTS.has(parsed.hostname)) return raw
    if (parsed.origin === window.location.origin) return raw
    return `${window.location.origin}${parsed.pathname}${parsed.search}${parsed.hash}`
  } catch {
    return raw
  }
}

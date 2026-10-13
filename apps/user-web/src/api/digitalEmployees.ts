import { createApiClient } from './client'
import { resolveEmployeeAvatarUrl } from '../composables/digitalEmployeeSession'

const client = createApiClient({
  baseURL: '/askai-api',
  timeout: 15000,
})

export interface AvailableDigitalEmployee {
  id: string
  name: string
  description: string
  avatar_url?: string | null
}

/**
 * Map the GET /digital-employees/available envelope (`{code, data}`) into the
 * picker list — the models.ts unwrap idiom, centralized so the contract test
 * can pin the response shape. Tolerates a bare array; rows without an id or a
 * name cannot be rendered and are dropped.
 */
export function normalizeAvailableDigitalEmployees(payload: unknown): AvailableDigitalEmployee[] {
  const envelope = payload as { data?: unknown } | null | undefined
  const rows = Array.isArray(payload)
    ? payload
    : Array.isArray(envelope?.data)
      ? envelope.data
      : []
  const employees: AvailableDigitalEmployee[] = []
  for (const row of rows) {
    if (!row || typeof row !== 'object') continue
    const item = row as Record<string, unknown>
    const id = String(item.id || '').trim()
    const name = String(item.name || '').trim()
    if (!id || !name) continue
    employees.push({
      id,
      name,
      description: String(item.description || ''),
      avatar_url: resolveEmployeeAvatarUrl(item.avatar_url ? String(item.avatar_url) : null),
    })
  }
  return employees
}

/** Ticket #42: the end-user picker list; only active, public employees today. */
export async function fetchAvailableDigitalEmployees(mainId = 'default'): Promise<AvailableDigitalEmployee[]> {
  const response = await client.get('/api/digital-employees/available', { params: { main_id: mainId } })
  return normalizeAvailableDigitalEmployees(response.data)
}

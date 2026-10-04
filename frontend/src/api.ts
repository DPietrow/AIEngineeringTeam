import { API_BASE, clearToken, getToken, setToken } from './auth'
import type { RunDetail, RunSummary } from './types'

/** fetch with the login token attached. A 401 means the token is missing, expired or revoked:
 * it is cleared, which sends the user back to the login page. */
async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (res.status === 401 && token) clearToken()
  return res
}

async function errorMessage(res: Response): Promise<string> {
  const body = (await res.json().catch(() => ({}))) as { error?: string }
  return body.error ?? `${res.status} ${res.statusText}`
}

async function getJson<T>(path: string): Promise<T> {
  const res = await apiFetch(path)
  if (!res.ok) throw new Error(await errorMessage(res))
  return (await res.json()) as T
}

export const fetchRuns = () => getJson<RunSummary[]>('/api/runs')
export const fetchRun = (id: string) => getJson<RunDetail>(`/api/runs/${id}`)

async function decide(id: string, decision: 'approve' | 'reject', reason: string): Promise<void> {
  const res = await apiFetch(`/api/runs/${id}/${decision}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  })
  if (!res.ok) throw new Error(await errorMessage(res))
}

export const approveRun = (id: string) => decide(id, 'approve', '')
export const rejectRun = (id: string, reason: string) => decide(id, 'reject', reason)

export async function createRun(task: string): Promise<{ id: string }> {
  const res = await apiFetch('/api/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task }),
  })
  if (!res.ok) throw new Error(await errorMessage(res))
  return (await res.json()) as { id: string }
}

// --- authentication -----------------------------------------------------------------------

/** Public: does this server require a login? */
export async function fetchAuthRequired(): Promise<boolean> {
  const res = await fetch(`${API_BASE}/api/auth/status`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return ((await res.json()) as { auth_required: boolean }).auth_required
}

export async function login(password: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  })
  if (res.status === 429) {
    const wait = Number(res.headers.get('Retry-After')) || 60
    throw new Error(`Too many attempts. Try again in ${Math.ceil(wait / 60)} minute(s).`)
  }
  if (res.status === 401) throw new Error('Wrong password.')
  if (!res.ok) throw new Error(await errorMessage(res))
  const body = (await res.json()) as { token: string; expires_at: number }
  setToken(body.token, body.expires_at)
}

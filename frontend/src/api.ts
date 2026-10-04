import type { RunDetail, RunSummary } from './types'

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return (await res.json()) as T
}

export const fetchRuns = () => getJson<RunSummary[]>('/api/runs')
export const fetchRun = (id: string) => getJson<RunDetail>(`/api/runs/${id}`)

async function decide(id: string, decision: 'approve' | 'reject', reason: string): Promise<void> {
  const res = await fetch(`/api/runs/${id}/${decision}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  })
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { error?: string }
    throw new Error(body.error ?? `${res.status} ${res.statusText}`)
  }
}

export const approveRun = (id: string) => decide(id, 'approve', '')
export const rejectRun = (id: string, reason: string) => decide(id, 'reject', reason)

export async function createRun(task: string): Promise<{ id: string }> {
  const res = await fetch('/api/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task }),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return (await res.json()) as { id: string }
}

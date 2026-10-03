import type { RunDetail, RunSummary } from './types'

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return (await res.json()) as T
}

export const fetchRuns = () => getJson<RunSummary[]>('/api/runs')
export const fetchRun = (id: string) => getJson<RunDetail>(`/api/runs/${id}`)

export async function createRun(task: string): Promise<{ id: string }> {
  const res = await fetch('/api/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task }),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return (await res.json()) as { id: string }
}

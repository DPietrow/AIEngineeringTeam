import { useEffect, useState } from 'react'
import { createRun, fetchRuns } from './api'
import type { RunSummary } from './types'
import { ago, usd } from './format'
import { StatusBadge } from './ui'

export default function RunList() {
  const [runs, setRuns] = useState<RunSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [task, setTask] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [showEvals, setShowEvals] = useState(false)
  // Review-eval runs are tagged "[eval-review]" in their task text by the harness.
  const isEval = (r: RunSummary) => r.task.startsWith('[eval')
  const visible = runs?.filter((r) => showEvals || !isEval(r))
  const hidden = (runs?.length ?? 0) - (visible?.length ?? 0)

  useEffect(() => {
    let cancelled = false
    const load = () =>
      fetchRuns()
        .then((r) => {
          if (!cancelled) {
            setRuns(r)
            setError(null)
          }
        })
        .catch((e: unknown) => {
          if (!cancelled) setError(String(e))
        })
    void load()
    const t = window.setInterval(() => void load(), 3000)
    return () => {
      cancelled = true
      window.clearInterval(t)
    }
  }, [])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!task.trim()) return
    setSubmitting(true)
    try {
      const { id } = await createRun(task)
      window.location.hash = `#/runs/${id}`
    } catch (err) {
      setError(String(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-4">
      <form onSubmit={(e) => void submit(e)} className="flex gap-2">
        <input
          value={task}
          onChange={(e) => setTask(e.target.value)}
          placeholder="Describe a task for the team..."
          className="flex-1 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
        />
        <button
          disabled={submitting || !task.trim()}
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
        >
          Run
        </button>
      </form>
      {error && <p className="text-sm text-red-700">API error: {error}</p>}
      <label className="flex items-center gap-2 text-sm text-slate-600">
        <input type="checkbox" checked={showEvals} onChange={(e) => setShowEvals(e.target.checked)} />
        Show eval runs{!showEvals && hidden > 0 ? ` (${hidden} hidden)` : ''}
      </label>
      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-2">Task</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2 text-right">Cost</th>
              <th className="px-4 py-2 text-right">Started</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {visible?.map((r) => (
              <tr key={r.id} className="hover:bg-slate-50">
                <td className="max-w-xl truncate px-4 py-2">
                  <a href={`#/runs/${r.id}`} className="font-medium text-blue-700 hover:underline">
                    {r.task}
                  </a>
                  <span className="ml-2 font-mono text-xs text-slate-400">{r.id.slice(0, 8)}</span>
                </td>
                <td className="px-4 py-2">
                  <StatusBadge status={r.status} />
                </td>
                <td className="px-4 py-2 text-right tabular-nums">{usd(r.total_cost_usd)}</td>
                <td className="px-4 py-2 text-right text-slate-500">{ago(r.created_at)}</td>
              </tr>
            ))}
            {visible?.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-slate-500">
                  No runs yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

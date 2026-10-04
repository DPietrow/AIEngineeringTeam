import { useState } from 'react'
import { approveRun, rejectRun } from './api'
import { Card } from './ui'

/** The human gate: nothing is pushed or opened on GitHub until someone clicks Approve. */
export default function ApprovalCard({ runId }: { runId: string }) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const act = async (fn: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try {
      await fn() // the run's SSE stream reports the new status; the page refreshes itself
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-lg border-2 border-amber-400 bg-amber-50 shadow-sm">
      <Card title="Waiting for your approval">
        <p className="mb-3 text-sm text-slate-700">
          The reviewer approved this change and the checks passed. Review the patch below, then approve to push the
          branch and open a pull request, or reject to stop here. Merging is always done by you on GitHub.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <button
            disabled={busy}
            onClick={() => void act(() => approveRun(runId))}
            className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
          >
            Approve and open PR
          </button>
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Reason for rejecting (optional)"
            className="min-w-64 flex-1 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
          />
          <button
            disabled={busy}
            onClick={() => void act(() => rejectRun(runId, reason))}
            className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 disabled:opacity-40"
          >
            Reject
          </button>
        </div>
        {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
      </Card>
    </div>
  )
}

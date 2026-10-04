import ApprovalCard from './ApprovalCard'
import Artifacts from './Artifacts'
import SpanTree from './SpanTree'
import StateProgress from './StateProgress'
import { useRun } from './useRun'
import { usd } from './format'
import { TERMINAL } from './types'
import { Card, StatusBadge } from './ui'

export default function RunPage({ runId }: { runId: string }) {
  const { detail, events, live, error } = useRun(runId)

  if (error && !detail) return <p className="text-sm text-red-700">Could not load run: {error}</p>
  if (!detail) return <p className="text-sm text-slate-500">Loading...</p>

  const { run, spans, artifacts } = detail
  // Why the run ended: a crash message on a root span (run / deliver), or the reason carried
  // by the final run.status event (failed, stopped, no_changes).
  const lastStatus = [...events].reverse().find((e) => e.type === 'run.status')
  const statusText = [lastStatus?.data.error, lastStatus?.data.reason].find(
    (v): v is string => typeof v === 'string' && v.length > 0,
  )
  // Prefer the status event's message (it also carries recovery reasons); a root span's error
  // text is the fallback, e.g. when the page loaded without the event stream's history.
  const runError = statusText ?? spans.find((s) => s.parent_id === null && s.error)?.error ?? null
  const neutral = run.status === 'no_changes'
  return (
    <div className="space-y-4">
      <div>
        <a href="#/" className="text-sm text-blue-700 hover:underline">
          &larr; All runs
        </a>
        <h1 className="mt-1 text-lg font-semibold">{run.task}</h1>
        <div className="mt-1 flex items-center gap-3 text-sm text-slate-500">
          <StatusBadge status={run.status} />
          <span>{usd(run.total_cost_usd)}</span>
          <span className="font-mono text-xs">{run.id}</span>
          {run.config_hash && <span className="font-mono text-xs">cfg {run.config_hash}</span>}
          {live && <span className="text-xs text-blue-600">● live</span>}
        </div>
      </div>
      <Card title="Progress">
        <StateProgress events={events} status={run.status} />
      </Card>
      {runError && TERMINAL.has(run.status) && (
        <div
          className={`rounded-lg border px-4 py-3 text-sm ${
            neutral ? 'border-slate-300 bg-slate-50 text-slate-700' : 'border-red-300 bg-red-50 text-red-800'
          }`}
        >
          <strong>{neutral ? 'Nothing to change' : run.status}:</strong> {runError}
          {neutral && <span className="block text-xs text-slate-500">This is the agent&apos;s own explanation, not a verified fact.</span>}
        </div>
      )}
      {run.status === 'awaiting_approval' && <ApprovalCard runId={run.id} />}
      <Artifacts artifacts={artifacts} />
      <Card title={`Trace (${spans.length} spans)`}>
        <SpanTree spans={spans} />
      </Card>
    </div>
  )
}

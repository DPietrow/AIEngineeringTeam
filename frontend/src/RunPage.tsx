import Artifacts from './Artifacts'
import SpanTree from './SpanTree'
import StateProgress from './StateProgress'
import { useRun } from './useRun'
import { usd } from './format'
import { Card, StatusBadge } from './ui'

export default function RunPage({ runId }: { runId: string }) {
  const { detail, events, live, error } = useRun(runId)

  if (error && !detail) return <p className="text-sm text-red-700">Could not load run: {error}</p>
  if (!detail) return <p className="text-sm text-slate-500">Loading...</p>

  const { run, spans, artifacts } = detail
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
      <Artifacts artifacts={artifacts} />
      <Card title={`Trace (${spans.length} spans)`}>
        <SpanTree spans={spans} />
      </Card>
    </div>
  )
}

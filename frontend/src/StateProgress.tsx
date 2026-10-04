import type { StreamEvent } from './types'

const BASE_STAGES = ['design', 'implement', 'test', 'review', 'done']
// no_changes is not a failure: the stage bar just stops at "implement".
const GATE_STATUSES = new Set(['awaiting_approval', 'approved', 'delivering', 'rejected'])

/** Maps backend state names onto the visible stages. */
function stageOf(state: string): string {
  const s = state.toLowerCase()
  if (s.includes('approval')) return 'approval'
  if (BASE_STAGES.includes(s)) return s
  if (s.includes('design') || s.includes('architect')) return 'design'
  if (s.includes('implement') || s.includes('fix')) return 'implement'
  if (s.includes('test')) return 'test'
  if (s.includes('review')) return 'review'
  if (s.includes('done') || s.includes('deliver')) return 'done'
  return s
}

export default function StateProgress({ events, status }: { events: StreamEvent[]; status: string }) {
  const transitions = events.filter((e) => e.type === 'state.transition')
  // Transitions and retries interleaved in the order they happened (event ids are monotonic).
  const timeline = events
    .filter((e) =>
      ['state.transition', 'loop.retry', 'gate.decision', 'llm.retry', 'run.recovered'].includes(e.type),
    )
    .sort((a, b) => a.id - b.id)
  const current = transitions.length ? stageOf(String(transitions[transitions.length - 1].data.to)) : null
  const visited = new Set(transitions.flatMap((t) => [stageOf(String(t.data.from)), stageOf(String(t.data.to))]))
  const failed = ['failed', 'error', 'rejected', 'timed_out'].includes(status)
  // The human-approval stage only appears for runs that went through the gate.
  const stages = visited.has('approval') || GATE_STATUSES.has(status)
    ? ['design', 'implement', 'test', 'review', 'approval', 'done']
    : BASE_STAGES
  const waiting = status === 'awaiting_approval'

  return (
    <div className="space-y-3">
      <ol className="flex flex-wrap items-center gap-2">
        {stages.map((s, i) => {
          const active = current === s
          const seen = visited.has(s)
          const cls = active
            ? failed
              ? 'border-red-500 bg-red-50 text-red-800'
              : status === 'done' && s === 'done'
                ? 'border-emerald-500 bg-emerald-50 text-emerald-800'
                : waiting && s === 'approval'
                  ? 'border-amber-500 bg-amber-50 text-amber-800 animate-pulse'
                  : 'border-blue-500 bg-blue-50 text-blue-800 animate-pulse'
            : seen
              ? 'border-emerald-300 bg-emerald-50 text-emerald-700'
              : 'border-slate-200 bg-white text-slate-400'
          return (
            <li key={s} className="flex items-center gap-2">
              <span className={`rounded-md border px-3 py-1 text-sm font-medium ${cls}`}>{s}</span>
              {i < stages.length - 1 && <span className="text-slate-300">&rarr;</span>}
            </li>
          )
        })}
        {failed && (
          <li className="rounded-md border border-red-500 bg-red-50 px-3 py-1 text-sm font-medium text-red-800">{status}</li>
        )}
      </ol>
      {timeline.length > 0 && (
        <ul className="space-y-0.5 text-xs text-slate-500">
          {timeline.map((e) =>
            e.type === 'loop.retry' ? (
              <li key={e.id} className="text-amber-700">
                &nbsp;&nbsp;&#8635; retry {String(e.data.retry)}/{String(e.data.cap)} ({String(e.data.loop)})
              </li>
            ) : e.type === 'llm.retry' ? (
              <li key={e.id} className="text-amber-700">
                &nbsp;&nbsp;&#8635; API retry {String(e.data.attempt)}/{String(e.data.max_retries)} in{' '}
                {String(e.data.delay_s)}s ({String(e.data.error)}
                {e.data.status_code ? ` ${String(e.data.status_code)}` : ''})
              </li>
            ) : e.type === 'run.recovered' ? (
              <li key={e.id} className="font-medium text-orange-700">
                recovered after worker loss: {String(e.data.was)} &rarr; {String(e.data.now)}
              </li>
            ) : e.type === 'gate.decision' ? (
              <li key={e.id} className="font-medium text-slate-700">
                human: {String(e.data.decision)}
                {e.data.reason ? <span> &middot; {String(e.data.reason)}</span> : null}
              </li>
            ) : (
              <li key={e.id}>
                <span className="font-mono">
                  {String(e.data.from)} &rarr; {String(e.data.to)}
                </span>
                {e.data.reason ? <span> &middot; {String(e.data.reason)}</span> : null}
              </li>
            ),
          )}
        </ul>
      )}
    </div>
  )
}

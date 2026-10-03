import type { StreamEvent } from './types'

const STAGES = ['design', 'implement', 'test', 'review', 'done'] as const

/** Maps backend state names onto the five visible stages. */
function stageOf(state: string): string {
  const s = state.toLowerCase()
  if (STAGES.includes(s as (typeof STAGES)[number])) return s
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
    .filter((e) => e.type === 'state.transition' || e.type === 'loop.retry')
    .sort((a, b) => a.id - b.id)
  const current = transitions.length ? stageOf(String(transitions[transitions.length - 1].data.to)) : null
  const visited = new Set(transitions.flatMap((t) => [stageOf(String(t.data.from)), stageOf(String(t.data.to))]))
  const failed = status === 'failed' || status === 'error'

  return (
    <div className="space-y-3">
      <ol className="flex flex-wrap items-center gap-2">
        {STAGES.map((s, i) => {
          const active = current === s
          const seen = visited.has(s)
          const cls = active
            ? failed
              ? 'border-red-500 bg-red-50 text-red-800'
              : status === 'done' && s === 'done'
                ? 'border-emerald-500 bg-emerald-50 text-emerald-800'
                : 'border-blue-500 bg-blue-50 text-blue-800 animate-pulse'
            : seen
              ? 'border-emerald-300 bg-emerald-50 text-emerald-700'
              : 'border-slate-200 bg-white text-slate-400'
          return (
            <li key={s} className="flex items-center gap-2">
              <span className={`rounded-md border px-3 py-1 text-sm font-medium ${cls}`}>{s}</span>
              {i < STAGES.length - 1 && <span className="text-slate-300">&rarr;</span>}
            </li>
          )
        })}
        {failed && <li className="rounded-md border border-red-500 bg-red-50 px-3 py-1 text-sm font-medium text-red-800">{status}</li>}
      </ol>
      {timeline.length > 0 && (
        <ul className="space-y-0.5 text-xs text-slate-500">
          {timeline.map((e) =>
            e.type === 'loop.retry' ? (
              <li key={e.id} className="text-amber-700">
                &nbsp;&nbsp;&#8635; retry {String(e.data.retry)}/{String(e.data.cap)} ({String(e.data.loop)})
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

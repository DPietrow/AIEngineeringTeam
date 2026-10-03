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
  const retries = events.filter((e) => e.type === 'loop.retry')
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
      {transitions.length > 0 && (
        <ul className="space-y-0.5 text-xs text-slate-500">
          {transitions.map((t) => (
            <li key={t.id}>
              <span className="font-mono">
                {String(t.data.from)} &rarr; {String(t.data.to)}
              </span>
              {t.data.reason ? <span> &middot; {String(t.data.reason)}</span> : null}
            </li>
          ))}
          {retries.map((r) => (
            <li key={r.id} className="text-amber-700">
              retry {String(r.data.retry)}/{String(r.data.cap)} ({String(r.data.loop)})
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

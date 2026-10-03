import { useState } from 'react'
import type { Span } from './types'
import { duration, StatusBadge, usd } from './ui'

interface Node {
  span: Span
  children: Node[]
}

function build(spans: Span[]): Node[] {
  const byId = new Map<string, Node>(spans.map((s) => [s.id, { span: s, children: [] }]))
  const roots: Node[] = []
  for (const n of byId.values()) {
    const parent = n.span.parent_id ? byId.get(n.span.parent_id) : undefined
    if (parent) parent.children.push(n)
    else roots.push(n)
  }
  return roots
}

function Row({ node, depth }: { node: Node; depth: number }) {
  const [open, setOpen] = useState(false)
  const s = node.span
  const tokens = (s.input_tokens ?? 0) + (s.output_tokens ?? 0)
  return (
    <li>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm hover:bg-slate-50"
        style={{ paddingLeft: depth * 16 + 8 }}
      >
        <span className="w-3 text-slate-400">{open ? '▾' : '▸'}</span>
        <span className="font-mono">{s.name}</span>
        <span className="rounded bg-slate-100 px-1.5 text-xs text-slate-500">{s.kind}</span>
        <StatusBadge status={s.status} />
        <span className="ml-auto flex gap-3 text-xs tabular-nums text-slate-500">
          {tokens > 0 && <span>{tokens} tok</span>}
          {s.cost_usd != null && s.cost_usd > 0 && <span>{usd(s.cost_usd)}</span>}
          <span>{s.ended_at ? duration(s.duration_ms) : 'running...'}</span>
        </span>
      </button>
      {open && (
        <div className="mb-1 space-y-2 border-l-2 border-slate-200 py-2 pr-2 text-xs" style={{ marginLeft: depth * 16 + 20, paddingLeft: 8 }}>
          {s.model && <div className="text-slate-500">model: {s.model}</div>}
          {s.callsite_file && (
            <div className="text-slate-500">
              {s.callsite_file}:{s.callsite_line}
            </div>
          )}
          {s.error && <pre className="whitespace-pre-wrap text-red-700">{s.error}</pre>}
          {s.traceback && <pre className="max-h-48 overflow-auto whitespace-pre-wrap text-red-700">{s.traceback}</pre>}
          <Payload label="input" value={s.input} />
          <Payload label="output" value={s.output} />
        </div>
      )}
      {node.children.length > 0 && (
        <ul>
          {node.children.map((c) => (
            <Row key={c.span.id} node={c} depth={depth + 1} />
          ))}
        </ul>
      )}
    </li>
  )
}

function Payload({ label, value }: { label: string; value: unknown }) {
  if (value == null) return null
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2)
  return (
    <div>
      <div className="mb-0.5 font-semibold uppercase text-slate-400">{label}</div>
      <pre className="max-h-64 overflow-auto rounded bg-slate-50 p-2 whitespace-pre-wrap">{text}</pre>
    </div>
  )
}

export default function SpanTree({ spans }: { spans: Span[] }) {
  if (spans.length === 0) return <p className="text-sm text-slate-500">No spans yet.</p>
  return (
    <ul>
      {build(spans).map((n) => (
        <Row key={n.span.id} node={n} depth={0} />
      ))}
    </ul>
  )
}

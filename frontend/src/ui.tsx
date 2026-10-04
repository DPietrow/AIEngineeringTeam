import type { ReactNode } from 'react'

const STATUS_STYLES: Record<string, string> = {
  pending: 'bg-slate-100 text-slate-700',
  running: 'bg-blue-100 text-blue-800',
  ok: 'bg-emerald-100 text-emerald-800',
  done: 'bg-emerald-100 text-emerald-800',
  failed: 'bg-red-100 text-red-800',
  error: 'bg-red-100 text-red-800',
  stopped: 'bg-amber-100 text-amber-800',
  awaiting_approval: 'bg-amber-100 text-amber-800',
  approved: 'bg-blue-100 text-blue-800',
  delivering: 'bg-blue-100 text-blue-800',
  rejected: 'bg-slate-200 text-slate-700',
  no_changes: 'bg-slate-200 text-slate-700',
  timed_out: 'bg-orange-100 text-orange-800',
}

export function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_STYLES[status] ?? 'bg-slate-100 text-slate-700'
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>
      {status}
    </span>
  )
}

export function Card({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white shadow-sm">
      <header className="flex items-center justify-between border-b border-slate-100 px-4 py-2">
        <h2 className="text-sm font-semibold text-slate-700">{title}</h2>
        {right}
      </header>
      <div className="p-4">{children}</div>
    </section>
  )
}

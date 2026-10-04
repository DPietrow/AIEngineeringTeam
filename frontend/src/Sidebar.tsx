import type { ReactNode } from 'react'
import { ABOUT, AGENTS, EXAMPLES, HARNESS, LLMOPS, LOOPS } from './about'
import type { ConceptItem } from './about'

/** One collapsible panel: its own card, its own heading, independent of the others. */
function Panel({
  title,
  subtitle,
  open = false,
  children,
}: {
  title: string
  subtitle?: string
  open?: boolean
  children: ReactNode
}) {
  return (
    <details open={open} className="group rounded-lg border border-slate-200 bg-white shadow-sm">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-4 py-3">
        <span>
          <span className="block text-sm font-semibold text-slate-800">{title}</span>
          {subtitle && <span className="block text-xs text-slate-500">{subtitle}</span>}
        </span>
        <span className="text-slate-400 transition-transform group-open:rotate-90">&#9656;</span>
      </summary>
      <div className="border-t border-slate-100 px-4 py-3">{children}</div>
    </details>
  )
}

function Concepts({ items }: { items: ConceptItem[] }) {
  return (
    <ul className="space-y-3">
      {items.map((c) => (
        <li key={c.title} className="text-sm">
          <div className="font-medium text-slate-800">{c.title}</div>
          <p className="text-slate-600">{c.text}</p>
          <code className="mt-0.5 inline-block rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">
            {c.where}
          </code>
        </li>
      ))}
    </ul>
  )
}

export default function Sidebar({ onUseExample }: { onUseExample: (ask: string) => void }) {
  return (
    <aside className="space-y-3">
      <Panel title="What is this?" subtitle="The project in 30 seconds" open>
        <p className="text-sm text-slate-700">{ABOUT.summary}</p>
        <ol className="mt-3 flex flex-wrap items-center gap-1 text-xs">
          {ABOUT.flow.map((step, i) => (
            <li key={step} className="flex items-center gap-1">
              <span
                className={`rounded border px-2 py-0.5 font-medium ${
                  step === 'You approve'
                    ? 'border-amber-300 bg-amber-50 text-amber-800'
                    : 'border-slate-200 bg-slate-50 text-slate-700'
                }`}
              >
                {step}
              </span>
              {i < ABOUT.flow.length - 1 && <span className="text-slate-300">&rarr;</span>}
            </li>
          ))}
        </ol>
        <p className="mt-3 text-xs text-slate-500">{ABOUT.note}</p>
        <a href="#/about" className="mt-3 inline-block text-sm font-medium text-blue-700 hover:underline">
          Read the full write-up &rarr;
        </a>
      </Panel>

      <Panel title="The agents" subtitle="Five agents and the code that runs them" open>
        <ul className="space-y-3">
          {AGENTS.map((a) => (
            <li key={a.name} className="text-sm">
              <div className="flex items-baseline justify-between gap-2">
                <span className="font-medium text-slate-800">{a.name}</span>
                <span className="text-xs text-slate-500">{a.role}</span>
              </div>
              <p className="text-slate-600">{a.how}</p>
              <p className="mt-0.5 text-xs text-slate-500">
                <span className="font-medium text-slate-600">Tools:</span> {a.tools}
              </p>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title="Harness engineering" subtitle="The scaffolding around the model">
        <Concepts items={HARNESS} />
      </Panel>

      <Panel title="Loop engineering" subtitle="Control flow, limits and recovery">
        <Concepts items={LOOPS} />
      </Panel>

      <Panel title="LLMOps" subtitle="Observing, measuring and operating it">
        <Concepts items={LLMOPS} />
      </Panel>

      <Panel title="Try these" subtitle="Click one to fill the task box" open>
        <ul className="space-y-2">
          {EXAMPLES.map((e) => (
            <li key={e.title}>
              <button
                onClick={() => onUseExample(e.ask)}
                className="w-full rounded-md border border-slate-200 px-3 py-2 text-left hover:border-blue-300 hover:bg-blue-50"
              >
                <span className="flex items-baseline justify-between gap-2">
                  <span className="text-sm font-medium text-slate-800">{e.title}</span>
                  <span className="text-xs text-slate-400">{e.cost}</span>
                </span>
                <span className="mt-0.5 block text-xs text-slate-600">{e.ask}</span>
                <span className="mt-1 block text-xs text-blue-700">{e.shows}</span>
              </button>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-slate-500">
          Costs are rough, on the default models. These are written for the toy notes app and assume
          its current code, so an example the app already satisfies will end as &quot;no changes&quot;.
        </p>
      </Panel>
    </aside>
  )
}

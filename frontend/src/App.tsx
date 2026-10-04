import { useState, useSyncExternalStore } from 'react'
import About from './AboutPage'
import RunList from './RunList'
import RunPage from './RunPage'
import Sidebar from './Sidebar'

function subscribe(cb: () => void) {
  window.addEventListener('hashchange', cb)
  return () => window.removeEventListener('hashchange', cb)
}
const getHash = () => window.location.hash

export default function App() {
  const hash = useSyncExternalStore(subscribe, getHash)
  const match = /^#\/runs\/([\w-]+)/.exec(hash)
  // The task box lives in the run list, but the sidebar's examples fill it from anywhere, so
  // the draft is held here.
  const [draft, setDraft] = useState('')

  const fillExample = (ask: string) => {
    setDraft(ask)
    window.location.hash = '#/' // jump to the run list, where the task box is
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const isAbout = hash.startsWith('#/about')
  const navCls = (active: boolean) =>
    `rounded-md px-3 py-1 text-sm font-medium ${
      active ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-200'
    }`

  return (
    <div className="mx-auto max-w-7xl px-4 py-6">
      <header className="mb-6 flex items-center justify-between">
        <a href="#/" className="text-xl font-bold">
          AIEngineeringTeam
        </a>
        <nav className="flex gap-1">
          <a href="#/" className={navCls(!isAbout)}>
            Runs
          </a>
          <a href="#/about" className={navCls(isAbout)}>
            About
          </a>
        </nav>
      </header>
      {isAbout ? (
        <main className="mx-auto max-w-5xl">
          <About />
        </main>
      ) : (
        <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
          <div className="lg:sticky lg:top-6 lg:max-h-[calc(100vh-3rem)] lg:w-96 lg:shrink-0 lg:overflow-y-auto lg:pr-1">
            <Sidebar onUseExample={fillExample} />
          </div>
          <main className="min-w-0 flex-1">
            {match ? (
              <RunPage key={match[1]} runId={match[1]} />
            ) : (
              <RunList task={draft} onTaskChange={setDraft} />
            )}
          </main>
        </div>
      )}
    </div>
  )
}

import { useSyncExternalStore } from 'react'
import RunList from './RunList'
import RunPage from './RunPage'

function subscribe(cb: () => void) {
  window.addEventListener('hashchange', cb)
  return () => window.removeEventListener('hashchange', cb)
}
const getHash = () => window.location.hash

export default function App() {
  const hash = useSyncExternalStore(subscribe, getHash)
  const match = /^#\/runs\/([\w-]+)/.exec(hash)

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <header className="mb-6 flex items-baseline justify-between">
        <a href="#/" className="text-xl font-bold">
          AIEngineeringTeam
        </a>
        <span className="text-xs text-slate-500">run observability</span>
      </header>
      {match ? <RunPage key={match[1]} runId={match[1]} /> : <RunList />}
    </div>
  )
}

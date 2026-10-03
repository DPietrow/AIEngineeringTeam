import { useEffect, useState } from 'react'
import './App.css'

type Health = { status: string; version: string }

function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/health')
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then(setHealth)
      .catch((e: Error) => setError(e.message))
  }, [])

  return (
    <main className="app">
      <h1>AIEngineeringTeam</h1>
      <p>Multi-agent coding team: live trace dashboard</p>
      <p className="status" data-state={health ? 'ok' : error ? 'error' : 'loading'}>
        Backend:{' '}
        {health ? `${health.status} (v${health.version})` : error ? `unreachable (${error})` : 'checking...'}
      </p>
    </main>
  )
}

export default App

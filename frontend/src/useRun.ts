import { useEffect, useRef, useState } from 'react'
import { fetchRun } from './api'
import type { RunDetail, StreamEvent } from './types'
import { TERMINAL } from './types'

export interface RunState {
  detail: RunDetail | null
  events: StreamEvent[]
  live: boolean
  error: string | null
}

const EVENT_TYPES = [
  'run.created',
  'run.status',
  'span.start',
  'span.end',
  'artifact.created',
  'state.transition',
  'loop.retry',
  'gate.decision',
  'llm.retry',
  'run.recovered',
]

/**
 * Loads a run snapshot and follows its SSE stream. The stream is replayed from the
 * start (so the state timeline is complete), and every event triggers a debounced
 * snapshot refetch, which keeps the span tree and artifacts consistent with the DB.
 * EventSource reconnects on its own and resends Last-Event-ID, so nothing is lost.
 */
export function useRun(runId: string): RunState {
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [events, setEvents] = useState<StreamEvent[]>([])
  const [live, setLive] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | undefined>(undefined)

  useEffect(() => {
    let cancelled = false
    const refresh = () => {
      fetchRun(runId)
        .then((d) => {
          if (cancelled) return
          setDetail(d)
          setError(null)
        })
        .catch((e: unknown) => {
          if (!cancelled) setError(String(e))
        })
    }
    const schedule = () => {
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(refresh, 250)
    }

    refresh()
    const es = new EventSource(`/api/runs/${runId}/events`)
    const onEvent = (type: string) => (msg: MessageEvent<string>) => {
      let data: Record<string, unknown> = {}
      try {
        data = JSON.parse(msg.data) as Record<string, unknown>
      } catch {
        /* keep empty */
      }
      const id = Number(msg.lastEventId) || 0
      setEvents((prev) => (prev.some((e) => e.id === id) ? prev : [...prev, { id, type, data }]))
      schedule()
    }
    for (const t of EVENT_TYPES) es.addEventListener(t, onEvent(t))
    es.addEventListener('end', () => {
      setLive(false)
      es.close()
      schedule()
    })
    return () => {
      cancelled = true
      window.clearTimeout(timer.current)
      es.close()
    }
  }, [runId])

  const terminal = detail ? TERMINAL.has(detail.run.status) : false
  return { detail, events, live: live && !terminal, error }
}

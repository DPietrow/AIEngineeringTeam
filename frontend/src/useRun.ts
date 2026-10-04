import { useEffect, useRef, useState } from 'react'
import { fetchRun } from './api'
import { streamSse } from './sse'
import type { SseMessage } from './sse'
import type { RunDetail, StreamEvent } from './types'
import { TERMINAL } from './types'

export interface RunState {
  detail: RunDetail | null
  events: StreamEvent[]
  live: boolean
  error: string | null
}

/**
 * Loads a run snapshot and follows its SSE stream. The stream is replayed from the
 * start (so the state timeline is complete), and every event triggers a debounced
 * snapshot refetch, which keeps the span tree and artifacts consistent with the DB.
 * The stream is read with fetch (EventSource cannot send the Authorization header) and
 * reconnects on its own with Last-Event-ID, so nothing is lost across a dropped connection.
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

    const onMessage = (m: SseMessage) => {
      if (cancelled) return
      if (m.event === 'end') {
        setLive(false)
        schedule()
        return
      }
      let data: Record<string, unknown> = {}
      try {
        data = JSON.parse(m.data) as Record<string, unknown>
      } catch {
        /* keep empty */
      }
      if (m.event === 'error') {
        setError(String(data.error ?? 'stream error'))
        return
      }
      const id = Number(m.id) || 0
      setEvents((prev) =>
        prev.some((e) => e.id === id) ? prev : [...prev, { id, type: m.event, data }],
      )
      schedule()
    }

    refresh()
    const controller = new AbortController()
    void streamSse(`/api/runs/${runId}/events`, onMessage, controller.signal)
    return () => {
      cancelled = true
      window.clearTimeout(timer.current)
      controller.abort()
    }
  }, [runId])

  const terminal = detail ? TERMINAL.has(detail.run.status) : false
  return { detail, events, live: live && !terminal, error }
}

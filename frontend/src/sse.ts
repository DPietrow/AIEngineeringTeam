import { API_BASE, clearToken, getToken } from './auth'
import { SseParser } from './sseParser'
import type { SseMessage } from './sseParser'

export type { SseMessage }
export type StreamResult = 'ended' | 'aborted' | 'unauthorized'

const MAX_BACKOFF_MS = 15_000
// The server sends a heartbeat comment every 15 s; a connection silent for 3x that is dead
// (proxies sometimes drop streams without telling anyone), so we reconnect.
const STALL_MS = 45_000

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve()
    const t = window.setTimeout(done, ms)
    function done() {
      window.clearTimeout(t)
      signal.removeEventListener('abort', done)
      resolve()
    }
    signal.addEventListener('abort', done)
  })
}

/**
 * Follows a Server-Sent Events endpoint with fetch, so it can send the Authorization header that
 * EventSource cannot. Behaves like EventSource where it matters: it reconnects after any drop
 * (with exponential backoff), and resends Last-Event-ID so the server resumes exactly where the
 * stream broke, with no gaps and no duplicates. Resolves when the server sends `event: end`,
 * the caller aborts, or the token is rejected (the token is cleared, which shows the login page).
 */
export async function streamSse(
  path: string,
  onMessage: (m: SseMessage) => void,
  signal: AbortSignal,
): Promise<StreamResult> {
  let lastId = ''
  let failures = 0

  while (!signal.aborted) {
    const attempt = new AbortController()
    const stop = () => attempt.abort()
    signal.addEventListener('abort', stop)
    let stall: number | undefined
    const arm = () => {
      window.clearTimeout(stall)
      stall = window.setTimeout(stop, STALL_MS)
    }
    try {
      const headers = new Headers({ Accept: 'text/event-stream' })
      const token = getToken()
      if (token) headers.set('Authorization', `Bearer ${token}`)
      if (lastId) headers.set('Last-Event-ID', lastId)
      arm()
      const res = await fetch(`${API_BASE}${path}`, { headers, signal: attempt.signal })
      if (res.status === 401) {
        clearToken()
        return 'unauthorized'
      }
      if (!res.ok || !res.body) throw new Error(`stream failed: ${res.status}`)

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      const parser = new SseParser()
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        arm()
        for (const m of parser.feed(decoder.decode(value, { stream: true }))) {
          failures = 0
          if (m.id) lastId = m.id
          onMessage(m)
          if (m.event === 'end') return 'ended'
        }
      }
      // The server closed the stream without sending `end`: treat as a drop and resume.
    } catch {
      /* network error, stall abort or bad status: fall through to the backoff and retry */
    } finally {
      window.clearTimeout(stall)
      signal.removeEventListener('abort', stop)
    }
    if (signal.aborted) break
    failures += 1
    await sleep(Math.min(MAX_BACKOFF_MS, 500 * 2 ** failures), signal)
  }
  return 'aborted'
}

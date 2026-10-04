import { useSyncExternalStore } from 'react'

/**
 * The login token lives in localStorage (so it survives reloads), with an in-memory copy as a
 * fallback when storage is blocked. It is a bearer token, not a cookie: the app sends it in an
 * Authorization header, which keeps cross-origin hosting (frontend and API on different domains)
 * simple and rules out cookie-based CSRF.
 *
 * Trade-off worth knowing: anything that can run script on this page (an XSS bug) can read the
 * token. The app renders no raw HTML and loads no third-party scripts, which is what keeps that
 * risk low.
 */
const KEY = 'agentteam.auth'

interface Stored {
  token: string
  expires_at: number // unix seconds
}

/** Base URL of the API. Empty (the default) means same origin, e.g. via the Vite dev proxy. */
export const API_BASE = ((import.meta.env.VITE_API_BASE as string | undefined) ?? '').replace(/\/$/, '')

let memory: Stored | null = null
const listeners = new Set<() => void>()

function load(): Stored | null {
  let stored: Stored | null = memory
  try {
    const raw = window.localStorage.getItem(KEY)
    if (raw) stored = JSON.parse(raw) as Stored
  } catch {
    /* storage unavailable or corrupt: use the in-memory copy */
  }
  if (!stored || typeof stored.token !== 'string' || stored.expires_at * 1000 <= Date.now()) {
    return null
  }
  return stored
}

function emit() {
  listeners.forEach((l) => l())
}

export function getToken(): string | null {
  return load()?.token ?? null
}

export function setToken(token: string, expires_at: number): void {
  memory = { token, expires_at }
  try {
    window.localStorage.setItem(KEY, JSON.stringify(memory))
  } catch {
    /* in-memory only */
  }
  emit()
}

export function clearToken(): void {
  memory = null
  try {
    window.localStorage.removeItem(KEY)
  } catch {
    /* nothing to clear */
  }
  emit()
}

function subscribe(cb: () => void) {
  listeners.add(cb)
  window.addEventListener('storage', cb) // another tab logged in or out
  return () => {
    listeners.delete(cb)
    window.removeEventListener('storage', cb)
  }
}

/** Current token (null when logged out), re-rendering on login/logout. */
export function useToken(): string | null {
  return useSyncExternalStore(subscribe, getToken)
}

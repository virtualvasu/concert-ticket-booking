import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Corrects for clock skew between this browser and the server: feed it the
 * `server_time` of every response, then call `now()` for server-aligned time.
 */
export function useServerClock() {
  const offset = useRef(0)
  const observe = useCallback((serverTime: string | undefined) => {
    if (serverTime) offset.current = Date.parse(serverTime) - Date.now()
  }, [])
  const now = useCallback(() => Date.now() + offset.current, [])
  return { observe, now }
}

/** Whole-or-fractional seconds from `now` until the ISO timestamp (never negative). */
export function secondsUntil(iso: string | null | undefined, now: number): number {
  if (!iso) return 0
  return Math.max(0, (Date.parse(iso) - now) / 1000)
}

/** Re-renders the caller every `ms` so countdowns move smoothly. */
export function useTicker(ms = 250): void {
  const [, set] = useState(0)
  useEffect(() => {
    const t = setInterval(() => set((n) => n + 1), ms)
    return () => clearInterval(t)
  }, [ms])
}

import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Calls `fn` now and then every `intervalMs`. Keeps the last good `data` when
 * a later call fails. Re-starts whenever `deps` change.
 */
export function usePoll<T>(
  fn: () => Promise<T>,
  intervalMs: number,
  deps: readonly unknown[] = [],
): { data: T | undefined; error: Error | undefined; refresh: () => Promise<void> } {
  const [data, setData] = useState<T>()
  const [error, setError] = useState<Error>()
  const fnRef = useRef(fn)
  fnRef.current = fn
  const alive = useRef(true)

  const refresh = useCallback(async () => {
    try {
      const value = await fnRef.current()
      if (!alive.current) return
      setData(value)
      setError(undefined)
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e : new Error(String(e)))
    }
  }, [])

  useEffect(() => {
    alive.current = true
    let timer: ReturnType<typeof setTimeout>
    const loop = async () => {
      await refresh()
      if (alive.current) timer = setTimeout(loop, intervalMs)
    }
    loop()
    return () => {
      alive.current = false
      clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, refresh, ...deps])

  return { data, error, refresh }
}

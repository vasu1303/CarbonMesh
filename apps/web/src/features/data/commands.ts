import { useEffect, useRef, useState } from 'react'

export function useIdempotencyKey() {
  const last = useRef<{ payload: string; key: string } | null>(null)
  return (payload: unknown) => {
    const signature = JSON.stringify(payload)
    if (last.current?.payload !== signature)
      last.current = { payload: signature, key: crypto.randomUUID() }
    return last.current.key
  }
}

// Local execution state keeps command bodies out of the shared mutation cache.
export function useCommand<T>() {
  const [state, setState] = useState<{
    pending: boolean
    data?: T
    error?: unknown
  }>({ pending: false })
  const running = useRef(false)
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  async function run(
    operation: () => Promise<T>,
    onSuccess?: (data: T) => void,
  ) {
    if (running.current) return
    running.current = true
    setState({ pending: true })
    try {
      const data = await operation()
      if (!mounted.current) return
      setState({ pending: false, data })
      onSuccess?.(data)
    } catch (error) {
      if (mounted.current) setState({ pending: false, error })
    } finally {
      running.current = false
    }
  }
  return { ...state, run }
}

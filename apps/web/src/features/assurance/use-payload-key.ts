import { useRef } from 'react'

export function usePayloadKey() {
  const previous = useRef({ payload: '', key: '' })
  return (payload: unknown) => {
    const serialized = JSON.stringify(payload)
    if (previous.current.payload !== serialized) {
      previous.current = { payload: serialized, key: crypto.randomUUID() }
    }
    return previous.current.key
  }
}

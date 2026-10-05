import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { apiUrl } from '@/services/api'
import { runOptions } from './queries'
import {
  eventDataSchema,
  eventNames,
  type AgentRun,
  type RunEvent,
} from './schemas'

const stopped = (run: AgentRun) =>
  run.terminal_state !== 'running' ||
  run.pending_interrupt?.status === 'pending'
export function useRunEvents(run: AgentRun) {
  const client = useQueryClient()
  const current = useRef(run)
  const stopStream = useRef<(() => void) | null>(null)
  const [generation, setGeneration] = useState(0)
  const [events, setEvents] = useState<RunEvent[]>([])
  const [connection, setConnection] = useState('Connecting to persisted events')
  useEffect(() => {
    if (!stopped(current.current) && stopped(run)) stopStream.current?.()
    current.current = run
  }, [run])
  useEffect(() => {
    let disposed = false
    let source: EventSource | undefined
    let timer: ReturnType<typeof setTimeout> | undefined
    let watchdog: ReturnType<typeof setTimeout> | undefined
    let pollCount = 0
    let polling = false
    let refreshing = false
    let refreshAgain = false
    const options = runOptions(run.context.company_id, run.run_id)
    const close = () => {
      source?.close()
      clearTimeout(timer)
      clearTimeout(watchdog)
    }
    stopStream.current = () => {
      close()
      setConnection('Updates stopped at the recorded state')
    }
    const update = async (): Promise<AgentRun | undefined> => {
      if (refreshing) {
        refreshAgain = true
        return
      }
      refreshing = true
      try {
        const result = await client.fetchQuery(options)
        if (!disposed) current.current = result
        return result
      } catch {
        return undefined
      } finally {
        refreshing = false
        if (refreshAgain && !disposed) {
          refreshAgain = false
          void update()
        }
      }
    }
    const poll = async () => {
      if (disposed) return
      if (stopped(current.current)) {
        close()
        setConnection('Updates stopped at the recorded state')
        return
      }
      if (pollCount >= 15) {
        close()
        setConnection('Live updates paused. Refresh to check the run.')
        return
      }
      pollCount += 1
      const result = await update()
      if (disposed) return
      if (result && stopped(result)) {
        close()
        setConnection('Updates stopped at the recorded state')
        return
      }
      timer = setTimeout(() => {
        void poll()
      }, 2000)
    }
    const fallback = () => {
      if (disposed || polling) return
      close()
      if (stopped(current.current)) {
        setConnection('Recorded run loaded; event replay ended')
        return
      }
      polling = true
      setConnection('Event stream unavailable; checking persisted status')
      void poll()
    }
    // One bounded replay also loads persisted steps for runs already stopped.
    try {
      source = new EventSource(
        apiUrl(`/runs/${run.run_id}/events`, {
          company_id: run.context.company_id,
        }),
      )
      source.onopen = () => {
        if (!disposed) setConnection('Connected to persisted events')
      }
      source.onerror = fallback
      for (const name of eventNames)
        source.addEventListener(name, (raw) => {
          if (disposed || polling) return
          try {
            const message = raw as MessageEvent<string>
            const data = eventDataSchema.parse(JSON.parse(message.data))
            const sequence = data.sequence ?? Number(message.lastEventId)
            if (
              !Number.isSafeInteger(sequence) ||
              sequence < 1 ||
              !data.occurred_at ||
              (data.run_id && data.run_id !== run.run_id)
            ) {
              fallback()
              return
            }
            const event: RunEvent = {
              sequence,
              name,
              occurred_at: data.occurred_at,
              data,
            }
            setEvents((previous) =>
              [...previous.filter((item) => item.sequence !== sequence), event]
                .sort((a, b) => a.sequence - b.sequence)
                .slice(-250),
            )
            if (
              [
                'run.completed',
                'run.stopped',
                'approval.required',
                'clarification.required',
                'run.resume_resolved',
              ].includes(name)
            ) {
              void update().then((latest) => {
                if (disposed || !latest || !stopped(latest)) return
                if (
                  latest.pending_interrupt &&
                  latest.pending_interrupt.sequence !== sequence
                )
                  return
                close()
                setConnection('Updates stopped at the recorded state')
              })
            }
          } catch {
            fallback()
          }
        })
      watchdog = setTimeout(fallback, 60000)
    } catch {
      fallback()
    }
    return () => {
      disposed = true
      stopStream.current = null
      close()
    }
  }, [client, run.context.company_id, run.run_id, generation])

  const merged = new Map(
    run.telemetry.events.map((event) => [event.sequence, event]),
  )
  for (const event of events) merged.set(event.sequence, event)
  return {
    events: [...merged.values()].sort((a, b) => a.sequence - b.sequence),
    connection,
    reconnect: () => setGeneration((value) => value + 1),
  }
}

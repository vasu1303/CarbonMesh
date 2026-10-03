import { useQuery } from '@tanstack/react-query'
import { z } from 'zod'

import { authenticationRequired, sessionOptions } from './session'

const configuredActor = z.uuid().safeParse(import.meta.env.VITE_ACTOR_ID)

export function useWorkspaceActor() {
  const session = useQuery({
    ...sessionOptions,
    enabled: authenticationRequired,
  })
  return {
    actorId: authenticationRequired
      ? (session.data?.actor_id ?? '')
      : configuredActor.success
        ? configuredActor.data
        : '',
    authenticated: authenticationRequired && !!session.data,
    role: authenticationRequired ? session.data?.role : undefined,
  }
}

import { z } from 'zod'
import { useWorkspaceOptions } from '@/services/workspace'

const configuredActor = z.uuid().safeParse(import.meta.env.VITE_ACTOR_ID)

export function useWorkspaceActor(preferredRole = 'sustainability_analyst') {
  const actors = useWorkspaceOptions('actors')
  const candidates = actors.data?.items.filter(
    (item) => item.status === 'active' && item.role === preferredRole,
  )
  const actor =
    candidates?.find(
      (item) => configuredActor.success && item.id === configuredActor.data,
    ) ?? candidates?.[0]
  return {
    actorId: actor?.id ?? '',
    actorName: actor?.label ?? '',
    authenticated: false,
    role: actor?.role ?? undefined,
    isPending: actors.isPending,
  }
}

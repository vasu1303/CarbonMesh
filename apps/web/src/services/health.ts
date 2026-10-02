import { healthSchema } from '@/schemas/health'
import { apiRequest } from '@/services/api'

export function getHealth({ signal }: { signal: AbortSignal }) {
  return apiRequest('/health', healthSchema, { signal })
}

import { healthSchema } from '@/schemas/health'

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? '/api'

export async function getHealth() {
  const response = await fetch(`${apiBaseUrl}/health`)

  if (!response.ok) {
    throw new Error('CarbonMesh API is unavailable')
  }

  return healthSchema.parse(await response.json())
}

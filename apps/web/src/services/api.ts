import { z } from 'zod'

const errorSchema = z.object({
  detail: z.object({
    code: z.string(),
    message: z.string(),
    trace_id: z.string().optional(),
    retryable: z.boolean().optional(),
  }),
})

export class ApiError extends Error {
  readonly code: string
  readonly retryable: boolean
  readonly traceId?: string

  constructor(
    message: string,
    code: string,
    retryable = false,
    traceId?: string,
  ) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.retryable = retryable
    this.traceId = traceId
  }
}

type RequestOptions = {
  signal: AbortSignal
  params?: Record<string, string | number | boolean | undefined>
  body?: unknown
}

export async function apiRequest<T>(
  path: string,
  schema: z.ZodType<T>,
  options: RequestOptions,
): Promise<T> {
  const base = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')
  const url = new URL(`${base}${path}`, window.location.origin)
  Object.entries(options.params ?? {}).forEach(([key, value]) => {
    if (value !== undefined) url.searchParams.set(key, String(value))
  })
  const timeout = AbortSignal.timeout(20_000)
  try {
    const response = await fetch(url, {
      method: options.body === undefined ? 'GET' : 'POST',
      headers:
        options.body === undefined
          ? { Accept: 'application/json' }
          : {
              Accept: 'application/json',
              'Content-Type': 'application/json',
            },
      body:
        options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: AbortSignal.any([options.signal, timeout]),
    })
    const body: unknown = await response.json().catch(() => null)
    if (!response.ok) {
      const error = errorSchema.safeParse(body)
      if (error.success) {
        const detail = error.data.detail
        throw new ApiError(
          detail.message,
          detail.code,
          detail.retryable,
          detail.trace_id,
        )
      }
      throw new ApiError(
        'The API could not complete this request.',
        `http_${response.status}`,
        response.status >= 500,
      )
    }
    const parsed = schema.safeParse(body)
    if (!parsed.success) {
      throw new ApiError(
        'The API response does not match the supported contract.',
        'invalid_response',
      )
    }
    return parsed.data
  } catch (error) {
    if (options.signal.aborted || error instanceof ApiError) throw error
    throw new ApiError(
      timeout.aborted
        ? 'The API request timed out.'
        : 'Cannot reach the API. Check the backend connection.',
      timeout.aborted ? 'timeout' : 'network_error',
      true,
    )
  }
}

export function retryApiQuery(attempt: number, error: Error) {
  return attempt < 1 && error instanceof ApiError && error.retryable
}

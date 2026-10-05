import { z } from 'zod'

const fieldDetailsSchema = z.union([
  z.record(z.string(), z.unknown()),
  z
    .array(z.record(z.string(), z.unknown()))
    .transform((details) =>
      Object.fromEntries(
        details.map((item, index) => [
          typeof item.field === 'string' ? item.field : `issue_${index + 1}`,
          item.detail ?? item,
        ]),
      ),
    ),
])

const errorSchema = z.object({
  detail: z.object({
    code: z.string(),
    message: z.string(),
    trace_id: z.string().nullable().optional(),
    retryable: z.boolean().optional(),
    field_details: fieldDetailsSchema.optional(),
    terminal_state: z.string().optional(),
  }),
})

export class ApiError extends Error {
  readonly code: string
  readonly retryable: boolean
  readonly traceId?: string
  readonly status?: number
  readonly fieldDetails?: Record<string, unknown>
  readonly terminalState?: string

  constructor(
    message: string,
    code: string,
    retryable = false,
    traceId?: string,
    status?: number,
    fieldDetails?: Record<string, unknown>,
    terminalState?: string,
  ) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.retryable = retryable
    this.traceId = traceId
    this.status = status
    this.fieldDetails = fieldDetails
    this.terminalState = terminalState
  }
}

type RequestOptions = {
  signal: AbortSignal
  params?: Record<string, string | number | boolean | undefined>
  body?: unknown
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
  headers?: Record<string, string>
  timeoutMs?: number
}

export function apiUrl(path: string, params: RequestOptions['params'] = {}) {
  const base = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')
  const url = new URL(`${base}${path}`, window.location.origin)
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined) url.searchParams.set(key, String(value))
  })
  return url
}

export async function apiRequest<T>(
  path: string,
  schema: z.ZodType<T>,
  options: RequestOptions,
): Promise<T> {
  const url = apiUrl(path, options.params)
  const timeout = AbortSignal.timeout(options.timeoutMs ?? 20_000)
  try {
    const response = await fetch(url, {
      method: options.method ?? (options.body === undefined ? 'GET' : 'POST'),
      credentials: 'include',
      cache: 'no-store',
      headers: {
        Accept: 'application/json',
        ...(options.body === undefined
          ? {}
          : { 'Content-Type': 'application/json' }),
        ...options.headers,
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
          detail.trace_id ?? undefined,
          response.status,
          detail.field_details,
          detail.terminal_state,
        )
      }
      throw new ApiError(
        'The API could not complete this request.',
        `http_${response.status}`,
        response.status >= 500,
        undefined,
        response.status,
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

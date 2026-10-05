import { expect, test } from '@playwright/test'
import { mockDashboard } from './dashboard-fixtures'

for (const [label, fields, expected] of [
  ['empty list', [], {}],
  [
    'field list',
    [{ field: 'grid_zone', detail: 'not_found' }],
    { grid_zone: 'not_found' },
  ],
  ['field map', { grid_zone: 'not_found' }, { grid_zone: 'not_found' }],
] as const) {
  test(`safe API errors preserve ${label}, code, trace and retry policy`, async ({
    page,
  }) => {
    await mockDashboard(page)
    let attempts = 0
    await page.route('**/api/contract-error', async (route) => {
      attempts += 1
      await route.fulfill({
        status: 503,
        json: {
          detail: {
            code: 'data_unavailable',
            message: 'Application data is temporarily unavailable.',
            trace_id: 'connection-test',
            retryable: true,
            field_details: fields,
          },
        },
      })
    })
    await page.goto('/dashboard')
    const result = await page.evaluate(async () => {
      const apiModule = '/src/services/api.ts'
      const healthModule = '/src/schemas/health.ts'
      const { apiRequest, ApiError, retryApiQuery } = await import(apiModule)
      const { healthSchema } = await import(healthModule)
      try {
        await apiRequest('/contract-error', healthSchema, {
          signal: new AbortController().signal,
        })
        return null
      } catch (error) {
        if (!(error instanceof ApiError)) throw error
        const failure = error as Error & {
          code: string
          traceId: string
          status: number
          fieldDetails: unknown
        }
        return {
          code: failure.code,
          message: failure.message,
          trace: failure.traceId,
          status: failure.status,
          fields: failure.fieldDetails,
          retryFirst: retryApiQuery(0, failure),
          retrySecond: retryApiQuery(1, failure),
        }
      }
    })
    expect(result).toEqual({
      code: 'data_unavailable',
      message: 'Application data is temporarily unavailable.',
      trace: 'connection-test',
      status: 503,
      fields: expected,
      retryFirst: true,
      retrySecond: false,
    })
    expect(attempts).toBe(1)
  })
}

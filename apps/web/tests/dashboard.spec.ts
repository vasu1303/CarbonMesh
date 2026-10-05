import { expect, test } from '@playwright/test'
import { fixtureFor, id, mockDashboard } from './dashboard-fixtures'
import { formatDecimal } from '../src/features/dashboard/format'

test('canonical reads, source inspection, chart, paging and responsive themes', async ({
  page,
}, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const requests = await mockDashboard(page)
  await page.goto('/')
  await expect(page).toHaveURL(/\/dashboard$/)
  await expect(
    page.getByText('Maverick Manufacturing (synthetic)'),
  ).toBeVisible()
  const measurements = page.getByRole('region', {
    name: 'Verified measurements',
    exact: true,
  })
  await expect(
    measurements.getByText('12,500.125', { exact: true }),
  ).toBeVisible()
  await expect(measurements.locator('.recharts-bar-rectangle')).toHaveCount(6)
  await expect(page.getByText('Stale preview', { exact: true })).toBeVisible()
  await expect(
    page.getByText('Synthetic data', { exact: true }).first(),
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Refresh dashboard', exact: true }),
  ).toBeEnabled()
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true)
  await page.screenshot({
    path: testInfo.outputPath('dashboard.png'),
    fullPage: true,
  })
  await measurements.getByRole('button', { name: 'Next page' }).click()
  await expect(measurements.getByText('7-7 of 7')).toBeVisible()
  await measurements.getByRole('button', { name: 'Previous page' }).click()
  await measurements
    .getByRole('button', { name: 'Inspect measurement M1', exact: true })
    .click()
  const dialog = page.getByRole('dialog')
  await expect(
    dialog.getByText('Measured emissions', { exact: true }),
  ).toBeVisible()
  await dialog.getByRole('tab', { name: 'Lineage' }).click()
  await expect(
    dialog.getByText('Partial lineage.', { exact: false }),
  ).toBeVisible()
  await expect(dialog.locator('.react-flow__node')).toHaveCount(2)
  await dialog.getByRole('button', { name: 'Inspect ledger event', exact: true }).click()
  await expect(
    dialog.getByText('synthetic-factor.txt', { exact: true }),
  ).toBeVisible()
  await expect(
    dialog.getByText('Recorded facts', { exact: true }),
  ).toBeVisible()
  await dialog.getByRole('button', { name: 'Close', exact: true }).click()
  await expect(
    measurements.getByRole('button', {
      name: 'Inspect measurement M1',
      exact: true,
    }),
  ).toBeFocused()
  const queue = page.getByRole('region', { name: 'Approval review queue' })
  await queue.getByRole('button', { name: 'Next page' }).click()
  await expect(queue.getByText('Expired preview')).toBeVisible()
  await page.getByRole('button', { name: /Switch to .* mode/ }).click()
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true)
  expect(requests.some(r => /health|electricity-maps|grid\//.test(r.url.pathname))).toBe(false)
  const paths = new Set(requests.map((r) => r.url.pathname))
  for (const path of [
    '/context/resolve',
    '/measurements',
    '/quality/issues',
    '/approvals',

  ])
    expect(paths.has(`/api${path}`)).toBe(true)
  for (const request of requests) {
    expect(request.method).toBe(
      request.url.pathname === '/api/context/resolve' ? 'POST' : 'GET',
    )
    if (
      request.method === 'GET' &&
      !['/api/health', '/api/auth/session'].includes(request.url.pathname)
    )
      expect(request.url.searchParams.get('company_id')).toBe(id(1))
  }
  const measurementRequest = requests.find(
    (r) => r.url.pathname === '/api/measurements',
  )!
  expect(measurementRequest.url.searchParams.get('site_id')).toBe(id(2))
  expect(measurementRequest.url.searchParams.get('reporting_period_id')).toBe(
    id(3),
  )
  expect(measurementRequest.url.searchParams.get('status')).toBe('verified')
  expect(errors).toEqual([])
})

test('slow measurements do not block other panels', async ({ page }) => {
  let release!: () => void
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  await mockDashboard(page, async (route, url) => {
    if (url.pathname !== '/api/measurements') return
    await gate
    await route.fulfill({ json: fixtureFor(url) })
    return true
  })
  await page.goto('/dashboard')
  const measurements = page.getByRole('region', {
    name: 'Verified measurements',
    exact: true,
  })
  try {
    await expect(
      measurements.getByRole('status', { name: 'Loading data' }),
    ).toBeVisible()
    await expect(
      page.getByText('Synthetic data', { exact: true }).first(),
    ).toBeVisible()
    await expect(
      page.getByText('Quantity is missing in the synthetic activity row.'),
    ).toBeVisible()
  } finally {
    release()
  }
  await expect(
    measurements.getByText('12,500.125', { exact: true }),
  ).toBeVisible()
})

test('empty results are not fabricated values', async ({ page }) => {
  await mockDashboard(page, undefined, true)
  await page.goto('/dashboard')
  await expect(
    page.getByText('No verified result', { exact: true }),
  ).toBeVisible()
  await expect(page.getByText('Latest grid intensity', { exact: true })).toHaveCount(0)
  await expect(
    page.getByText('No pending approval previews', { exact: true }),
  ).toBeVisible()
  await page.getByText('Recent activity', { exact: true }).click()
  await expect(
    page.getByText('No ledger events', { exact: true }),
  ).toBeVisible()
  await expect(page.locator('.recharts-bar-rectangle')).toHaveCount(0)
})

test('invalid response fails closed, retry recovers, failed refresh keeps a warning', async ({
  page,
}) => {
  let mode = 'invalid'
  await mockDashboard(page, async (route, url) => {
    if (url.pathname !== '/api/measurements' || mode === 'success') return
    if (mode === 'invalid')
      await route.fulfill({ json: { items: [], total: 'not-a-number' } })
    else
      await route.fulfill({
        status: 503,
        json: {
          detail: {
            code: 'data_unavailable',
            message: 'Test data unavailable.',
            retryable: false,
          },
        },
      })
    return true
  })
  await page.goto('/dashboard')
  const measurements = page.getByRole('region', {
    name: 'Verified measurements',
    exact: true,
  })
  await expect(
    measurements.getByText(
      'The API response does not match the supported contract.',
    ),
  ).toBeVisible()
  await expect(
    page.getByText('Synthetic data', { exact: true }).first(),
  ).toBeVisible()
  mode = 'success'
  await measurements.getByRole('button', { name: 'Retry', exact: true }).click()
  await expect(
    measurements.getByText('12,500.125', { exact: true }),
  ).toBeVisible()
  mode = 'failure'
  await measurements
    .getByRole('button', { name: 'Refresh measurements', exact: true })
    .click()
  await expect(
    measurements.getByText('Refresh failed. Showing previously fetched data.'),
  ).toBeVisible()
  await expect(
    measurements.getByText('12,500.125', { exact: true }),
  ).toBeVisible()
})

test('quality filters use API severity without changing company-wide summary', async ({
  page,
}) => {
  const requests = await mockDashboard(page)
  await page.goto('/dashboard')
  const quality = page.getByRole('region', {
    name: 'Data quality',
    exact: true,
  })
  await quality.getByRole('tab', { name: 'error', exact: true }).click()
  await expect(
    quality.getByText('Showing 1 of 1 open error issues'),
  ).toBeVisible()
  expect(
    requests.some(
      (r) =>
        r.url.searchParams.get('severity') === 'error' &&
        r.url.searchParams.get('status') === 'open',
    ),
  ).toBe(true)
  await quality.getByRole('tab', { name: 'info', exact: true }).click()
  await expect(
    quality.getByText('No open issues', { exact: true }),
  ).toBeVisible()
})

test('decimal formatting preserves exact precision and negative signs', () => {
  expect(formatDecimal('1234567890123456789.12345678')).toBe(
    '1,234,567,890,123,456,789.12345678',
  )
  expect(formatDecimal('-1234.5000')).toBe('-1,234.5')
  expect(formatDecimal('0.0000')).toBe('0')
  expect(formatDecimal('1E-8')).toBe('1E-8')
})

test('a measurement from a different context is not displayed', async ({
  page,
}) => {
  await mockDashboard(page, async (route, url) => {
    if (url.pathname !== '/api/measurements') return
    const result = fixtureFor(url) as { items: { site_id: string }[] }
    await route.fulfill({
      json: {
        ...result,
        items: result.items.map((item) => ({ ...item, site_id: id(999) })),
      },
    })
    return true
  })
  await page.goto('/dashboard')
  await expect(
    page
      .getByRole('region', { name: 'Verified measurements', exact: true })
      .getByText('The API response does not match the supported contract.'),
  ).toBeVisible()
  await expect(page.getByText('12,500.125', { exact: true })).toHaveCount(0)
  await expect(
    page.getByText('Synthetic data', { exact: true }).first(),
  ).toBeVisible()
})

test('narrow viewport and source dialog remain usable with long labels', async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 720 })
  await mockDashboard(page)
  await page.goto('/dashboard')
  await expect(
    page.getByRole('button', { name: 'Refresh dashboard', exact: true }),
  ).toBeEnabled()
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true)
  await page
    .getByRole('region', { name: 'Verified measurements', exact: true })
    .getByRole('button', { name: 'Inspect measurement M1', exact: true })
    .click()
  const dialog = page.getByRole('dialog')
  await expect(
    dialog.getByText('Measured emissions', { exact: true }),
  ).toBeVisible()
  expect(
    await dialog.evaluate(
      (element) => element.scrollWidth <= element.clientWidth,
    ),
  ).toBe(true)
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeVisible()
})

test('generic dispatch approvals render without invented procurement values', async ({
  page,
}) => {
  await mockDashboard(page, async (route, url) => {
    if (url.pathname !== '/api/approvals') return
    await route.fulfill({
      json: {
        items: [
          {
            id: id(9001),
            company_id: id(1),
            target_type: 'dispatch_recommendation',
            target_id: id(9002),
            requester_name: 'Synthetic planner',
            status: 'pending',
            preview_hash: 'a'.repeat(64),
            analysis_signature: 'b'.repeat(64),
            expires_at: '2099-01-01T00:00:00Z',
            created_at: '2026-10-01T00:00:00Z',
            expired: false,
            preview_current: true,
            recommended_product_name: null,
            supplier_name: null,
            avoided_kgco2e: null,
            cost_delta_pct: null,
          },
        ],
        total: 1,
        limit: 2,
        offset: 0,
      },
    })
    return true
  })
  await page.goto('/dashboard')
  const queue = page.getByRole('region', { name: 'Approval review queue' })
  await expect(queue.getByText('Requested by Synthetic planner')).toBeVisible()
  await expect(queue.getByText('Cost change', { exact: true })).toHaveCount(0)
  await queue.getByRole('button', { name: 'Inspect preview' }).click()
  await expect(
    page.getByRole('dialog').getByText(id(9002), { exact: true }),
  ).toHaveCount(0)
  await expect(page.getByRole('dialog').getByRole('link', { name: 'Review approval' })).toHaveAttribute('href', `/approvals?approval=${id(9001)}`)
  await expect(
    page.getByRole('dialog').getByText('Product / supplier', { exact: true }),
  ).toHaveCount(0)
})

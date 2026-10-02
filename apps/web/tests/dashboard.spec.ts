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
    page.getByText('Synthetic fixture', { exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Refresh data', exact: true }),
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
    dialog.getByText('synthetic-row-1', { exact: true }),
  ).toBeVisible()
  await dialog.getByRole('tab', { name: 'Lineage' }).click()
  await expect(
    dialog.getByText('Partial lineage:', { exact: false }),
  ).toBeVisible()
  await dialog.getByRole('tab', { name: 'Verified facts' }).click()
  await dialog.getByRole('button', { name: 'Open ledger fact' }).click()
  await expect(
    dialog.getByText('synthetic-factor.txt', { exact: true }),
  ).toBeVisible()
  await expect(
    dialog.getByText('Persisted payload', { exact: true }),
  ).toBeVisible()
  await dialog.getByRole('button', { name: 'Close', exact: true }).click()
  await expect(
    measurements.getByRole('button', {
      name: 'Inspect measurement M1',
      exact: true,
    }),
  ).toBeFocused()
  const queue = page.getByRole('region', { name: 'Procurement review queue' })
  await queue.getByRole('button', { name: 'Next page' }).click()
  await expect(queue.getByText('Expired preview')).toBeVisible()
  await page.getByRole('button', { name: /Switch to .* mode/ }).click()
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true)
  const paths = new Set(requests.map((r) => r.url.pathname))
  for (const path of [
    '/context/resolve',
    '/measurements',
    '/quality/issues',
    '/approvals',
    '/measurement/grid/latest',
    '/procurement/suppliers',
    '/procurement/products',
    '/assurance/standards',
    '/dispatch/loads',
    '/ledger/events',
  ])
    expect(paths.has(`/api${path}`)).toBe(true)
  for (const request of requests) {
    expect(request.method).toBe(
      request.url.pathname === '/api/context/resolve' ? 'POST' : 'GET',
    )
    if (request.method === 'GET' && request.url.pathname !== '/api/health')
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
      page.getByText('Synthetic fixture', { exact: true }),
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
  await expect(
    page.getByText('No cached grid data', { exact: true }),
  ).toBeVisible()
  await expect(
    page.getByText('No pending procurement previews', { exact: true }),
  ).toBeVisible()
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
    page.getByText('Synthetic fixture', { exact: true }),
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
    page.getByText('Synthetic fixture', { exact: true }),
  ).toBeVisible()
})

test('narrow viewport and source dialog remain usable with long labels', async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 720 })
  await mockDashboard(page)
  await page.goto('/dashboard')
  await expect(
    page.getByRole('button', { name: 'Refresh data', exact: true }),
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
    dialog.getByText('synthetic-row-1', { exact: true }),
  ).toBeVisible()
  expect(
    await dialog.evaluate(
      (element) => element.scrollWidth <= element.clientWidth,
    ),
  ).toBe(true)
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeVisible()
})

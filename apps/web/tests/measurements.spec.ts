import { expect, test, type Page, type Route } from '@playwright/test'
import fixture from './fixtures/measurements.synthetic.json' with { type: 'json' }

const first = fixture.records[0].summary

test('dedicated measurement detail links facts, calculations and source documents', async ({
  page,
}, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await mockApi(page)
  await page.goto('/measurement')
  await page
    .getByRole('link', { name: `Open measurement ${first.id}`, exact: true })
    .click()
  await expect(
    page.getByRole('heading', { name: 'Measurement record', exact: true }),
  ).toBeVisible()
  await expect(
    page.getByText('Confidence breakdown', { exact: true }),
  ).toBeVisible()
  await page.getByRole('tab', { name: 'Calculations', exact: true }).click()
  await expect(page.getByText('18,000 kgCO2e', { exact: true })).toBeVisible()
  await expect(page.locator('.recharts-line')).toHaveCount(1)
  await expect(
    page.getByRole('link', { name: 'Document', exact: true }),
  ).toHaveAttribute(
    'href',
    `/data?document=${fixture.records[0].detail.inputs[0].source_document_id}`,
  )
  await page.screenshot({
    path: testInfo.outputPath('measurement-detail.png'),
    fullPage: true,
  })
  await page.getByRole('tab', { name: 'Evidence', exact: true }).click()
  await page
    .getByLabel('Source records', { exact: true })
    .selectOption('factors')
  await expect(
    page.getByText('Synthetic recycled-aluminium factor', { exact: true }),
  ).toBeVisible()
  await page.getByRole('tab', { name: 'Lineage', exact: true }).click()
  await expect(
    page.getByRole('link', { name: 'Inspect ledger event' }).first(),
  ).toHaveAttribute('href', /\/ledger\?event=/)
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true)
  expect(errors).toEqual([])
})

test('measurement calculation submits the explicit API contract and opens its persisted result', async ({
  page,
}) => {
  let payload: Record<string, unknown> | undefined
  await mockApi(page, async (route, url) => {
    if (url.pathname !== '/api/measurement/calculate') return
    expect(route.request().method()).toBe('POST')
    payload = route.request().postDataJSON()
    await route.fulfill({
      json: {
        ...fixture.records[0].detail,
        terminal_state: 'completed',
        trace_id: 'test-measurement',
        idempotent: false,
      },
    })
    return true
  })
  await page.goto('/measurement')
  await page.getByRole('button', { name: 'Calculate', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await dialog
    .getByLabel('Material code', { exact: true })
    .fill('RECYCLED_ALUMINIUM')
  await dialog.getByRole('button', { name: 'Calculate', exact: true }).click()
  await expect(
    page.getByRole('heading', { name: 'Measurement record', exact: true }),
  ).toBeVisible()
  expect(payload).toEqual({
    company_id: fixture.scope.company_id,
    site_id: fixture.scope.site_id,
    reporting_period_id: fixture.scope.reporting_period_id,
    actor_id: '00000000-0000-4000-8000-000000000004',
    material_code: 'RECYCLED_ALUMINIUM',
  })
})

test('a rejected Scope 2 calculation stays explicit without a fallback result', async ({
  page,
}) => {
  await mockApi(page, async (route, url) => {
    if (url.pathname !== '/api/measurement/calculate') return
    expect(route.request().postDataJSON().output_metric_key).toBe(
      'emissions.scope2.location_based',
    )
    expect(route.request().postDataJSON().material_code).toBeUndefined()
    await route.fulfill({
      status: 422,
      json: {
        detail: {
          code: 'missing_grid_intervals',
          message: 'Grid intervals are missing.',
          retryable: false,
          trace_id: 'test-grid-missing',
        },
      },
    })
    return true
  })
  await page.goto('/measurement')
  await page.getByRole('button', { name: 'Calculate', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await dialog
    .getByLabel('Measurement path', { exact: true })
    .selectOption('electricity')
  await dialog.getByRole('button', { name: 'Calculate', exact: true }).click()
  await expect(
    dialog.getByText('Grid intervals are missing.', { exact: true }),
  ).toBeVisible()
  await expect(
    dialog.getByText('Trace: test-grid-missing', { exact: true }),
  ).toBeVisible()
  await expect(page).toHaveURL(/\/measurement$/)
})

function responseFor(url: URL) {
  if (url.pathname === '/api/auth/session')
    return {
      company_id: fixture.scope.company_id,
      actor_id: '00000000-0000-4000-8000-000000000004',
      role: 'sustainability_analyst',
    }
  if (url.pathname === '/api/health')
    return { status: 'ok', service: 'CarbonMesh API' }
  if (url.pathname === '/api/context/resolve') return fixture.context
  if (url.pathname === '/api/semantic/metrics') return fixture.metrics
  if (url.pathname === `/api/measurements/${first.id}/breakdown`) {
    const detail = fixture.records[0].detail
    return {
      measurement_id: first.id,
      metric_key: first.metric_key,
      status: first.status,
      unit: 'kgCO2e',
      total_kgco2e: detail.value_kgco2e,
      facts: detail.facts,
      items: detail.calculations.map((item) => ({
        calculation_id: item.id,
        activity_record_id: item.activity_record_id,
        raw_activity_record_id: detail.inputs[0].raw_activity_record_id,
        source_document_id: detail.inputs[0].source_document_id,
        interval_start: null,
        activity_date: detail.inputs[0].activity_date,
        material_code: detail.inputs[0].material_code,
        quantity: item.normalized_quantity_kg,
        quantity_unit: 'kg',
        emissions_kgco2e: item.emissions_kgco2e,
        emission_factor_id: item.emission_factor_id,
        grid_intensity_point_id: null,
        output_hash: item.output_hash,
      })),
    }
  }
  if (url.pathname === '/api/measurements') {
    const status = url.searchParams.get('status')
    const category = url.searchParams.get('category')
    const limit = Number(url.searchParams.get('limit') ?? 10)
    const offset = Number(url.searchParams.get('offset') ?? 0)
    const items = fixture.records
      .map((record) => record.summary)
      .filter((item) => !status || item.status === status)
      .filter((item) => !category || item.category === category)
    return {
      items: items.slice(offset, offset + limit),
      total: items.length,
      limit,
      offset,
    }
  }
  const match = url.pathname.match(/^\/api\/measurements\/([^/]+)(\/lineage)?$/)
  const record =
    match && fixture.records.find((item) => item.summary.id === match[1])
  if (record && match) return match[2] ? record.lineage : record.detail
  return undefined
}

async function mockApi(
  page: Page,
  override?: (route: Route, url: URL) => Promise<boolean | void>,
) {
  const calls: { url: URL; method: string }[] = []
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    calls.push({ url, method: route.request().method() })
    if (override && (await override(route, url))) return
    const result = responseFor(url)
    if (!result) throw new Error(`Unexpected API request: ${url.pathname}`)
    await route.fulfill({ json: result })
  })
  return calls
}

test('measurements use canonical reads, filters, paging and source inspection', async ({
  page,
}, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const calls = await mockApi(page)
  await page.goto('/measurement?limit=5')
  await expect(
    page.getByRole('heading', { name: 'Measurements', exact: true }),
  ).toBeVisible()
  const records = page.getByRole('region', { name: 'Measurement records' })
  await expect(records.getByText('1-5 of 6')).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Refresh measurements', exact: true }),
  ).toBeEnabled()
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true)
  await page.screenshot({
    path: testInfo.outputPath('measurements.png'),
    fullPage: true,
  })
  await records.getByRole('button', { name: 'Next page' }).click()
  await expect(records.getByText('6-6 of 6')).toBeVisible()
  await expect(
    records.getByText('Not supported', { exact: true }),
  ).toBeVisible()
  await page.getByLabel('Status', { exact: true }).selectOption('verified')
  await expect(records.getByText('1-2 of 2')).toBeVisible()
  await page
    .getByLabel('Category', { exact: true })
    .selectOption(first.category)
  await records
    .getByRole('button', { name: `View facts ${first.id}`, exact: true })
    .click()
  const dialog = page.getByRole('dialog')
  await expect(
    dialog.getByText('Confidence breakdown', { exact: true }),
  ).toBeVisible()
  await dialog.getByRole('tab', { name: 'Evidence', exact: true }).click()
  await expect(
    dialog.getByText('synthetic-ui-factor.txt', { exact: true }),
  ).toBeVisible()
  await dialog.getByRole('tab', { name: 'Lineage', exact: true }).click()
  await expect(
    dialog.getByText('Synthetic raw activity', { exact: true }),
  ).toBeVisible()
  await dialog.getByRole('button', { name: 'Close', exact: true }).click()
  await expect(
    records.getByRole('button', {
      name: `View facts ${first.id}`,
      exact: true,
    }),
  ).toBeFocused()
  expect(
    calls.some(
      (call) => call.url.searchParams.get('category') === first.category,
    ),
  ).toBe(true)
  for (const call of calls) {
    expect(call.method).toBe(
      call.url.pathname === '/api/context/resolve' ? 'POST' : 'GET',
    )
    if (
      call.method === 'GET' &&
      !['/api/health', '/api/auth/session'].includes(call.url.pathname)
    )
      expect(call.url.searchParams.get('company_id')).toBe(
        fixture.scope.company_id,
      )
    if (call.url.pathname === '/api/measurements') {
      expect(call.url.searchParams.get('site_id')).toBe(fixture.scope.site_id)
      expect(call.url.searchParams.get('reporting_period_id')).toBe(
        fixture.scope.reporting_period_id,
      )
    }
  }
  expect(errors).toEqual([])
})

test('legacy preview URL still uses API data and supports chart inspection', async ({
  page,
}, testInfo) => {
  const calls = await mockApi(page)
  await page.goto('/measurement?source=preview&view=chart')
  await expect(
    page.getByText('Synthetic data / API', { exact: true }),
  ).toBeVisible()
  const records = page.getByRole('region', { name: 'Measurement records' })
  await expect(records.locator('.recharts-bar-rectangle')).toHaveCount(5)
  await records.locator('.recharts-bar-rectangle').first().hover()
  await expect(records.locator('.recharts-tooltip-wrapper')).toContainText(
    '18,000 kgCO2e',
  )
  await page.screenshot({
    path: testInfo.outputPath('measurements-chart.png'),
    fullPage: true,
  })
  await records
    .getByRole('button', { name: `R1 / ${first.id.slice(-12)}`, exact: true })
    .click()
  await expect(
    page.getByRole('dialog').getByText('Confidence breakdown', { exact: true }),
  ).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(
    page.getByRole('tab', { name: 'Synthetic preview', exact: true }),
  ).toHaveCount(0)
  await expect(
    page.getByText('Synthetic data / API', { exact: true }),
  ).toBeVisible()
  expect(calls.some((call) => call.url.pathname === '/api/measurements')).toBe(
    true,
  )
  expect(new URL(page.url()).searchParams.has('record')).toBe(false)
})

test('record loading is independent of summary counts and context', async ({
  page,
}) => {
  let release!: () => void
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  await mockApi(page, async (route, url) => {
    if (
      url.pathname !== '/api/measurements' ||
      url.searchParams.get('limit') === '1'
    )
      return
    await gate
    await route.fulfill({ json: responseFor(url) })
    return true
  })
  await page.goto('/measurement')
  try {
    await expect(
      page
        .getByRole('region', { name: 'Measurement records' })
        .getByRole('status', { name: 'Loading data' }),
    ).toBeVisible()
    await expect(
      page.getByRole('button', { name: 'Show verified records' }),
    ).toHaveText('2')
    await expect(
      page.getByText('Maverick Manufacturing (synthetic)'),
    ).toBeVisible()
  } finally {
    release()
  }
  await expect(
    page.getByRole('button', { name: `View facts ${first.id}`, exact: true }),
  ).toBeVisible()
})

test('failed refresh is explicit and a missing record never loads preview data', async ({
  page,
}) => {
  let failure = false
  await mockApi(page, async (route, url) => {
    if (!failure || !url.pathname.startsWith('/api/measurements')) return
    await route.fulfill({
      status: 503,
      json: {
        detail: {
          code: 'measurement_service_unavailable',
          message: 'Test service unavailable.',
          retryable: false,
        },
      },
    })
    return true
  })
  await page.goto('/measurement')
  await expect(
    page.getByRole('button', { name: `View facts ${first.id}`, exact: true }),
  ).toBeVisible()
  failure = true
  await page
    .getByRole('button', { name: 'Refresh measurements', exact: true })
    .click()
  await expect(
    page
      .getByRole('region', { name: 'Measurement records' })
      .getByText('Refresh failed. Showing previously fetched data.'),
  ).toBeVisible()
  await page
    .getByRole('button', { name: `View facts ${first.id}`, exact: true })
    .click()
  await expect(
    page.getByRole('dialog').getByText('Test service unavailable.'),
  ).toBeVisible()
  await expect(
    page.getByRole('dialog').getByText('Confidence breakdown', { exact: true }),
  ).toHaveCount(0)
})

test('empty results, invalid identifiers and stale statuses are distinct', async ({
  page,
}) => {
  await mockApi(page)
  await page.goto(
    '/measurement?source=preview&category=nonexistent&record=bad-id',
  )
  await expect(
    page.getByText('No measurements found', { exact: true }),
  ).toBeVisible()
  await expect(
    page.getByText('The measurement identifier is invalid.'),
  ).toBeVisible()
  await page.getByRole('button', { name: 'Dismiss', exact: true }).click()
  await page.getByRole('button', { name: 'Clear filters', exact: true }).click()
  await expect(page.getByLabel('Category', { exact: true })).toHaveValue('')
  await page
    .getByRole('button', { name: 'Show superseded records', exact: true })
    .click()
  await expect(
    page
      .getByRole('region', { name: 'Measurement records' })
      .getByText('1-1 of 1'),
  ).toBeVisible()
  await page
    .getByRole('button', {
      name: `View facts ${fixture.records[3].summary.id}`,
      exact: true,
    })
    .click()
  await expect(
    page.getByRole('dialog').getByText('Historical result.', { exact: false }),
  ).toBeVisible()
})

test('scope mismatch fails closed; filters survive refresh and browser history', async ({
  page,
}) => {
  let mismatch = false
  await mockApi(page, async (route, url) => {
    if (!mismatch || url.pathname !== '/api/measurements') return
    const data = responseFor(url) as { items: (typeof first)[] }
    await route.fulfill({
      json: {
        ...data,
        items: data.items.map((item) => ({
          ...item,
          site_id: '00000000-0000-4000-8000-000000009999',
        })),
      },
    })
    return true
  })
  await page.goto('/measurement')
  await page.getByLabel('Status', { exact: true }).selectOption('draft')
  await page.reload()
  await expect(page.getByLabel('Status', { exact: true })).toHaveValue('draft')
  await page.getByLabel('Status', { exact: true }).selectOption('verified')
  await page.goBack()
  await expect(page.getByLabel('Status', { exact: true })).toHaveValue('draft')
  mismatch = true
  await page.reload()
  await expect(
    page
      .getByRole('region', { name: 'Measurement records' })
      .getByText('The API response does not match the supported contract.'),
  ).toBeVisible()
})

test('measurement inspection fits a narrow viewport', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 })
  await mockApi(page)
  await page.goto(`/measurement?source=preview&record=${first.id}`)
  const dialog = page.getByRole('dialog')
  await expect(
    dialog.getByText('Confidence breakdown', { exact: true }),
  ).toBeVisible()
  expect(
    await dialog.evaluate(
      (element) => element.scrollWidth <= element.clientWidth,
    ),
  ).toBe(true)
  await page.keyboard.press('Escape')
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true)
  await expect(
    page
      .getByRole('navigation', { name: 'Mobile navigation' })
      .getByRole('link', { name: 'Measurements', exact: true }),
  ).toHaveAttribute('aria-current', 'page')
})

test('confidence v2 and hourly Scope 2 evidence use recorded API fields', async ({
  page,
}) => {
  const original = fixture.records[0].detail
  const pointId = '00000000-0000-4000-8000-000000009801'
  await mockApi(page, async (route, url) => {
    if (url.pathname !== `/api/measurements/${first.id}`) return
    await route.fulfill({
      json: {
        ...original,
        confidence_breakdown: {
          version: '2.0.0',
          source_quality: '0.90',
          method_fit: '1',
          temporal_match: '1',
          completeness: '1',
          weights: {
            source_quality: '0.35',
            method_fit: '0.25',
            temporal_match: '0.20',
            completeness: '0.20',
          },
          overall: '0.965',
        },
        metric_key: 'emissions.scope2.location_based',
        confidence: '0.965',
        formula: 'kWh * gCO2e_per_kWh / 1000',
        calculation_run: {
          ...original.calculation_run,
          method_version: '2.0.0',
        },
        inputs: original.inputs.map((input) => ({
          ...input,
          source_unit: 'kWh',
          source_quantity: '100',
          normalized_quantity_kg: null,
          normalized_quantity_kwh: '100',
          interval_start: original.created_at,
        })),
        factors: [],
        grid_points: [
          {
            id: pointId,
            provider: 'Synthetic grid provider',
            zone: 'IN-SO',
            observed_at: original.created_at,
            temporal_granularity: 'hourly',
            intensity_gco2e_per_kwh: '412.125',
            is_estimated: true,
            method_version: 'test-v1',
            point_hash: original.output_hash,
            evidence: original.factors[0].evidence,
          },
        ],
        coverage: {
          interval_start: original.created_at,
          interval_end: original.created_at,
          observed_hours: 1,
          reporting_period_hours: 2208,
          reporting_period_fraction: '0.00045290',
          full_reporting_period: false,
        },
        calculations: original.calculations.map((item) => ({
          ...item,
          emission_factor_id: null,
          normalized_quantity_kg: null,
          factor_kgco2e_per_kg: null,
          grid_intensity_point_id: pointId,
          normalized_quantity_kwh: '100',
          intensity_gco2e_per_kwh: '412.125',
        })),
      },
    })
    return true
  })
  await page.goto(`/measurement?record=${first.id}`)
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('Method fit', { exact: true })).toBeVisible()
  await expect(dialog.getByText('Factor recency', { exact: true })).toHaveCount(
    0,
  )
  await expect(
    dialog.getByText('Partial reporting period', { exact: true }),
  ).toBeVisible()
  await dialog.getByRole('tab', { name: 'Evidence', exact: true }).click()
  await expect(
    dialog.getByText('Hourly grid evidence', { exact: true }),
  ).toBeVisible()
  await expect(
    dialog.getByText('Synthetic grid provider / IN-SO', { exact: true }),
  ).toBeVisible()
  await expect(dialog.getByText('412.125', { exact: true })).toHaveCount(2)
})

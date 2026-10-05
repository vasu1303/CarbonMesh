import { expect, test, type Locator } from '@playwright/test'
import { id } from './dashboard-fixtures'
import {
  documentId,
  factor,
  importId,
  importResult,
  mockIntake,
  qualityIssues,
  sourceReceipt,
  time,
} from './intake-quality-fixtures'

async function supplyActor(region: Locator) {
  await region.getByLabel('Requested by', { exact: true }).selectOption({ label: 'Synthetic analyst / active' })
}

test('import deep link reads bounded status, provenance and quality links without commands', async ({
  page,
}) => {
  const calls = await mockIntake(page)
  await page.goto(`/data?import=${importId}`)
  const status = page.getByRole('region', {
    name: 'Import status',
    exact: true,
  })
  await expect(
    status.getByText('Completed with errors', { exact: true }),
  ).toBeVisible()
  await expect(
    status.getByText('Synthetic source', { exact: true }),
  ).toBeVisible()
  await expect(
    status.getByText('Showing 2 issues', { exact: false }),
  ).toBeVisible()
  await expect(
    status.getByRole('link', { name: 'Open source document' }),
  ).toHaveAttribute('href', `/data?document=${documentId}`)
  await status.getByRole('link', { name: 'Review quality checks' }).click()
  await expect(page).toHaveURL(new RegExp(`/quality\\?import_id=${importId}`))
  await expect(page.getByLabel('Import', { exact: true })).toHaveValue(importId)
  expect(
    calls
      .filter((call) => call.method !== 'GET')
      .every((call) => call.url.pathname === '/api/context/resolve'),
  ).toBe(true)
})

test('file import retains raw decimals and retry identity until its payload changes', async ({
  page,
}) => {
  const bodies: Record<string, unknown>[] = []
  await mockIntake(page, async (route, url) => {
    if (url.pathname !== '/api/activities/import') return
    bodies.push(route.request().postDataJSON() as Record<string, unknown>)
    await route.fulfill(
      bodies.length < 3
        ? {
            status: 503,
            json: {
              detail: {
                code: 'database_unavailable',
                message: 'Synthetic transient import failure.',
                retryable: false,
              },
            },
          }
        : { status: 201, json: importResult },
    )
    return true
  })
  await page.goto('/data')
  const form = page.getByRole('region', { name: 'New import', exact: true })
  await supplyActor(form)
  await form
    .getByLabel('Source name', { exact: true })
    .fill('Synthetic hourly source')
  await form.getByLabel('Metric').selectOption(id(101))
  await form.getByLabel('This file contains synthetic data').check()
  const original =
    '[{"timestamp":"2026-07-01T00:00:00Z","kwh":123456789012.123456}]'
  await form
    .getByLabel('Import file', { exact: true })
    .setInputFiles({
      name: 'synthetic.json',
      mimeType: 'application/json',
      buffer: Buffer.from(original),
    })
  expect(bodies).toHaveLength(0)
  await form.getByRole('button', { name: 'Upload and check', exact: true }).click()
  await expect(
    form.getByText('Synthetic transient import failure.'),
  ).toBeVisible()
  await form.getByRole('button', { name: 'Upload and check', exact: true }).click()
  await expect.poll(() => bodies.length).toBe(2)
  await expect(
    form.getByText('Synthetic transient import failure.'),
  ).toBeVisible()
  expect(bodies[0].idempotency_key).toBe(bodies[1].idempotency_key)
  expect(bodies[0].actor_id).toBe(id(4))
  expect(bodies[0].content).toBe(original)
  expect(bodies[0].company_id).toBe(id(1))
  expect(bodies[0].site_id).toBe(id(2))
  expect(bodies[0].is_synthetic).toBe(true)
  await form
    .getByLabel('Source name', { exact: true })
    .fill('Synthetic changed source')
  await form.getByRole('button', { name: 'Upload and check', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`import=${importId}`))
  expect(bodies[2].idempotency_key).not.toBe(bodies[1].idempotency_key)
})

test('supplier imports use the canonical envelope and no activity scope fields', async ({
  page,
}) => {
  let body: Record<string, unknown> | undefined
  await mockIntake(page, async (route, url) => {
    if (url.pathname !== '/api/imports/suppliers') return
    body = route.request().postDataJSON() as Record<string, unknown>
    await route.fulfill({
      status: 201,
      json: { ...importResult, import_type: 'suppliers' },
    })
    return true
  })
  await page.goto('/data')
  const form = page.getByRole('region', { name: 'New import', exact: true })
  await form.getByLabel('Import type').selectOption('suppliers')
  await form.getByLabel('Requested by', { exact: true }).selectOption({ label: 'Synthetic approver / active' })
  await form
    .getByLabel('Source name', { exact: true })
    .fill('Synthetic supplier file')
  await form.getByLabel('This file contains synthetic data').check()
  const csv = 'supplier_code,supplier_name\nSYNTHETIC,Synthetic supplier\n'
  await form
    .getByLabel('Import file', { exact: true })
    .setInputFiles({
      name: 'synthetic.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from(csv),
    })
  await form.getByRole('button', { name: 'Upload and check', exact: true }).click()
  await expect.poll(() => body).toBeTruthy()
  expect(body?.content).toBe(csv)
  expect(body?.actor_id).toBe(id(6004))
  expect(body?.is_synthetic).toBe(true)
  expect(body).not.toHaveProperty('site_id')
  expect(body).not.toHaveProperty('metric_definition_id')
})

test('oversized imports and malformed UUIDs are blocked before any command', async ({
  page,
}) => {
  const calls = await mockIntake(page)
  await page.goto('/data?import=invalid')
  await expect(
    page.getByText('This import link is invalid. Select an import above.'),
  ).toBeVisible()
  const form = page.getByRole('region', { name: 'New import', exact: true })
  await form
    .getByLabel('Source name', { exact: true })
    .fill('Synthetic oversized import')
  await form.getByLabel('Metric').selectOption(id(101))
  await form
    .getByLabel('Import file', { exact: true })
    .setInputFiles({
      name: 'synthetic.csv',
      mimeType: 'text/csv',
      buffer: Buffer.alloc(5 * 1024 * 1024 + 1, 'x'),
    })
  await form.getByRole('button', { name: 'Upload and check', exact: true }).click()
  await expect(
    form.getByText('Import files must be non-empty and no larger than 5 MiB.'),
  ).toBeVisible()
  expect(
    calls.some((call) => call.url.pathname === '/api/activities/import'),
  ).toBe(false)
  expect(
    calls.some((call) => call.url.pathname === '/api/imports/invalid'),
  ).toBe(false)
})

test('document deep link downloads original content and indexes only on submit', async ({
  page,
}) => {
  await page.context().addCookies([
    { name: 'carbonmesh_session', value: 'expired-demo-session', url: 'http://127.0.0.1:3100' },
  ])
  let indexes = 0
  await mockIntake(page, async (route, url) => {
    if (url.pathname === `/api/sources/${documentId}/content`) {
      const headers = await route.request().allHeaders()
      expect(headers.cookie).toBeUndefined()
      expect(headers.authorization).toBeUndefined()
      await route.fulfill({
        body: 'Synthetic evidence',
        contentType: 'application/octet-stream',
        headers: {
          'Content-Disposition': "attachment; filename*=UTF-8''synthetic.txt",
        },
      })
      return true
    }
    if (url.pathname === `/api/sources/${documentId}/index`) {
      indexes++
      expect(route.request().postDataJSON()).toEqual({
        company_id: id(1),
        actor_id: id(4),
      })
      await route.fulfill({
        json: {
          document_id: documentId,
          embedding_model_id: 'synthetic-embedding',
          indexed_count: 1,
          replayed: false,
        },
      })
      return true
    }
  })
  await page.goto(`/data?document=${documentId}`)
  const source = page.getByRole('region', {
    name: 'Source document',
    exact: true,
  })
  await expect(source.getByText(documentId, { exact: true })).toHaveCount(0)
  await expect(source.getByRole('button', { name: 'Download source content' })).toBeEnabled()
  expect(indexes).toBe(0)
  const download = page.waitForEvent('download')
  await source.getByRole('button', { name: 'Download source content' }).click()
  expect((await download).suggestedFilename()).toBe('synthetic.txt')
  await supplyActor(source)
  await source
    .getByRole('button', { name: 'Index document', exact: true })
    .click()
  await expect(
    source.getByText('Index complete: 1 evidence items'),
  ).toBeVisible()
  expect(indexes).toBe(1)
})

test('source upload encodes the actual selected bytes and exposes API evidence IDs', async ({
  page,
}) => {
  let body: Record<string, unknown> | undefined
  await mockIntake(page, async (route, url) => {
    if (url.pathname !== '/api/sources/upload') return
    body = route.request().postDataJSON() as Record<string, unknown>
    await route.fulfill({ status: 201, json: sourceReceipt })
    return true
  })
  await page.goto('/data?view=documents')
  const form = page.getByRole('region', { name: 'Upload source', exact: true })
  await supplyActor(form)
  await form
    .getByLabel('Source name', { exact: true })
    .fill('Synthetic evidence')
  await form
    .getByLabel('Evidence type', { exact: true })
    .selectOption({ label: 'Supplier declaration' })
  await form.getByLabel('This document contains synthetic data').check()
  await form
    .getByLabel('Source document file')
    .setInputFiles({
      name: 'synthetic.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('Synthetic evidence'),
    })
  await form
    .getByRole('button', { name: 'Upload document', exact: true })
    .click()
  await expect(page).toHaveURL(new RegExp(`document=${documentId}`))
  expect(body?.encoding).toBe('base64')
  expect(body?.is_synthetic).toBe(true)
  expect(Buffer.from(String(body?.content), 'base64').toString()).toBe(
    'Synthetic evidence',
  )
  await expect(
    form.getByRole('link', { name: 'Register factor' }),
  ).toHaveAttribute('href', `/data?view=factors&evidence=${id(8400)}`)
})

test('quality filters and paging stay on server; errors cannot be waived', async ({
  page,
}) => {
  let decision: Record<string, unknown> | undefined
  await mockIntake(page, async (route, url) => {
    if (
      !url.pathname.startsWith('/api/quality/issues/') ||
      route.request().method() !== 'PATCH'
    )
      return
    decision = route.request().postDataJSON() as Record<string, unknown>
    const original = qualityIssues.find((issue) =>
      url.pathname.endsWith(issue.id),
    )!
    await route.fulfill({
      json: {
        ...original,
        status: decision.status,
        details: {
          review: {
            actor_id: decision.actor_id,
            status: decision.status,
            decision_note: decision.decision_note,
          },
          reviewed_at: time,
          data_validity_unchanged: true,
        },
      },
    })
    return true
  })
  await page.goto('/quality')
  const results = page.getByRole('region', {
    name: 'Quality issues',
    exact: true,
  })
  await expect(results.getByText('1-25 of 27')).toBeVisible()
  await results.getByRole('button', { name: 'Next page' }).click()
  await expect(results.getByText('26-27 of 27')).toBeVisible()
  await page.getByLabel('Severity', { exact: true }).selectOption('error')
  await page.getByRole('button', { name: 'Apply filters', exact: true }).click()
  await expect(results.getByText('1-1 of 1')).toBeVisible()
  await results
    .getByRole('button', { name: /^Review Missing quantity/ })
    .click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('option', { name: 'Waive' })).toBeDisabled()
  await supplyActor(dialog)
  await dialog
    .getByLabel('Decision note')
    .fill('Synthetic source corrected and reimported; review only.')
  await dialog.getByRole('button', { name: 'Record decision' }).click()
  await expect(
    dialog.getByText('Decision recorded.'),
  ).toBeVisible()
  expect(decision?.status).toBe('resolved')
  expect(decision?.actor_id).toBe(id(4))
  expect(decision?.company_id).toBe(id(1))
})

test('factor registration preserves decimal strings and reports rejected evidence', async ({
  page,
}) => {
  let submitted: Record<string, unknown> | undefined
  await mockIntake(page, async (route, url) => {
    if (
      url.pathname !== '/api/emission-factors' ||
      route.request().method() !== 'POST'
    )
      return
    submitted = route.request().postDataJSON() as Record<string, unknown>
    await route.fulfill({
      status: 422,
      json: {
        detail: {
          code: 'factor_evidence_invalid',
          message: 'Synthetic evidence trust failure.',
          trace_id: 'synthetic-factor-trace',
          retryable: false,
        },
      },
    })
    return true
  })
  await page.goto(`/data?view=factors&evidence=${id(8400)}`)
  await expect(
    page
      .getByRole('region', { name: 'Emission factor catalog' })
      .getByRole('cell').filter({ hasText: factor.factor_value }),
  ).toBeVisible()
  await page.getByText('Register a factor', { exact: true }).click()
  const form = page.getByRole('region', { name: 'Register emission factor' })
  await supplyActor(form)
  await form.getByLabel('Purchased-material metric').selectOption(id(102))
  for (const [label, value] of Object.entries({
    'Factor code': 'SYNTHETIC-NEW',
    Version: 'test-v2',
    'Factor name': 'Synthetic factor',
    'Material code': 'AL',
    Geography: 'GLOBAL',
    'Factor value (kgCO2e/kg)': '1.123456789012',
    'Source quality (0 to 1)': '0.95',
    'Factor specificity (0 to 1)': '0.8',
    'Factor recency (0 to 1)': '1',
    'Effective from': '2026-07-01',
  }))
    await form.getByLabel(label, { exact: true }).fill(value)
  await form
    .getByRole('button', { name: 'Register factor', exact: true })
    .click()
  await expect(
    form.getByText('Synthetic evidence trust failure.'),
  ).toBeVisible()
  await expect(form.getByText('Trace: synthetic-factor-trace')).toHaveCount(0)
  expect(submitted?.factor_value).toBe('1.123456789012')
  expect(submitted?.evidence_item_id).toBe(id(8400))
})

test('legacy system route redirects without diagnostics, reset or provider requests', async ({ page }) => {
  const calls = await mockIntake(page)
  await page.goto('/demo')
  await expect(page).toHaveURL(/\/dashboard$/)
  await expect(page.getByRole('heading', { name: 'Overview', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /Reset synthetic demo|Test provider connection|Sync grid history/ })).toHaveCount(0)
  expect(calls.some(call => /health|db\/demo|demo\/reset|electricity-maps|grid\//.test(call.url.pathname))).toBe(false)
})

test('upload data offers recorded sources without live grid controls', async ({ page }) => {
  const calls = await mockIntake(page)
  await page.goto('/data')
  await expect(page.getByRole('region', { name: 'New import', exact: true })).toBeVisible()
  await expect(page.getByText(/Electricity Maps/i)).toHaveCount(0)
  expect(calls.some(call => /electricity-maps|grid\/.*sync/.test(call.url.pathname))).toBe(false)
})

test('a warning can be waived only with an explicit actor and decision note', async ({
  page,
}) => {
  let decisions = 0
  await mockIntake(page, async (route, url) => {
    if (url.pathname !== `/api/quality/issues/${qualityIssues[1].id}`) return
    decisions++
    const body = route.request().postDataJSON()
    expect(route.request().method()).toBe('PATCH')
    expect(body).toEqual({
      company_id: id(1),
      actor_id: id(4),
      status: 'waived',
      decision_note: 'Synthetic evidence reviewed.',
    })
    await route.fulfill({
      json: {
        ...qualityIssues[1],
        status: 'waived',
        details: {
          review: body,
          reviewed_at: time,
          data_validity_unchanged: true,
        },
      },
    })
    return true
  })
  await page.goto('/quality?severity=warning')
  await page.getByRole('button', { name: /^Review Estimated factor/ }).click()
  const dialog = page.getByRole('dialog')
  await supplyActor(dialog)
  await dialog.getByLabel('Decision', { exact: true }).selectOption('waived')
  await dialog.getByRole('button', { name: 'Record decision' }).click()
  expect(decisions).toBe(0)
  await dialog.getByLabel('Decision note').fill('Synthetic evidence reviewed.')
  await dialog.getByRole('button', { name: 'Record decision' }).click()
  await expect(
    dialog.getByText('Decision recorded.'),
  ).toBeVisible()
  expect(decisions).toBe(1)
})

test('stale quality data stays visible but cannot be reviewed after refresh failure', async ({
  page,
}) => {
  let fail = false
  await mockIntake(page, async (route, url) => {
    if (!fail || url.pathname !== '/api/quality/issues') return
    await route.fulfill({
      status: 503,
      json: {
        detail: {
          code: 'database_unavailable',
          message: 'Synthetic refresh failure.',
          retryable: false,
        },
      },
    })
    return true
  })
  await page.goto('/quality?severity=error')
  await expect(
    page.getByRole('button', { name: /^Review Missing quantity/ }),
  ).toBeVisible()
  fail = true
  await page
    .getByRole('button', { name: 'Refresh quality issues', exact: true })
    .click()
  await expect(
    page.getByText('Refresh failed. Showing previously fetched data.'),
  ).toBeVisible()
  await page.getByRole('button', { name: /^Review Missing quantity/ }).click()
  await expect(
    page.getByRole('dialog').getByRole('button', { name: 'Record decision' }),
  ).toBeDisabled()
})

test('numeric JSON factors fail the Decimal string boundary', async ({
  page,
}) => {
  await mockIntake(page, async (route, url) => {
    if (url.pathname !== '/api/emission-factors') return
    await route.fulfill({
      json: {
        items: [{ ...factor, factor_value: 1.25 }],
        total: 1,
        offset: 0,
        limit: 25,
      },
    })
    return true
  })
  await page.goto('/data?view=factors')
  const catalog = page.getByRole('region', { name: 'Emission factor catalog' })
  await expect(
    catalog.getByText(
      'The API response does not match the supported contract.',
    ),
  ).toBeVisible()
  await expect(catalog.getByText(factor.name, { exact: true })).toHaveCount(0)
})

test('intake, quality and redirected overview fit narrow screens', async ({
  page,
}, testInfo) => {
  await mockIntake(page)
  await page.setViewportSize({ width: 320, height: 740 })
  for (const [url, title] of [
    ['/data', 'Upload data'],
    ['/quality', 'Check data'],
    ['/demo', 'Overview'],
  ]) {
    await page.goto(url)
    await expect(
      page.getByRole('heading', { name: title, exact: true }),
    ).toBeVisible()
    await expect(
      page.getByText('Synthetic data', { exact: true }).first(),
    ).toBeVisible()
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true)
    await page.screenshot({
      path: testInfo.outputPath(`${title.replaceAll(' ', '-')}-mobile.png`),
      fullPage: true,
    })
  }
})

test('demo URLs cannot expose reset controls even with saved operator state', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('demo-reset-enabled', 'true'))
  const calls = await mockIntake(page)
  await page.goto('/demo?view=reset')
  await expect(page).toHaveURL(/\/dashboard$/)
  await expect(page.getByLabel('Manual reset token')).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'Demo reset', exact: true })).toHaveCount(0)
  expect(calls.some(call => call.url.pathname === '/api/demo/reset')).toBe(false)
})

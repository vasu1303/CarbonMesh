import { expect, test, type Page } from '@playwright/test'
import { id } from './dashboard-fixtures'
import {
  dispatchLoad,
  dispatchRecommendation,
  dispatchScenario,
  mockDispatch,
} from './dispatch-fixtures'

async function fillScenario(page: Page) {
  await page
    .getByLabel('Flexible load', { exact: true })
    .selectOption(dispatchLoad.id)
  await page
    .getByLabel('Calculation method', { exact: true })
    .selectOption({ label: 'Dispatch optimization (synthetic)' })
  await page
    .getByLabel('Stored forecast', { exact: true })
    .selectOption({ label: 'Synthetic hourly forecast' })
  await page.getByLabel('Requested by', { exact: true }).selectOption({ label: 'Synthetic analyst / active' })
  await page
    .getByLabel('Earliest start (UTC)', { exact: true })
    .fill('2026-10-01T00:00')
  await page
    .getByLabel('Latest finish (UTC)', { exact: true })
    .fill('2026-10-01T12:00')
  await page
    .getByLabel('Baseline start (UTC)', { exact: true })
    .fill('2026-10-01T00:00')
  await page.getByLabel('Maximum delay (minutes)', { exact: true }).fill('240')
  await page
    .getByLabel('Available capacity (kW, optional)', { exact: true })
    .fill('600.125000')
}

test('energy planning selects stored forecasts without provider or live-sync controls', async ({ page }) => {
  const calls = await mockDispatch(page)
  await page.goto('/dispatch')
  await expect(page.getByRole('cell', { name: '500.125000', exact: true })).toBeVisible()
  await page.getByLabel('Stored forecast', { exact: true }).selectOption({ label: 'Synthetic hourly forecast' })
  await expect(page.getByRole('button', { name: /Sync.*forecast/i })).toHaveCount(0)
  await expect(page.getByText(/Electricity Maps/i)).toHaveCount(0)
  expect(calls.filter(call => call.method === 'POST' && call.url.pathname !== '/api/context/resolve')).toHaveLength(0)
  expect(calls.some(call => /forecasts\/sync|electricity-maps/.test(call.url.pathname))).toBe(false)
})

test('scenario creation keeps exact quantities and stable retry identity', async ({
  page,
}) => {
  const attempts: Record<string, unknown>[] = []
  const calls = await mockDispatch(page, async (route, url) => {
    if (url.pathname !== '/api/dispatch/scenarios') return
    attempts.push(route.request().postDataJSON())
    if (attempts.length < 3)
      await route.fulfill({
        status: 503,
        json: {
          detail: {
            code: 'temporary_failure',
            message: 'Synthetic transient failure.',
            retryable: true,
          },
        },
      })
    else await route.fulfill({ json: dispatchScenario })
    return true
  })
  await page.goto('/dispatch')
  await fillScenario(page)
  await page
    .getByRole('button', { name: 'Create scenario', exact: true })
    .click()
  await expect(
    page.getByText('Synthetic transient failure.', { exact: true }),
  ).toBeVisible()
  await page
    .getByRole('button', { name: 'Create scenario', exact: true })
    .click()
  await expect.poll(() => attempts.length).toBe(2)
  await expect(
    page.getByRole('button', { name: 'Create scenario', exact: true }),
  ).toBeEnabled()
  await page
    .getByLabel('Available capacity (kW, optional)', { exact: true })
    .fill('601.125000')
  await page
    .getByRole('button', { name: 'Create scenario', exact: true })
    .click()
  await expect(page).toHaveURL(new RegExp(`/dispatch/${dispatchScenario.id}$`))
  expect(attempts[0].idempotency_key).toBe(attempts[1].idempotency_key)
  expect(attempts[2].idempotency_key).not.toBe(attempts[1].idempotency_key)
  expect(attempts[0]).toMatchObject({
    available_capacity_kw: '600.125000',
    maximum_delay_minutes: 240,
    baseline_start: '2026-10-01T00:00:00.000Z',
    requested_by: id(4),
  })
  expect(calls.some((call) => call.url.pathname.endsWith('/optimize'))).toBe(
    false,
  )
})

test('recommendation reads preserve precision, frozen forecasts, lineage and advisory-only scope', async ({
  page,
}, testInfo) => {
  const calls = await mockDispatch(page)
  await page.goto(`/dispatch/${dispatchScenario.id}`)
  await expect(
    page.getByRole('link', { name: '300.555555555 kgCO2e', exact: true }),
  ).toHaveAttribute(
    'href',
    `/ledger?event=${dispatchRecommendation.ledger_event_id}`,
  )
  await expect(
    page.getByRole('link', { name: 'Review approval', exact: true }),
  ).toHaveAttribute(
    'href',
    `/approvals?approval=${dispatchRecommendation.approval.id}`,
  )
  await page.getByText('Exact forecast values / UTC', { exact: true }).click()
  await expect(
    page.getByRole('cell', { name: '200.123456789', exact: true }),
  ).toBeVisible()
  await expect(
    page.getByText('Advisory only. Approval cannot actuate equipment.', {
      exact: true,
    }),
  ).toBeVisible()
  await expect(
    page.getByText('Maximum delay exceeded', { exact: true }),
  ).toBeVisible()
  expect(
    calls.some(
      (call) =>
        call.url.pathname === `/api/dispatch/scenarios/${dispatchScenario.id}`,
    ),
  ).toBe(false)
  expect(
    calls.filter(
      (call) =>
        call.method === 'POST' && call.url.pathname !== '/api/context/resolve',
    ),
  ).toHaveLength(0)
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true)
  await page.screenshot({
    path: testInfo.outputPath('dispatch-recommendation.png'),
    fullPage: true,
  })
})

test('explicit optimization returns infeasible windows without relaxing constraints', async ({
  page,
}) => {
  const calls = await mockDispatch(page, async (route, url) => {
    if (url.pathname.endsWith('/recommendation')) {
      await route.fulfill({
        status: 404,
        json: {
          detail: {
            code: 'dispatch_not_found',
            message: 'No Dispatch recommendation exists for this scenario.',
            retryable: false,
          },
        },
      })
      return true
    }
    if (url.pathname.endsWith('/optimize')) {
      await route.fulfill({
        json: {
          scenario_id: dispatchScenario.id,
          terminal_state: 'no_feasible_option',
          evaluated_windows: 11,
          feasible_windows: 0,
          rejected_windows: [
            {
              start: '2026-10-01T00:00:00Z',
              end: '2026-10-01T02:00:00Z',
              reasons: ['insufficient_capacity'],
            },
          ],
          recommendation: null,
        },
      })
      return true
    }
  })
  await page.goto(`/dispatch/${dispatchScenario.id}`)
  await expect(
    page.getByText('No Dispatch recommendation exists for this scenario.', {
      exact: true,
    }),
  ).toBeVisible()
  await page
    .getByLabel(
      'Keep the recorded constraints; advisory recommendation only.',
      { exact: true },
    )
    .check()
  await page
    .getByRole('button', { name: 'Optimize scenario', exact: true })
    .click()
  await expect(
    page.getByText('No feasible operating window', { exact: true }),
  ).toBeVisible()
  await expect(
    page.getByText('Insufficient capacity', { exact: true }),
  ).toBeVisible()
  expect(
    calls.find((call) => call.url.pathname.endsWith('/optimize'))?.body,
  ).toEqual({ company_id: id(1) })
  await expect(
    page.getByRole('link', { name: 'Review approval', exact: true }),
  ).toHaveCount(0)
})

test('stale recommendations retain historical facts and disable optimization', async ({
  page,
}) => {
  await mockDispatch(page, async (route, url) => {
    if (!url.pathname.endsWith('/recommendation')) return
    await route.fulfill({
      json: {
        scenario_id: dispatchScenario.id,
        terminal_state: 'stale',
        recommendation: {
          ...dispatchRecommendation,
          invalidated_at: '2026-10-02T00:00:00Z',
          status: 'invalidated',
          approval: {
            ...dispatchRecommendation.approval,
            status: 'invalidated',
          },
        },
      },
    })
    return true
  })
  await page.goto(`/dispatch/${dispatchScenario.id}`)
  await expect(
    page.getByText(
      'Stale recommendation. Recorded windows and facts are historical; the preview is no longer current.',
      { exact: true },
    ),
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Optimize scenario', exact: true }),
  ).toBeDisabled()
  await expect(
    page.getByRole('link', { name: '300.555555555 kgCO2e', exact: true }),
  ).toBeVisible()
})

test('empty loads and unsafe response bindings fail closed', async ({
  page,
}) => {
  await mockDispatch(page, async (route, url) => {
    if (url.pathname === '/api/dispatch/loads') {
      await route.fulfill({
        json: { items: [], total: 0, limit: 25, offset: 0 },
      })
      return true
    }
    if (url.pathname.endsWith('/recommendation')) {
      await route.fulfill({
        json: {
          scenario_id: dispatchScenario.id,
          terminal_state: 'approval_required',
          recommendation: {
            ...dispatchRecommendation,
            actuation_authorized: true,
          },
        },
      })
      return true
    }
  })
  await page.goto('/dispatch')
  await expect(
    page.getByText('No flexible loads', { exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Create scenario', exact: true }),
  ).toBeDisabled()
  await page.goto(`/dispatch/${dispatchScenario.id}`)
  await expect(
    page.getByText('The API response does not match the supported contract.', {
      exact: true,
    }),
  ).toBeVisible()
  await expect(
    page.getByRole('link', { name: 'Review approval', exact: true }),
  ).toHaveCount(0)
  await page.goto('/dispatch/not-a-uuid')
  await expect(
    page.getByRole('heading', {
      name: 'Invalid scenario identifier',
      exact: true,
    }),
  ).toBeVisible()
})

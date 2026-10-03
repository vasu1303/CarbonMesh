import { expect, test, type Page } from '@playwright/test'
import { id, measurement } from './dashboard-fixtures'
import { products } from './procurement-fixtures'
import {
  approvalFixture,
  ids,
  ledgerDetail,
  mockScenariosApprovalsLedger,
  scenario,
  sha,
} from './scenarios-approvals-ledger-fixtures'

async function completeScenario(page: Page) {
  await page
    .getByLabel('Current product', { exact: true })
    .selectOption(products[0].id)
  await page
    .getByLabel('Purchased-material measurement')
    .selectOption(measurement.id)
  await page.getByLabel('Quantity', { exact: true }).fill('10000.123456789012')
  await page.getByLabel('Quantity unit', { exact: true }).fill('kg')
  await page.getByLabel('Scoring method UUID').fill(ids.method)
  const actor = page.getByLabel('Actor UUID', { exact: true })
  if (await actor.isEditable()) await actor.fill(ids.actor)
  await page.getByLabel('Maximum cost increase (%)').fill('5.00')
  await page.getByLabel('Maximum lead time (days)').fill('30')
  await page.getByLabel('Minimum circularity score').fill('50.00')
  await page
    .getByLabel('Allowed material codes (comma separated)')
    .fill('aluminium')
  await page.getByLabel('Excluded risk levels (comma separated)').fill('high')
}
async function confirmPreview(page: Page) {
  const actor = page.getByLabel('Actor UUID', { exact: true })
  if (await actor.isEditable()) await actor.fill(ids.actor)
  await page.getByLabel('Confirm preview hash', { exact: true }).fill(sha)
  await page.getByRole('checkbox', { name: /I confirm this/ }).check()
}

test('scenario commands require a submitted valid form and preserve exact decimal strings', async ({
  page,
}) => {
  const calls = await mockScenariosApprovalsLedger(page)
  await page.goto('/procurement/scenarios/new')
  await completeScenario(page)
  expect(
    calls.filter(
      (c) =>
        c.method === 'POST' &&
        c.url.pathname.startsWith('/api/procurement/scenarios'),
    ),
  ).toEqual([])
  await page.getByLabel('Maximum cost increase (%)').fill('101')
  await page.getByRole('button', { name: 'Create and assess scenario' }).click()
  await expect(page.getByText('Must be between 0 and 100.')).toBeVisible()
  await expect(
    page.getByLabel('Maximum cost increase (%)', { exact: true }),
  ).toHaveAttribute('aria-invalid', 'true')
  await expect(
    page.getByLabel('Maximum cost increase (%)', { exact: true }),
  ).toHaveAccessibleDescription('Must be between 0 and 100.')
  await page.getByLabel('Maximum cost increase (%)').fill('5.00')
  await page.getByRole('button', { name: 'Create and assess scenario' }).click()
  await expect(page).toHaveURL(
    new RegExp(`/procurement/scenarios/${ids.scenario}$`),
  )
  const create = calls.find(
    (c) => c.url.pathname === '/api/procurement/scenarios',
  )!
  expect(create.body).toMatchObject({
    company_id: id(1),
    site_id: id(2),
    reporting_period_id: id(3),
    current_product_id: products[0].id,
    carbon_measurement_id: measurement.id,
    quantity: '10000.123456789012',
    max_cost_increase_pct: '5.00',
    minimum_circularity_score: '50.00',
    requested_by: ids.actor,
    method_definition_id: ids.method,
    material_constraints: {
      allowed_material_codes: ['aluminium'],
      excluded_risk_levels: ['high'],
    },
  })
  await expect(
    page.getByText('Lead time exceeds the hard constraint.'),
  ).toBeVisible()
  await expect(
    page.getByRole('link', { name: 'Review exact approval' }),
  ).toHaveAttribute('href', `/approvals?approval=${ids.approval}`)
  expect(calls.some((c) => c.url.pathname.endsWith('/score'))).toBe(false)
  await page.getByRole('button', { name: 'Score frozen scenario' }).click()
  await expect
    .poll(() => calls.filter((c) => c.url.pathname.endsWith('/score')).length)
    .toBe(1)
  expect(calls.find((c) => c.url.pathname.endsWith('/score'))?.body).toEqual({
    company_id: id(1),
  })
})

test('scenario creation reuses an unchanged retry key and renews it after an edit', async ({
  page,
}) => {
  const calls = await mockScenariosApprovalsLedger(page, async (route, url) => {
    if (url.pathname !== '/api/procurement/scenarios') return
    await route.fulfill({
      status: 503,
      json: {
        detail: {
          code: 'provider_unavailable',
          message: 'Synthetic service unavailable.',
          retryable: false,
        },
      },
    })
    return true
  })
  await page.goto('/procurement/scenarios/new')
  await completeScenario(page)
  const submit = page.getByRole('button', {
    name: 'Create and assess scenario',
  })
  await submit.click()
  await expect(page.getByText('Synthetic service unavailable.')).toBeVisible()
  await submit.click()
  await expect(submit).toBeEnabled()
  await page.getByLabel('Quantity', { exact: true }).fill('9999.123456789012')
  await submit.click()
  await expect(submit).toBeEnabled()
  const bodies = calls
    .filter((c) => c.url.pathname === '/api/procurement/scenarios')
    .map((c) => c.body as { idempotency_key: string })
  expect(bodies).toHaveLength(3)
  expect(bodies[0].idempotency_key).toBe(bodies[1].idempotency_key)
  expect(bodies[2].idempotency_key).not.toBe(bodies[1].idempotency_key)
})

test('no feasible scenario has no invented recommendation or history request', async ({
  page,
}) => {
  const calls = await mockScenariosApprovalsLedger(page, async (route, url) => {
    if (url.pathname !== `/api/procurement/scenarios/${ids.scenario}`) return
    await route.fulfill({
      json: {
        ...scenario,
        terminal_state: 'no_feasible_option',
        selected_recommendation: null,
      },
    })
    return true
  })
  await page.goto(`/procurement/scenarios/${ids.scenario}`)
  await expect(
    page.getByText('No feasible option', { exact: true }),
  ).toBeVisible()
  await expect(
    page.getByText('No recommendation', { exact: true }),
  ).toBeVisible()
  expect(calls.some((c) => c.url.pathname.endsWith('/recommendation'))).toBe(
    false,
  )
})

for (const [kind, text] of [
  ['procurement_recommendation', 'Frozen supplier score'],
  ['disclosure_draft', 'Atomic claims'],
  ['dispatch_recommendation', 'Frozen forecast points'],
] as const) {
  test(`exact ${kind} approval renders domain facts and binds the human decision`, async ({
    page,
  }, testInfo) => {
    const preview = approvalFixture(kind)
    const calls = await mockScenariosApprovalsLedger(
      page,
      async (route, url) => {
        if (url.pathname !== `/api/approvals/${ids.approval}`) return
        await route.fulfill({ json: preview })
        return true
      },
    )
    await page.goto(`/approvals?status=all&approval=${ids.approval}`)
    await expect(
      page.getByRole('heading', { name: text, exact: true }),
    ).toBeVisible()
    await expect(
      page.getByRole('button', { name: 'Confirm approval' }),
    ).toBeDisabled()
    await confirmPreview(page)
    expect(calls.some((c) => c.url.pathname.endsWith('/decision'))).toBe(false)
    await page.screenshot({
      path: testInfo.outputPath(`${kind}-preview.png`),
      fullPage: true,
    })
    // Capture the command against this domain's exact response identity.
    await page.route(
      `**/api/approvals/${ids.approval}/decision`,
      async (route) => {
        const body = route.request().postDataJSON()
        expect(body).toMatchObject({
          company_id: id(1),
          decision: 'approve',
          actor_id: ids.actor,
          preview_hash: sha,
          idempotency_key: preview.idempotency_key,
        })
        await route.fulfill({
          json: {
            approval_id: preview.id,
            target_type: kind,
            target_id: preview.target_id,
            status: 'approved',
            preview_hash: sha,
            analysis_signature: sha,
            decided_by: ids.actor,
            decided_at: '2026-09-30T12:00:00Z',
            decision_note: null,
            ledger_event_id: id(6024),
            idempotent_replay: false,
          },
        })
      },
    )
    await page.getByRole('button', { name: 'Confirm approval' }).click()
    await expect(page.getByText('Decision recorded: approved.')).toBeVisible()
    await page.getByRole('button', { name: 'Close approval' }).click()
    await expect(page).toHaveURL(/status=all$/)
  })
}

test('a changed preview at submit time fails closed without a decision call', async ({
  page,
}) => {
  let upstreamChanged = false
  const calls = await mockScenariosApprovalsLedger(page, async (route, url) => {
    if (url.pathname !== `/api/approvals/${ids.approval}`) return
    await route.fulfill({
      json: { ...approvalFixture(), preview_current: !upstreamChanged },
    })
    return true
  })
  await page.goto(`/approvals?approval=${ids.approval}`)
  await confirmPreview(page)
  upstreamChanged = true
  await page.getByRole('button', { name: 'Confirm approval' }).click()
  await expect(
    page.getByText('Decision unavailable: stale preview.'),
  ).toBeVisible()
  expect(
    calls.filter((c) => c.url.pathname.endsWith('/decision')),
  ).toHaveLength(0)
})

for (const kind of [
  'procurement_recommendation',
  'disclosure_draft',
  'dispatch_recommendation',
]) {
  test(`malformed ${kind} preview blocks approval and rejection`, async ({
    page,
  }) => {
    const calls = await mockScenariosApprovalsLedger(
      page,
      async (route, url) => {
        if (url.pathname !== `/api/approvals/${ids.approval}`) return
        await route.fulfill({
          json: { ...approvalFixture(kind), preview_payload: {} },
        })
        return true
      },
    )
    await page.goto(`/approvals?approval=${ids.approval}`)
    await expect(
      page.getByText(
        'Unsupported or incomplete preview. Decisions are blocked.',
      ),
    ).toBeVisible()
    await expect(
      page.getByRole('button', { name: 'Confirm approval' }),
    ).toBeDisabled()
    await expect(page.getByLabel('Decision', { exact: true })).toBeDisabled()
    expect(
      calls.filter((c) => c.url.pathname.endsWith('/decision')),
    ).toHaveLength(0)
  })

  test(`malformed ${kind} preview after confirmation stops the decision command`, async ({
    page,
  }) => {
    let upstreamChanged = false
    const calls = await mockScenariosApprovalsLedger(
      page,
      async (route, url) => {
        if (url.pathname !== `/api/approvals/${ids.approval}`) return
        const preview = approvalFixture(kind)
        await route.fulfill({
          json: upstreamChanged ? { ...preview, preview_payload: {} } : preview,
        })
        return true
      },
    )
    await page.goto(`/approvals?approval=${ids.approval}`)
    await confirmPreview(page)
    upstreamChanged = true
    await page.getByRole('button', { name: 'Confirm approval' }).click()
    await expect(
      page.getByText(
        'The exact preview could not be revalidated. Review the refreshed record before deciding.',
      ),
    ).toBeVisible()
    await expect(
      page.getByRole('button', { name: 'Confirm approval' }),
    ).toBeDisabled()
    expect(
      calls.filter((c) => c.url.pathname.endsWith('/decision')),
    ).toHaveLength(0)
  })
}

test('a failed rejection retries with the preview key and requires renewed confirmation', async ({
  page,
}) => {
  let attempts = 0
  const calls = await mockScenariosApprovalsLedger(page, async (route, url) => {
    if (!url.pathname.endsWith('/decision')) return
    attempts += 1
    if (attempts !== 1) return
    await route.fulfill({
      status: 503,
      json: {
        detail: {
          code: 'temporarily_unavailable',
          message: 'Decision temporarily unavailable.',
          retryable: false,
        },
      },
    })
    return true
  })
  await page.goto(`/approvals?approval=${ids.approval}`)
  await page.getByLabel('Decision', { exact: true }).selectOption('reject')
  await page
    .getByLabel('Decision note (optional)')
    .fill('Synthetic reviewer rejects the trade-off.')
  await confirmPreview(page)
  await page.getByRole('button', { name: 'Confirm rejection' }).click()
  await expect(
    page.getByText('Decision temporarily unavailable.'),
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Confirm rejection' }),
  ).toBeDisabled()
  await page.getByRole('checkbox', { name: /I confirm this/ }).check()
  await page.getByRole('button', { name: 'Confirm rejection' }).click()
  await expect(page.getByText('Decision recorded: rejected.')).toBeVisible()
  const decisions = calls
    .filter((c) => c.url.pathname.endsWith('/decision'))
    .map((c) => c.body)
  expect(decisions).toHaveLength(2)
  expect(decisions[0]).toEqual(decisions[1])
})

test('expired and unsupported previews cannot be decided', async ({ page }) => {
  let unsupported = false
  const calls = await mockScenariosApprovalsLedger(page, async (route, url) => {
    if (url.pathname !== `/api/approvals/${ids.approval}`) return
    await route.fulfill({
      json: {
        ...approvalFixture(),
        ...(unsupported
          ? { target_type: 'unknown_artifact' }
          : { expired: true, expires_at: '2020-01-01T00:00:00Z' }),
      },
    })
    return true
  })
  await page.goto(`/approvals?approval=${ids.approval}`)
  await expect(
    page.getByText('Decision unavailable: expired preview.'),
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Confirm approval' }),
  ).toBeDisabled()
  unsupported = true
  await page
    .getByRole('button', { name: 'Refresh approval', exact: true })
    .click()
  await expect(
    page.getByText('Unsupported or incomplete preview. Decisions are blocked.'),
  ).toBeVisible()
  expect(calls.some((c) => c.url.pathname.endsWith('/decision'))).toBe(false)
})

test('ledger filters and pagination survive event links and bounded recursive audit', async ({
  page,
}, testInfo) => {
  const calls = await mockScenariosApprovalsLedger(page)
  await page.goto('/ledger?event_type=measurement.verified')
  const list = page.getByRole('region', { name: 'Ledger events', exact: true })
  await list.getByRole('button', { name: 'Next page' }).click()
  await expect(page).toHaveURL(/offset=10/)
  await page.getByLabel('Event UUID', { exact: true }).fill(ids.event)
  await page.getByRole('button', { name: 'Open event', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`event=${ids.event}`))
  await expect(page).toHaveURL(/offset=10/)
  await expect(
    page.getByText('Synthetic evidence', { exact: true }),
  ).toBeVisible()
  await page.getByRole('button', { name: 'Trace entity audit' }).click()
  await expect(
    page.getByText('Partial trace: the server traversal limit was reached.'),
  ).toBeVisible()
  await page
    .getByRole('button', { name: `Open linked event ${ids.parent}` })
    .click()
  await expect(page).toHaveURL(new RegExp(`event=${ids.parent}`))
  await expect(page).toHaveURL(/event_type=measurement.verified/)
  await expect(page).toHaveURL(/offset=10/)
  await page.screenshot({
    path: testInfo.outputPath('ledger-audit.png'),
    fullPage: true,
  })
  await page.getByRole('button', { name: 'Close event' }).click()
  await expect(page).not.toHaveURL(/event=000/)
  expect(
    calls.some(
      (c) => c.url.pathname === `/api/audit/measurement/${measurement.id}`,
    ),
  ).toBe(true)
  expect(
    calls
      .filter(
        (c) =>
          c.url.pathname.startsWith('/api/ledger') ||
          c.url.pathname.startsWith('/api/audit'),
      )
      .every(
        (c) =>
          c.method === 'GET' && c.url.searchParams.get('company_id') === id(1),
      ),
  ).toBe(true)
})

test('ledger rejects invalid filters and foreign-company event payloads', async ({
  page,
}) => {
  const calls = await mockScenariosApprovalsLedger(page, async (route, url) => {
    if (url.pathname !== `/api/ledger/events/${ids.event}`) return
    await route.fulfill({ json: { ...ledgerDetail, company_id: id(9999) } })
    return true
  })
  await page.goto('/ledger?entity_id=bad-uuid')
  await expect(
    page.getByRole('heading', { name: 'Invalid ledger filters' }),
  ).toBeVisible()
  expect(calls.some((c) => c.url.pathname.startsWith('/api/ledger'))).toBe(
    false,
  )
  await page.goto(`/ledger?event=${ids.event}`)
  await expect(
    page
      .getByRole('region', { name: 'Ledger event detail' })
      .getByText('The API response does not match the supported contract.'),
  ).toBeVisible()
})

test('independent approval detail renders while its queue is unavailable and mobile content fits', async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await mockScenariosApprovalsLedger(page, async (route, url) => {
    if (url.pathname !== '/api/approvals') return
    await route.fulfill({
      status: 503,
      json: {
        detail: {
          code: 'provider_unavailable',
          message: 'Queue unavailable.',
          retryable: false,
        },
      },
    })
    return true
  })
  await page.goto(`/approvals?approval=${ids.approval}`)
  await expect(page.getByText('Queue unavailable.')).toBeVisible()
  await expect(
    page.getByRole('heading', { name: 'Frozen supplier score' }),
  ).toBeVisible()
  await page.screenshot({
    path: testInfo.outputPath('approval-mobile.png'),
    fullPage: true,
  })
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true)
})

test('ledger date and entity filters submit bounded UTC queries', async ({
  page,
}) => {
  const calls = await mockScenariosApprovalsLedger(page)
  await page.goto('/ledger')
  await page.getByLabel('Entity type', { exact: true }).fill('measurement')
  await page.getByLabel('Entity UUID', { exact: true }).fill(measurement.id)
  await page.getByLabel('Created from (local time)').fill('2026-09-01T00:00')
  await page.getByLabel('Created to (local time)').fill('2026-09-30T23:59:59')
  await page.getByRole('button', { name: 'Apply filters' }).click()
  await expect
    .poll(() =>
      calls.some(
        (c) =>
          c.url.pathname === '/api/ledger/events' &&
          c.url.searchParams.get('entity_id') === measurement.id,
      ),
    )
    .toBe(true)
  const query = calls
    .filter((c) => c.url.pathname === '/api/ledger/events')
    .at(-1)!.url.searchParams
  expect(query.get('created_from')).toMatch(/Z$/)
  expect(query.get('created_to')).toMatch(/Z$/)
  expect(query.get('limit')).toBe('10')
  expect(query.get('offset')).toBe('0')
})

test('numeric JSON decimals fail the scenario boundary without a fallback', async ({
  page,
}) => {
  await mockScenariosApprovalsLedger(page, async (route, url) => {
    if (url.pathname !== `/api/procurement/scenarios/${ids.scenario}`) return
    await route.fulfill({ json: { ...scenario, quantity: 10000 } })
    return true
  })
  await page.goto(`/procurement/scenarios/${ids.scenario}`)
  await expect(
    page
      .getByRole('region', { name: 'Scenario requirements' })
      .getByText('The API response does not match the supported contract.'),
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Score frozen scenario' }),
  ).toHaveCount(0)
})

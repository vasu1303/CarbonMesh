import { expect, test } from '@playwright/test'
import { id } from './dashboard-fixtures'
import {
  assuranceDraft,
  assuranceMeasurement,
  assuranceStandards,
  assuranceValidation,
  mockAssurance,
} from './assurance-fixtures'

test('assurance lookup and atomic claims are read-only until an explicit command', async ({
  page,
}, testInfo) => {
  const calls = await mockAssurance(page)
  await page.goto('/assurance')
  await expect(
    page.getByRole('heading', { name: 'Assurance', exact: true }),
  ).toBeVisible()
  await expect(
    page.getByText('Synthetic data / API', { exact: true }),
  ).toBeVisible()
  await page.getByLabel('Draft UUID', { exact: true }).fill(assuranceDraft.id)
  await page.getByRole('button', { name: 'Open draft', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/assurance/${assuranceDraft.id}$`))
  await expect(
    page.getByText('Recorded emissions: 123.456789012 kgCO2e.', {
      exact: true,
    }),
  ).toBeVisible()
  await expect(
    page.getByText('A comparable prior-period fact is unavailable.', {
      exact: true,
    }),
  ).toBeVisible()
  await expect(
    page.getByText('assurance-synthetic-evidence.txt', { exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole('link', { name: 'Source measurement', exact: true }),
  ).toHaveAttribute('href', `/measurement/${assuranceMeasurement.id}`)
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
    path: testInfo.outputPath('assurance-claims.png'),
    fullPage: true,
  })
})

test('draft creation preserves unchanged retry keys and renews keys for changed payloads', async ({
  page,
}) => {
  const attempts: Record<string, unknown>[] = []
  await mockAssurance(page, async (route, url) => {
    if (url.pathname !== '/api/assurance/drafts') return
    attempts.push(route.request().postDataJSON())
    if (attempts.length < 3)
      await route.fulfill({
        status: 503,
        json: {
          detail: {
            code: 'service_unavailable',
            message: 'Temporary draft failure.',
            retryable: true,
          },
        },
      })
    else await route.fulfill({ json: assuranceDraft })
    return true
  })
  await page.goto('/assurance')
  await page
    .getByLabel('Standard', { exact: true })
    .selectOption(assuranceStandards.items[0].id)
  await page
    .getByLabel('Verified measurement', { exact: true })
    .selectOption(assuranceMeasurement.id)
  const actor = page.getByLabel('Requesting actor UUID', { exact: true })
  if (await actor.isEditable()) await actor.fill(id(4))
  await page.getByRole('button', { name: 'Create draft', exact: true }).click()
  await expect(
    page.getByText('Temporary draft failure.', { exact: true }),
  ).toBeVisible()
  expect(attempts).toHaveLength(1)
  await page.getByRole('button', { name: 'Create draft', exact: true }).click()
  await expect.poll(() => attempts.length).toBe(2)
  await expect(
    page.getByRole('button', { name: 'Create draft', exact: true }),
  ).toBeEnabled()
  await page
    .getByLabel('Draft title (optional)', { exact: true })
    .fill('Revised synthetic disclosure')
  await page.getByRole('button', { name: 'Create draft', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/assurance/${assuranceDraft.id}$`))
  expect(attempts[0].idempotency_key).toBe(attempts[1].idempotency_key)
  expect(attempts[2].idempotency_key).not.toBe(attempts[1].idempotency_key)
  expect(attempts[0]).toMatchObject({
    company_id: id(1),
    site_id: id(2),
    reporting_period_id: id(3),
    requested_by: id(4),
    measurement_id: assuranceMeasurement.id,
    requirement_ids: [],
  })
})

test('validation binds the current context and evidence export is explicitly requested', async ({
  page,
}) => {
  const calls = await mockAssurance(page)
  await page.goto(`/assurance/${assuranceDraft.id}`)
  await expect(
    page.getByRole('heading', { name: assuranceDraft.title, exact: true }),
  ).toBeVisible()
  const actor = page.getByLabel('Validating actor UUID', { exact: true })
  if (await actor.isEditable()) await actor.fill(id(4))
  await page
    .getByRole('button', { name: 'Validate draft', exact: true })
    .click()
  await expect(page.getByText('Unsupported: 1', { exact: true })).toBeVisible()
  const validation = calls.find((call) =>
    call.url.pathname.endsWith('/validate'),
  )
  expect(validation?.body).toMatchObject({
    company_id: id(1),
    requested_by: id(4),
    expected_context_hash: assuranceDraft.context_hash,
  })
  expect(
    calls.some((call) => call.url.pathname.endsWith('/evidence-pack')),
  ).toBe(false)
  const download = page.waitForEvent('download')
  await page
    .getByRole('button', { name: 'Export evidence pack', exact: true })
    .click()
  expect((await download).suggestedFilename()).toBe(
    `assurance-${assuranceDraft.id}-evidence-pack.json`,
  )
})

test('stale validation blocks export and preserves recorded facts', async ({
  page,
}) => {
  await mockAssurance(page, async (route, url) => {
    if (!url.pathname.endsWith('/validate')) return
    await route.fulfill({
      json: {
        ...assuranceValidation,
        terminal_state: 'stale',
        draft: {
          ...assuranceDraft,
          status: 'invalidated',
          invalidated_at: '2026-10-01T00:00:00Z',
          validation_summary: { terminal_state: 'stale' },
        },
      },
    })
    return true
  })
  await page.goto(`/assurance/${assuranceDraft.id}`)
  const actor = page.getByLabel('Validating actor UUID', { exact: true })
  if (await actor.isEditable()) await actor.fill(id(4))
  await page
    .getByRole('button', { name: 'Validate draft', exact: true })
    .click()
  await expect(
    page.getByText(
      'Stale draft. Its recorded claims and preview are no longer current.',
      { exact: true },
    ),
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Export evidence pack', exact: true }),
  ).toBeDisabled()
  await expect(
    page.getByText('Recorded emissions: 123.456789012 kgCO2e.', {
      exact: true,
    }),
  ).toBeVisible()
})

test('standards can fail independently and cross-company drafts fail closed', async ({
  page,
}) => {
  await mockAssurance(page, async (route, url) => {
    if (url.pathname === '/api/assurance/standards') {
      await route.fulfill({
        status: 503,
        json: {
          detail: {
            code: 'standards_unavailable',
            message: 'Standards unavailable.',
            retryable: false,
          },
        },
      })
      return true
    }
    if (url.pathname === `/api/assurance/drafts/${assuranceDraft.id}`) {
      await route.fulfill({ json: { ...assuranceDraft, company_id: id(9999) } })
      return true
    }
  })
  await page.goto('/assurance')
  await expect(
    page.getByText('Standards unavailable.', { exact: true }),
  ).toBeVisible()
  await expect(
    page.getByText('Synthetic data / API', { exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Create draft', exact: true }),
  ).toBeDisabled()
  await page.goto(`/assurance/${assuranceDraft.id}`)
  await expect(
    page.getByText('The API response does not match the supported contract.', {
      exact: true,
    }),
  ).toBeVisible()
  await expect(
    page.getByRole('heading', { name: assuranceDraft.title, exact: true }),
  ).toHaveCount(0)
  await page.goto('/assurance/not-a-uuid')
  await expect(
    page.getByRole('heading', {
      name: 'Invalid draft identifier',
      exact: true,
    }),
  ).toBeVisible()
})

test('optional requirements can be excluded while required claims stay bound to an approval', async ({
  page,
}) => {
  const approval = {
    id: id(6350),
    target_type: 'disclosure_draft',
    target_id: assuranceDraft.id,
    status: 'pending',
    preview_hash: assuranceDraft.payload_hash,
    analysis_signature: assuranceDraft.context_hash,
    context_hash: assuranceDraft.context_hash,
    idempotency_key: 'synthetic-approval-key',
    expires_at: '2099-01-01T00:00:00Z',
    created_at: assuranceDraft.created_at,
  }
  const supported = {
    ...assuranceDraft,
    claims: [assuranceDraft.claims[0]],
    gaps: [],
    status: 'pending_approval',
    approval,
    validation_summary: {
      terminal_state: 'approval_required',
      state: 'validated',
    },
  }
  const calls = await mockAssurance(page, async (route, url) => {
    if (
      url.pathname === '/api/assurance/drafts' ||
      url.pathname === `/api/assurance/drafts/${assuranceDraft.id}`
    ) {
      await route.fulfill({ json: supported })
      return true
    }
  })
  await page.goto('/assurance')
  await page
    .getByLabel('Standard', { exact: true })
    .selectOption(assuranceStandards.items[0].id)
  await page
    .getByLabel('Verified measurement', { exact: true })
    .selectOption(assuranceMeasurement.id)
  await expect(
    page.getByRole('checkbox', {
      name: 'S2-TOTAL: Reported emissions (required)',
      exact: true,
    }),
  ).toBeDisabled()
  await page
    .getByRole('checkbox', {
      name: 'S2-REDUCTION: Prior-period reduction (optional)',
      exact: true,
    })
    .uncheck()
  const actor = page.getByLabel('Requesting actor UUID', { exact: true })
  if (await actor.isEditable()) await actor.fill(id(4))
  await page.getByRole('button', { name: 'Create draft', exact: true }).click()
  await expect(
    page.getByRole('link', { name: 'Review approval', exact: true }),
  ).toHaveAttribute('href', `/approvals?approval=${approval.id}`)
  expect(
    calls.find((call) => call.url.pathname === '/api/assurance/drafts')?.body,
  ).toMatchObject({ requirement_ids: [id(6102)] })
})

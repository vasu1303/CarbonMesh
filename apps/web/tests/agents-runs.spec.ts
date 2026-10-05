import { expect, test } from '@playwright/test'
import { id } from './dashboard-fixtures'
import {
  actorId,
  approvalFixture,
  interruptedRun,
  mockAgents,
  recordedAt,
  runFixture,
  runId,
  streamFixture,
} from './agents-runs-fixtures'

test('run landing requires a named selection before reading its saved result', async ({
  page,
}) => {
  const calls = await mockAgents(page)
  await page.goto('/runs')
  await expect(
    page.getByRole('heading', { name: 'Activity', exact: true }),
  ).toBeVisible()
  await page.getByRole('button', { name: 'Open run', exact: true }).click()
  await expect(page.getByLabel('Saved run', { exact: true })).toHaveValue('')
  expect(calls.some((call) => call.path.startsWith('/api/runs'))).toBe(false)
  await page.getByLabel('Saved run', { exact: true }).selectOption({ label: 'Synthetic measurement run' })
  await page.getByRole('button', { name: 'Open run', exact: true }).click()
  await expect(page).toHaveURL(`/runs/${runId}`)
  await expect(
    page.getByRole('region', { name: 'Verified facts' }),
  ).toContainText('123.000000000000001 kgCO2e')
  expect(calls.some((call) => call.path === '/api/runs')).toBe(false)
})

test('prepared procurement uses a named requester and exact decimal constraints only on submit', async ({
  page,
}) => {
  const calls = await mockAgents(page)
  await page.goto('/ask')
  await expect(
    page.getByText('Synthetic data', { exact: true }).first(),
  ).toBeVisible()
  await page
    .getByLabel('What would you like to find out?', { exact: true })
    .fill('Compare the scoped recycled aluminium products.')
  await page.getByLabel('Workflow').selectOption('procurement')
  await page.getByLabel('Requester', { exact: true }).selectOption({ label: 'Synthetic analyst / active' })
  await page.getByLabel('Materials (required)', { exact: true }).selectOption('AL')
  await page.getByLabel('Procurement scenario (required)', { exact: true }).selectOption({ label: 'Synthetic aluminium comparison' })
  await page.getByText('Commercial constraints', { exact: true }).click()
  await page.getByLabel('Maximum cost increase (%)').fill('2.50000')
  await page.getByLabel('Maximum lead time (days)').fill('14')
  await page.getByLabel('Minimum circularity score').fill('75.0000')
  expect(
    calls.filter((call) => call.path === '/api/agent/requests'),
  ).toHaveLength(0)
  await page.getByRole('button', { name: 'Start run', exact: true }).click()
  await expect(page).toHaveURL(`/runs/${runId}`)
  const commands = calls.filter((call) => call.path === '/api/agent/requests')
  expect(commands).toHaveLength(1)
  expect(commands[0].body).toMatchObject({
    context: {
      actor_id: actorId,
      company_id: id(1),
      site_id: id(2),
      reporting_period_id: id(3),
      grid_source_mode: 'live',
      procurement_scenario_id: id(6001),
      material_scope: ['AL'],
      constraints: {
        max_cost_increase_pct: '2.50000',
        max_lead_time_days: 14,
        minimum_circularity_score: '75.0000',
      },
    },
  })
  expect(
    calls.some((call) => /reset|seed|decision|grid\/sync/.test(call.path)),
  ).toBe(false)
})

test('run facts, judgments, recorded trace and documented proxy remain separate', async ({
  page,
}, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await mockAgents(page)
  await page.goto(`/runs/${runId}`)
  const facts = page.getByRole('region', {
    name: 'Verified facts',
    exact: true,
  })
  await expect(facts).toContainText('123.000000000000001 kgCO2e')
  await expect(
    facts.getByRole('link', { name: 'Ledger lineage' }),
  ).toHaveAttribute('href', `/ledger?event=${id(501)}`)
  await expect(
    page.getByRole('region', { name: 'Judgments', exact: true }),
  ).toContainText('Structured model plan')
  await page.getByText('Technical telemetry and footprint estimates', { exact: true }).click()
  await expect(
    page.getByRole('region', { name: 'Run timeline', exact: true }),
  ).toContainText('Run completed')
  await expect(
    page.getByRole('region', { name: 'Run telemetry', exact: true }),
  ).toContainText('synthetic-test-model')
  await expect(
    page.getByRole('region', { name: 'Company sustainability telemetry' }),
  ).toContainText('not measured realized savings')
  await expect(
    page.getByRole('link', { name: 'Purchased-material emissions (synthetic)', exact: true }),
  ).toHaveAttribute('href', `/measurement/${id(201)}`)
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true)
  await page.screenshot({
    path: testInfo.outputPath('agent-run-trace.png'),
    fullPage: true,
  })
  expect(errors).toEqual([])
})

test('native event replay uses configured credentials and closes after a terminal run', async ({
  page,
}) => {
  await page.addInitScript(() => {
    const native = window.EventSource
    const tracking = { credentials: [] as boolean[], opened: 0, closed: 0 }
    Object.assign(window, { agentStreamTracking: tracking })
    window.EventSource = class extends native {
      constructor(url: string | URL, options?: EventSourceInit) {
        super(url, options)
        tracking.opened++
        tracking.credentials.push(options?.withCredentials ?? false)
      }
      close() {
        tracking.closed++
        super.close()
      }
    }
  })
  await mockAgents(page)
  await page.goto(`/runs/${runId}`)
  await expect(
    page.getByRole('region', { name: 'Run timeline', exact: true }),
  ).toContainText('Run completed')
  const tracking = () =>
    page.evaluate(
      () =>
        (
          window as unknown as {
            agentStreamTracking: {
              credentials: boolean[]
              opened: number
              closed: number
            }
          }
        ).agentStreamTracking,
    )
  await expect.poll(async () => (await tracking()).closed).toBeGreaterThan(0)
  expect(
    (await tracking()).credentials.every(
      (value) => value === false,
    ),
  ).toBe(true)
})

test('clarification preserves retry keys, renews changed payload and never replans a frozen plan', async ({
  page,
}) => {
  const run = interruptedRun('clarification')
  // A missing UUID is represented by null at the boundary, never an empty string.
  const response = {
    ...run,
    context: { ...run.context, carbon_measurement_id: null },
  }
  const resumes: Record<string, unknown>[] = []
  await mockAgents(page, async (route, url) => {
    if (url.pathname === `/api/runs/${runId}`) {
      await route.fulfill({ json: response })
      return true
    }
    if (url.pathname === `/api/runs/${runId}/events`) {
      await route.fulfill({
        contentType: 'text/event-stream',
        body: streamFixture('clarification.required'),
      })
      return true
    }
    if (url.pathname === `/api/runs/${runId}/resume`) {
      resumes.push(route.request().postDataJSON())
      await route.fulfill({
        status: 503,
        json: {
          detail: {
            code: 'agent_resume_unavailable',
            message: 'Resume temporarily unavailable.',
            retryable: true,
          },
        },
      })
      return true
    }
  })
  await page.goto(`/runs/${runId}`)
  const form = page.getByRole('form', { name: 'Clarification form' })
  await expect(form).toBeVisible()
  await expect(form.getByLabel('Clarified request')).toHaveCount(0)
  await form.getByLabel('Verified measurement', { exact: true }).selectOption({ label: 'Purchased-material emissions (synthetic)' })
  const submit = form.getByRole('button', { name: 'Resume with clarification' })
  await submit.click()
  await expect(form.getByRole('alert')).toContainText(
    'Resume temporarily unavailable.',
  )
  await submit.click()
  await expect.poll(() => resumes.length).toBe(2)
  await expect(submit).toBeEnabled()
  expect(resumes[0].idempotency_key).toBe(resumes[1].idempotency_key)
  expect(resumes[0]).toMatchObject({
    actor_id: actorId,
    interrupt_sequence: 4,
    clarification: { context: { carbon_measurement_id: id(201) } },
  })
  expect(resumes[0].clarification as object).not.toHaveProperty(
    'clarified_query',
  )
  await form.getByLabel('Verified measurement', { exact: true }).selectOption({ label: 'Earlier material emissions (synthetic)' })
  await submit.click()
  await expect.poll(() => resumes.length).toBe(3)
  expect(resumes[2].idempotency_key).not.toBe(resumes[1].idempotency_key)
})

test('approval observer only resumes after an exact recorded human decision', async ({
  page,
}) => {
  const run = interruptedRun('approval')
  const approval = { ...approvalFixture }
  const calls = await mockAgents(page, async (route, url) => {
    if (url.pathname === `/api/runs/${runId}`) {
      await route.fulfill({ json: run })
      return true
    }
    if (url.pathname === `/api/runs/${runId}/events`) {
      await route.fulfill({
        contentType: 'text/event-stream',
        body: streamFixture('approval.required'),
      })
      return true
    }
    if (url.pathname === `/api/approvals/${id(301)}`) {
      await route.fulfill({ json: approval })
      return true
    }
    if (url.pathname === `/api/runs/${runId}/resume`) {
      await route.fulfill({
        json: {
          run_id: runId,
          trace_id: 'synthetic-agent-trace',
          terminal_state: 'success',
          resumed: true,
          idempotent_replay: false,
        },
      })
      return true
    }
  })
  await page.goto(`/runs/${runId}`)
  const panel = page.getByRole('region', { name: 'Approval observation' })
  const submit = panel.getByRole('button', {
    name: 'Resume after recorded decision',
  })
  await expect(submit).toBeDisabled()
  expect(calls.filter((call) => call.path.endsWith('/resume'))).toHaveLength(0)
  approval.status = 'approved'
  approval.decided_by = id(9)
  approval.decided_at = recordedAt
  approval.ledger_event_id = id(502)
  await panel.getByRole('button', { name: 'Refresh approval decision' }).click()
  await expect(submit).toBeEnabled()
  await submit.click()
  await expect
    .poll(() => calls.filter((call) => call.path.endsWith('/resume')).length)
    .toBe(1)
  expect(
    calls.find((call) => call.path.endsWith('/resume'))?.body,
  ).toMatchObject({
    approval: { approval_id: id(301), preview_hash: approval.preview_hash },
  })
  expect(calls.some((call) => call.path.endsWith('/decision'))).toBe(false)
})

test('failed event stream falls back to reads and stops on terminal result', async ({
  page,
}) => {
  let reads = 0
  let streamFailures = 0
  let fallbackReads = 0
  let completed = false
  await mockAgents(page, async (route, url) => {
    if (url.pathname === `/api/runs/${runId}`) {
      reads++
      if (streamFailures > 0) fallbackReads++
      await route.fulfill({
        json: {
          ...runFixture,
          terminal_state: completed ? 'success' : 'running',
          completed_at: completed ? recordedAt : null,
        },
      })
      return true
    }
    if (url.pathname.endsWith('/events') && url.pathname.includes('/runs/')) {
      streamFailures++
      await route.abort('failed')
      return true
    }
  })
  await page.goto(`/runs/${runId}`)
  await expect(page.getByRole('region', { name: 'Run outcome' })).toContainText(
    'Running',
  )
  await expect(
    page.getByText('Event stream unavailable; checking persisted status'),
  ).toBeVisible()
  await expect.poll(() => fallbackReads).toBeGreaterThan(0)
  // Mount/refetch reads must not complete the fixture before fallback is observed.
  completed = true
  await expect(page.getByRole('region', { name: 'Run outcome' })).toContainText(
    'Success',
  )
  await expect(
    page.getByText('Updates stopped at the recorded state'),
  ).toBeVisible()
  const settled = reads
  const settledStreamFailures = streamFailures
  await page.waitForTimeout(2200)
  expect(reads).toBe(settled)
  expect(streamFailures).toBe(settledStreamFailures)
})

for (const state of [
  'unsupported',
  'provider_unavailable',
  'no_data',
  'no_feasible_option',
  'stale',
]) {
  test(`run exposes ${state} without executing a command`, async ({ page }) => {
    const calls = await mockAgents(page, async (route, url) => {
      if (url.pathname === `/api/runs/${runId}`) {
        await route.fulfill({
          json: {
            ...runFixture,
            terminal_state: state,
            facts: [],
            message: `Synthetic terminal state: ${state}.`,
          },
        })
        return true
      }
    })
    await page.goto(`/runs/${runId}`)
    await expect(
      page.getByRole('region', { name: 'Run outcome' }),
    ).toContainText(new RegExp(state.replaceAll('_', ' '), 'i'))
    await expect(
      page.getByRole('region', { name: 'Verified facts' }),
    ).toContainText('No verified facts recorded')
    expect(
      calls.filter(
        (call) =>
          call.method === 'POST' && call.path !== '/api/context/resolve',
      ),
    ).toHaveLength(0)
  })
}

test('sustainability failure does not hide verified run facts', async ({
  page,
}) => {
  await mockAgents(page, async (route, url) => {
    if (url.pathname === '/api/metrics/agent-sustainability') {
      await route.fulfill({
        status: 409,
        json: {
          detail: {
            code: 'agent_metrics_fact_invalid',
            message:
              'Projected benefits no longer match approved ledger facts.',
            retryable: false,
          },
        },
      })
      return true
    }
  })
  await page.goto(`/runs/${runId}`)
  await page.getByText('Technical telemetry and footprint estimates', { exact: true }).click()
  await expect(
    page.getByRole('region', { name: 'Company sustainability telemetry' }),
  ).toContainText('Projected benefits no longer match approved ledger facts.')
  await expect(
    page.getByRole('region', { name: 'Verified facts' }),
  ).toContainText('123.000000000000001 kgCO2e')
})

import { expect, test, type Page } from '@playwright/test'
import { id, mockDashboard } from './dashboard-fixtures'
import { mockAgents, runId } from './agents-runs-fixtures'
import { mockIntake } from './intake-quality-fixtures'
import { mockAssurance, assuranceDraft } from './assurance-fixtures'
import { mockDispatch, dispatchScenario } from './dispatch-fixtures'
import { mockCatalog } from './procurement-fixtures'
import { mockScenariosApprovalsLedger, ids } from './scenarios-approvals-ledger-fixtures'
import measurements from './fixtures/measurements.synthetic.json' with { type: 'json' }
import { workspaceResponse } from './workspace-fixtures'

const uuid = /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/i
const forbidden = /\/api\/(auth(?:\/|$)|health(?:\/|$)|db\/demo|demo\/reset|measurement\/grid|dispatch\/forecasts\/sync|integrations\/electricity-maps)|electricitymaps\.com/i

async function expectSimpleSurface(page: Page) {
  // UUIDs remain valid in API payloads, selected values and internal links.
  expect(await page.locator('body').innerText()).not.toMatch(uuid)
  const accessible = (await page.locator('body').ariaSnapshot()).split('\n').filter(line => !line.trim().startsWith('- /url:')).join('\n')
  expect(accessible).not.toMatch(uuid)
  await expect(page.getByText(/Electricity Maps|API online|API offline|API availability/i)).toHaveCount(0)
  await expect(page.getByRole('link', { name: 'System', exact: true })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'System', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /Sign in|Sign out/i })).toHaveCount(0)
  await expect(page.getByRole('textbox', { name: /UUID/i })).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
}

const screens = [
  { name: 'overview', path: '/dashboard', mock: mockDashboard },
  { name: 'assistant', path: '/ask', mock: mockAgents },
  { name: 'upload', path: '/data', mock: mockIntake },
  { name: 'checks', path: '/quality', mock: mockIntake },
  { name: 'disclosures', path: '/assurance', mock: mockAssurance },
  { name: 'disclosure details', path: `/assurance/${assuranceDraft.id}`, mock: mockAssurance },
  { name: 'suppliers', path: '/procurement/suppliers', mock: mockCatalog },
  { name: 'energy planning', path: '/dispatch', mock: mockDispatch },
  { name: 'energy details', path: `/dispatch/${dispatchScenario.id}`, mock: mockDispatch },
  { name: 'approvals', path: `/approvals?approval=${ids.approval}`, mock: mockScenariosApprovalsLedger },
  { name: 'activity', path: `/runs/${runId}`, mock: mockAgents },
  { name: 'evidence trail', path: `/ledger?event=${ids.event}`, mock: mockScenariosApprovalsLedger },
]

for (const screen of screens) {
  test(`${screen.name} uses readable records without removed UI, live calls or overflow`, async ({ page }, testInfo) => {
    const requests: string[] = [], errors: string[] = []
    page.on('request', request => { if (forbidden.test(request.url())) requests.push(request.url()) })
    page.on('pageerror', error => errors.push(error.message))
    await screen.mock(page)
    await page.goto(screen.path)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    await expect(page.getByText('Maverick Manufacturing (synthetic)').first()).toBeVisible()
    await expect(page.getByRole('status', { name: 'Loading data', exact: true })).toHaveCount(0)
    if (screen.name === 'overview') await expect(page.locator('.recharts-bar-rectangle')).toHaveCount(6)
    if (screen.name === 'evidence trail') await expect(page.locator('.react-flow__node').first()).toBeVisible()
    await expectSimpleSurface(page)
    expect(requests).toEqual([])
    expect(errors).toEqual([])
    await page.screenshot({ path: testInfo.outputPath('simplified-workflow.png'), fullPage: true })
  })
}

test('overview exposes five workflow steps, named navigation and a working refresh icon', async ({ page }) => {
  const calls = await mockDashboard(page)
  await page.goto('/dashboard')
  const workflow = page.getByRole('navigation', { name: 'Carbon workflow', exact: true })
  await expect(workflow.getByRole('link')).toHaveCount(5)
  await expect(workflow.getByRole('link')).toHaveText(['1Upload data', '2Check data', '3Measure', '4Plan', '5Review'])
  const mobileMenu = page.getByRole('button', { name: 'Open navigation', exact: true })
  if (await mobileMenu.isVisible()) await mobileMenu.click()
  for (const name of ['Overview', 'Assistant', 'Upload data', 'Check data', 'Emissions', 'Disclosures', 'Suppliers', 'Energy planning', 'Approvals', 'Activity', 'Evidence trail']) {
    await expect(page.getByRole('navigation').getByRole('link', { name, exact: true }).first()).toBeAttached()
  }
  if (await page.getByRole('dialog').isVisible()) await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).click()
  const refresh = page.getByRole('button', { name: 'Refresh dashboard', exact: true })
  await expect(refresh).toBeEnabled()
  const reads = calls.filter(call => call.url.pathname === '/api/measurements').length
  await refresh.click()
  await expect.poll(() => calls.filter(call => call.url.pathname === '/api/measurements').length).toBeGreaterThan(reads)
  expect(calls.some(call => forbidden.test(call.url.href))).toBe(false)
})

test('saved runs are selected and searched by name while IDs stay internal', async ({ page }) => {
  const calls = await mockAgents(page)
  await page.goto('/runs')
  const form = page.getByRole('form', { name: 'Open run', exact: true })
  const selection = form.getByRole('combobox', { name: 'Saved run', exact: true })
  await expect(selection).toBeEnabled()
  await form.getByRole('button', { name: 'Find a record', exact: true }).click()
  await form.getByRole('textbox', { name: 'Search records', exact: true }).fill('measurement')
  await expect.poll(() => calls.some(call => call.url.searchParams.get('search') === 'measurement')).toBe(true)
  await selection.selectOption({ label: 'Synthetic measurement run' })
  await expect(selection).toHaveValue(runId)
  await expectSimpleSurface(page)
  await form.getByRole('button', { name: 'Open run', exact: true }).click()
  await expect(page).toHaveURL(`/runs/${runId}`)
  await expect(page.getByRole('region', { name: 'Verified facts', exact: true })).toContainText('123.000000000000001 kgCO2e')
})

test('measurement lineage renders real API nodes and relationships with selection and zoom', async ({ page }, testInfo) => {
  const record = measurements.records[0]
  await mockDashboard(page, async (route, url) => {
    if (url.pathname === `/api/measurements/${record.summary.id}`) {
      await route.fulfill({ json: record.detail }); return true
    }
    if (url.pathname === `/api/measurements/${record.summary.id}/lineage`) {
      await route.fulfill({ json: record.lineage }); return true
    }
    if (url.pathname === `/api/measurements/${record.summary.id}/breakdown`) {
      await route.fulfill({ json: {
        measurement_id: record.summary.id, metric_key: record.summary.metric_key,
        status: record.summary.status, unit: 'kgCO2e', total_kgco2e: record.detail.value_kgco2e,
        facts: record.detail.facts,
        items: record.detail.calculations.map(item => ({
          calculation_id: item.id, activity_record_id: item.activity_record_id,
          raw_activity_record_id: record.detail.inputs[0].raw_activity_record_id,
          source_document_id: record.detail.inputs[0].source_document_id,
          activity_date: record.detail.inputs[0].activity_date,
          material_code: record.detail.inputs[0].material_code,
          interval_start: null, quantity: item.normalized_quantity_kg, quantity_unit: 'kg',
          emissions_kgco2e: item.emissions_kgco2e, emission_factor_id: item.emission_factor_id,
          grid_intensity_point_id: null, output_hash: item.output_hash,
        })),
      } }); return true
    }
  })
  await page.goto(`/measurement/${record.summary.id}`)
  await page.getByRole('tab', { name: 'Lineage', exact: true }).click()
  const diagram = page.getByRole('figure', { name: 'Evidence lineage diagram', exact: true })
  await expect(diagram.locator('.react-flow__node')).toHaveCount(record.lineage.nodes.length)
  await expect(diagram.locator('.react-flow__edge')).toHaveCount(record.lineage.edges.length)
  await page.screenshot({ path: testInfo.outputPath('measurement-lineage.png'), fullPage: true })
  const node = record.lineage.nodes.find(item => item.id !== record.lineage.root_event_id)!
  await diagram.locator(`.react-flow__node[data-id="${node.id}"] button`).click()
  await expect(diagram.locator(`.react-flow__node[data-id="${node.id}"]`)).toHaveClass(/selected/)
  await expect(page.getByRole('region', { name: 'Selected lineage record' })).toBeVisible()
  const viewport = diagram.locator('.react-flow__viewport')
  const before = await viewport.getAttribute('style')
  await diagram.getByRole('button', { name: 'Zoom in', exact: true }).click()
  await expect.poll(() => viewport.getAttribute('style')).not.toBe(before)
  await diagram.getByRole('button', { name: 'Zoom out', exact: true }).click()
  await diagram.getByRole('button', { name: 'Fit evidence diagram', exact: true }).click()
  await page.screenshot({ path: testInfo.outputPath('measurement-lineage.png'), fullPage: true })
  await expectSimpleSurface(page)
})

test('workspace option fixtures honor record lookup, search, role, scope and pagination', () => {
  const response = (query: string) => workspaceResponse(new URL(`http://127.0.0.1:3100/api/workspace/options?company_id=${id(1)}&${query}`))!
  expect(response('kind=actors&role=approver').items).toEqual([{ id: id(6004), label: 'Synthetic approver', description: null, status: 'active', role: 'approver' }])
  expect(response(`kind=runs&id=${runId}`).items[0].label).toBe('Synthetic measurement run')
  expect(response('kind=products&search=ALUMINIUM&limit=2&offset=2')).toMatchObject({ total: 9, limit: 2, offset: 2, items: [{ id: id(3001) }, { id: id(3002) }] })
  expect(response(`kind=runs&site_id=${id(999)}`).items).toEqual([])
  expect(response('kind=runs&search=no-such-record').items).toEqual([])
})

test('named selection survives pagination, failed list reads, retry and empty search', async ({ page }) => {
  let failPage = true
  const options = Array.from({ length: 55 }, (_, index) => ({ id: id(1701 + index), label: index ? `Synthetic run ${index + 1}` : 'Synthetic measurement run', description: null, status: null, role: null }))
  const calls = await mockAgents(page, async (route, url) => {
    if (url.pathname !== '/api/workspace/options' || url.searchParams.get('kind') !== 'runs') return
    const params = url.searchParams, offset = Number(params.get('offset') ?? 0), limit = Number(params.get('limit') ?? 50)
    if (offset === 50 && failPage) {
      await route.fulfill({ status: 503, json: { detail: { code: 'records_unavailable', message: 'Saved records are unavailable.', retryable: false } } }); return true
    }
    const items = options.filter(item => (!params.get('id') || item.id === params.get('id')) && (!params.get('search') || item.label.toLowerCase().includes(params.get('search')!.toLowerCase())))
    await route.fulfill({ json: { items: items.slice(offset, offset + limit), total: items.length, limit, offset } }); return true
  })
  await page.goto('/runs')
  const form = page.getByRole('form', { name: 'Open run', exact: true })
  const select = form.getByLabel('Saved run', { exact: true })
  await select.selectOption({ label: 'Synthetic measurement run' })
  await expect(form.getByText('1-50 of 55', { exact: true })).toBeVisible()
  await form.getByRole('button', { name: 'Next records', exact: true }).click()
  await expect(form.getByRole('button', { name: 'Retry loading records', exact: true })).toBeVisible()
  await expect(select).toBeDisabled()
  failPage = false
  await form.getByRole('button', { name: 'Retry loading records', exact: true }).click()
  await expect(form.getByText('51-55 of 55', { exact: true })).toBeVisible()
  await expect(select).toHaveValue(runId)
  await expect(select.locator('option:checked')).toHaveText('Synthetic measurement run')
  expect(calls.some(call => call.url.searchParams.get('id') === runId)).toBe(true)
  await form.getByRole('button', { name: 'Find a record', exact: true }).click()
  await form.getByRole('textbox', { name: 'Search records', exact: true }).fill('no matching saved run')
  await expect(select.getByRole('option', { name: 'No matching records', exact: true })).toBeAttached()
  await expect(select).toHaveValue(runId)
  await expectSimpleSurface(page)
  await form.getByRole('button', { name: 'Open run', exact: true }).click()
  await expect(page).toHaveURL(`/runs/${runId}`)
})

test('a failed selected-record lookup exposes a retry and recovers its readable label', async ({ page }) => {
  let failed = true
  const calls = await mockAgents(page, async (route, url) => {
    if (url.pathname !== '/api/workspace/options' || url.searchParams.get('kind') !== 'runs') return
    if (url.searchParams.get('id') === runId && failed) {
      await route.fulfill({ status: 503, json: { detail: { code: 'selected_record_unavailable', message: 'The selected record is unavailable.', retryable: false } } }); return true
    }
  })
  await page.goto('/runs')
  const form = page.getByRole('form', { name: 'Open run', exact: true })
  const select = form.getByLabel('Saved run', { exact: true })
  await select.selectOption({ label: 'Synthetic measurement run' })
  await form.getByRole('button', { name: 'Find a record', exact: true }).click()
  await form.getByRole('textbox', { name: 'Search records', exact: true }).fill('no matching saved run')
  await expect.poll(() => calls.some(call => call.url.searchParams.get('id') === runId)).toBe(true)
  const retry = form.getByRole('button', { name: /Retry loading (records|selected record)/ })
  await expect(retry).toBeVisible()
  await expectSimpleSurface(page)
  failed = false
  await retry.click()
  await expect(select.locator('option:checked')).toHaveText('Synthetic measurement run')
  await expect(select).toHaveValue(runId)
})

test('a deep-linked default selection survives delayed option loading and unchanged filter submission', async ({ page }) => {
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  const calls = await mockScenariosApprovalsLedger(page, async (route, url) => {
    if (url.pathname !== '/api/workspace/options' || url.searchParams.get('kind') !== 'measurements') return
    await gate
    await route.fulfill({ json: workspaceResponse(url) }); return true
  })
  await page.goto(`/ledger?entity_type=measurement&entity_id=${id(201)}`)
  const record = page.getByLabel('Record', { exact: true })
  try { await expect(record).toBeDisabled() } finally { release() }
  await expect(record).toBeEnabled()
  await expect(record).toHaveValue(id(201))
  await expect(record.locator('option:checked')).toHaveText('Purchased-material emissions (synthetic)')
  await page.getByRole('button', { name: 'Apply filters', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`entity_id=${id(201)}`))
  expect(calls.filter(call => call.url.pathname === '/api/ledger/events').every(call => call.url.searchParams.get('entity_id') === id(201))).toBe(true)
})

test('reviewer lookup retains the approver role when resolving a selected record outside search results', async ({ page }) => {
  const calls = await mockScenariosApprovalsLedger(page)
  await page.goto(`/approvals?approval=${ids.approval}`)
  const reviewer = page.getByLabel('Reviewer', { exact: true })
  await expect(reviewer).toHaveValue(ids.actor)
  await expect(reviewer.getByRole('option', { name: /Synthetic analyst/ })).toHaveCount(0)
  const picker = reviewer.locator('xpath=../../..')
  await picker.getByRole('button', { name: 'Find a record', exact: true }).click()
  await picker.getByRole('textbox', { name: 'Search records', exact: true }).fill('no matching reviewer')
  await expect.poll(() => calls.filter(call => call.url.searchParams.get('id') === ids.actor && call.url.searchParams.get('kind') === 'actors').length).toBeGreaterThan(0)
  const lookups = calls.filter(call => call.url.searchParams.get('id') === ids.actor && call.url.searchParams.get('kind') === 'actors')
  expect(lookups.every(call => call.url.searchParams.get('role') === 'approver')).toBe(true)
  await expect(reviewer).toHaveValue(ids.actor)
  await expect(reviewer.locator('option:checked')).toContainText('Synthetic approver')
})

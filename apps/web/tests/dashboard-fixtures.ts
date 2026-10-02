import type { Page, Route } from '@playwright/test'

// Synthetic contract projections for browser tests, never application fallback data.
export const id = (value: number) =>
  `00000000-0000-4000-8000-${String(value).padStart(12, '0')}`
const hash = 'a'.repeat(64)
const time = '2026-09-30T12:00:00Z'
export const measurement = {
  id: id(201),
  company_id: id(1),
  site_id: id(2),
  reporting_period_id: id(3),
  metric_key: 'emissions.scope3.category1',
  metric_version: '1.0.0',
  category: 'purchased_material',
  value_kgco2e: '12500.1250',
  unit: 'kgCO2e',
  confidence: '0.925',
  status: 'verified',
  ledger_event_id: id(501),
  output_hash: hash,
  created_at: time,
}
const measurements = Array.from({ length: 7 }, (_, index) => ({
  ...measurement,
  id: id(201 + index),
  value_kgco2e: index ? `${3000 + index * 100}.1250` : measurement.value_kgco2e,
}))
const approval = {
  id: id(301),
  company_id: id(1),
  target_type: 'procurement_recommendation',
  target_id: id(401),
  requester_name: 'Synthetic analyst',
  recommendation_id: id(401),
  status: 'pending',
  preview_hash: hash,
  analysis_signature: hash,
  expires_at: new Date(Date.now() + 3_600_000).toISOString(),
  recommended_product_name: 'Recycled aluminium A (synthetic)',
  supplier_name: 'Fixture supplier',
  projected_footprint_kgco2e: '5000.0000',
  avoided_kgco2e: '7500.1250',
  reduction_pct: '60.01',
  cost_delta_pct: '2.50',
  lead_time_delta_days: 1,
  expired: false,
  preview_current: true,
  created_at: time,
}
const approvals = [
  approval,
  { ...approval, id: id(302), preview_current: false },
  { ...approval, id: id(303), expires_at: '2020-01-01T00:00:00Z' },
]
export const event = {
  id: id(501),
  event_type: 'measurement.verified',
  entity_type: 'measurement',
  entity_id: id(201),
  payload_hash: hash,
  created_at: time,
}
const issues = [
  {
    id: id(601),
    code: 'missing_quantity',
    severity: 'error',
    message: 'Quantity is missing in the synthetic activity row.',
    status: 'open',
    row_number: 7,
    field_name: 'quantity',
    created_at: time,
  },
  {
    id: id(602),
    code: 'estimated_factor',
    severity: 'warning',
    message: 'An estimated factor requires review.',
    status: 'open',
    row_number: 12,
    field_name: 'factor',
    created_at: time,
  },
]

function paginate(items: unknown[], url: URL) {
  const limit = Number(url.searchParams.get('limit') || 25)
  const offset = Number(url.searchParams.get('offset') || 0)
  return {
    items: items.slice(offset, offset + limit),
    total: items.length,
    limit,
    offset,
  }
}

export function fixtureFor(url: URL, empty = false): unknown {
  const path = url.pathname.replace('/api', '')
  if (path === '/health') return { status: 'ok', service: 'CarbonMesh API' }
  if (path === '/auth/session')
    return { company_id: id(1), actor_id: id(4), role: 'sustainability_analyst' }
  if (path === '/context/resolve')
    return {
      company: {
        id: id(1),
        name: 'Maverick Manufacturing (synthetic)',
        is_synthetic: true,
      },
      site: {
        id: id(2),
        name: 'Plant B (synthetic)',
        timezone: 'Asia/Kolkata',
      },
      reporting_period: {
        id: id(3),
        name: 'Q3 2026',
        start_date: '2026-07-01',
        end_date: '2026-09-30',
      },
      analysis_signature: hash,
    }
  if (path === '/measurements') return paginate(empty ? [] : measurements, url)
  if (path === `/measurements/${id(201)}`)
    return {
      ...measurement,
      formula: 'kg * kgCO2e_per_kg',
      calculation_run: {
        id: id(701),
        method_key: 'purchased_material_v1',
        method_version: '1.0.0',
        code_version: 'test-v1',
        input_hash: hash,
        output_hash: hash,
        rounding_policy: 'ROUND_HALF_UP',
      },
      inputs: [
        {
          activity_record_id: id(801),
          raw_activity_record_id: id(901),
          source_document_id: id(1001),
          source_row_key: 'synthetic-row-1',
          raw_checksum: hash,
          source_quantity: '10000',
          source_unit: 'kg',
        },
      ],
      factors: [
        {
          id: id(1101),
          name: 'Synthetic material factor',
          version: 'v1',
          factor_value: '1.2500125',
          numerator_unit: 'kgCO2e',
          denominator_unit: 'kg',
          evidence: {
            id: id(1201),
            source_document_id: id(1001),
            source_document_filename: 'synthetic-factor.txt',
            locator: 'line:1',
            checksum: hash,
          },
        },
      ],
      facts: { fact_id: id(201), ledger_event_id: id(501), output_hash: hash },
    }
  if (path === `/measurements/${id(201)}/lineage`)
    return {
      measurement_id: id(201),
      root_event_id: id(501),
      truncated: true,
      nodes: [
        {
          id: id(501),
          node_type: 'ledger_event',
          label: 'Verified synthetic measurement',
        },
        {
          id: id(1201),
          node_type: 'evidence',
          label: 'Synthetic factor evidence',
        },
      ],
      edges: [
        {
          id: id(1301),
          source: id(1201),
          target: id(501),
          relationship_type: 'supports',
        },
      ],
    }
  if (path === '/quality/issues')
    return paginate(
      empty
        ? []
        : issues.filter(
            (item) =>
              !url.searchParams.get('severity') ||
              item.severity === url.searchParams.get('severity'),
          ),
      url,
    )
  if (path === '/approvals') return paginate(empty ? [] : approvals, url)
  if (path === '/ledger/events') return paginate(empty ? [] : [event], url)
  if (path === `/ledger/events/${id(501)}`)
    return {
      ...event,
      payload: { value_kgco2e: '12500.1250', synthetic: true },
      evidence: [
        {
          id: id(1201),
          source_document_id: id(1001),
          source_filename: 'synthetic-factor.txt',
          locator: 'line:1',
          checksum: hash,
          is_synthetic: true,
        },
      ],
      parents: [],
      children: [],
      evidence_truncated: false,
      parents_truncated: false,
      children_truncated: false,
    }
  if (path === '/measurement/grid/latest')
    return {
      site_id: id(2),
      grid_intensity_point_id: id(1401),
      zone: 'IN-NO',
      value: '0.4150',
      unit: 'kgCO2e/kWh',
      provider_timestamp: time,
      is_estimated: true,
      temporal_granularity: 'hourly',
      provenance: {
        provider: 'electricity_maps',
        provider_mode: 'fixture',
        synthetic: true,
        source_document_id: id(1002),
        evidence_item_id: id(1202),
        response_checksum: hash,
        retrieved_at: time,
      },
    }
  if (
    [
      '/procurement/suppliers',
      '/procurement/products',
      '/assurance/standards',
      '/dispatch/loads',
    ].includes(path)
  ) {
    return paginate(
      empty ? [] : [{ id: id(1501), name: 'Synthetic catalog record' }],
      url,
    )
  }
  return undefined
}

export async function mockDashboard(
  page: Page,
  override?: (route: Route, url: URL) => Promise<boolean | void>,
  empty = false,
) {
  const requests: { url: URL; method: string; body: unknown }[] = []
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    requests.push({
      url,
      method: request.method(),
      body: request.postDataJSON(),
    })
    if (override && (await override(route, url))) return
    if (empty && url.pathname === '/api/measurement/grid/latest') {
      await route.fulfill({
        status: 404,
        json: {
          detail: {
            code: 'grid_intensity_not_found',
            message: 'No grid data.',
            retryable: false,
          },
        },
      })
      return
    }
    const body = fixtureFor(url, empty)
    if (body === undefined)
      throw new Error(`Unexpected request: ${request.method()} ${url.pathname}`)
    await route.fulfill({ json: body })
  })
  return requests
}

import type { Page, Route } from '@playwright/test'
import { id, mockDashboard } from './dashboard-fixtures'

// Synthetic test-only projections. Every API call is intercepted, including commands.
export const time = '2026-09-30T12:00:00Z'
export const importId = id(8100)
export const documentId = id(8101)
export const qualityIssues = Array.from({ length: 27 }, (_, index) => ({
  id: id(8200 + index),
  company_id: id(1),
  raw_activity_record_id: id(8300 + index),
  activity_record_id: null,
  import_id: importId,
  row_number: index + 1,
  issue_type: 'validation',
  code:
    index === 0
      ? 'missing_quantity'
      : index === 1
        ? 'estimated_factor'
        : `review_${index}`,
  severity: index === 0 ? 'error' : index === 1 ? 'warning' : 'info',
  field_name: 'quantity',
  message: `Synthetic intake issue ${index}`,
  status: 'open',
  details: {},
  created_at: time,
  updated_at: time,
}))
export const importResult = {
  import_id: importId,
  data_source_id: importId,
  source_document_id: documentId,
  import_type: 'activity',
  status: 'completed_with_errors',
  accepted_count: 2,
  rejected_count: 1,
  issue_count: 27,
  returned_issue_count: 2,
  issues_truncated: true,
  is_synthetic: true,
  issues: qualityIssues.slice(0, 2),
  created_at: time,
  updated_at: time,
}
export const metrics = {
  company_id: id(1),
  count: 2,
  items: [
    {
      id: id(101),
      key: 'emissions.scope2.location_based',
      name: 'Hourly electricity (synthetic)',
      version: '2.0.0',
      canonical_unit: 'kgCO2e',
      method_version: '2.0.0',
    },
    {
      id: id(102),
      key: 'emissions.scope3.category1',
      name: 'Purchased material (synthetic)',
      version: '1.0.0',
      canonical_unit: 'kgCO2e',
      method_version: '1.0.0',
    },
  ],
}
export const sourceReceipt = {
  replayed: false,
  ingestion_method_id: 'synthetic-ingestion-v1',
  extraction_method_id: 'synthetic-text-v1',
  chunking_method_id: 'synthetic-chunks-v1',
  embedding_model_id: 'synthetic-embedding',
  decoded_size_bytes: 18,
  evidence_count: 1,
  source: {
    id: importId,
    company_id: id(1),
    site_id: id(2),
    name: 'Synthetic evidence',
    source_type: 'synthetic',
    status: 'ready',
    is_synthetic: true,
  },
  document: {
    id: documentId,
    data_source_id: importId,
    filename: 'synthetic.txt',
    content_type: 'text/plain',
    checksum: 'a'.repeat(64),
    version: 1,
    size_bytes: 18,
  },
  evidence: [
    {
      id: id(8400),
      source_document_id: documentId,
      evidence_type: 'supplier_declaration',
      locator: 'chunk:000001',
      checksum: 'b'.repeat(64),
      embedding_model: 'synthetic-embedding',
      embedded_at: time,
    },
  ],
}
export const factor = {
  id: id(8500),
  company_id: id(1),
  metric_definition_id: id(102),
  evidence_item_id: id(8400),
  factor_code: 'SYNTHETIC-AL',
  version: 'test-v1',
  name: 'Synthetic aluminium factor',
  material_code: 'AL',
  product_code: null,
  geography: 'GLOBAL',
  factor_value: '1.123456789012',
  numerator_unit: 'kgCO2e',
  denominator_unit: 'kg',
  effective_from: '2026-07-01',
  effective_to: null,
  source_quality: '0.95',
  factor_specificity: '0.8',
  factor_recency: '1',
  status: 'active',
  created_at: time,
}
export async function mockIntake(
  page: Page,
  override?: (route: Route, url: URL) => Promise<boolean | void>,
) {
  return mockDashboard(page, async (route, url) => {
    if (override && (await override(route, url))) return true
    const method = route.request().method()
    if (method !== 'GET' && url.pathname !== '/api/context/resolve')
      throw new Error(`Unapproved test command: ${method} ${url.pathname}`)
    let result: unknown
    if (url.pathname === '/api/semantic/metrics') result = metrics
    if (url.pathname === `/api/imports/${importId}`) result = importResult
    if (url.pathname === '/api/quality/issues') {
      const params = url.searchParams
      const items = qualityIssues.filter((item) =>
        ['status', 'severity', 'code', 'issue_type', 'import_id'].every(
          (key) =>
            !params.get(key) ||
            item[key as keyof typeof item] === params.get(key),
        ),
      )
      const limit = Number(params.get('limit') ?? 25),
        offset = Number(params.get('offset') ?? 0)
      result = {
        items: items.slice(offset, offset + limit),
        limit,
        offset,
        total: items.length,
      }
    }
    if (url.pathname === '/api/emission-factors') {
      const limit = Number(url.searchParams.get('limit') ?? 25),
        offset = Number(url.searchParams.get('offset') ?? 0)
      const matches =
        !url.searchParams.get('material_code') ||
        url.searchParams.get('material_code') === factor.material_code
      result = {
        items: offset || !matches ? [] : [factor],
        total: matches ? 1 : 0,
        limit,
        offset,
      }
    }
    if (result === undefined) return
    await route.fulfill({ json: result })
    return true
  })
}

import type { Page, Route } from '@playwright/test'
import type { Product, Supplier } from '../src/features/procurement/schemas'
import { id, mockDashboard } from './dashboard-fixtures'

// Synthetic browser-test data only. Production queries never import this module.
export const products: Product[] = Array.from({ length: 8 }, (_, index) => ({
  id: id(3000 + index),
  supplier_id: id(2000 + (index === 1 ? 0 : index)),
  supplier_code: `TEST-SUP-${index === 1 ? 0 : index}`,
  supplier_name: `Synthetic supplier ${index === 1 ? 0 : index}`,
  supplier_country_code: index % 2 ? 'DE' : 'IN',
  supplier_status: index === 7 ? 'inactive' : 'active',
  risk: 'low',
  product_code: `TEST-PROD-${index}`,
  name: `Synthetic aluminium ${index}`,
  material_code: index === 2 ? 'steel' : 'recycled_aluminium',
  category: 'purchased_material',
  description: 'Synthetic catalog product for browser contract tests.',
  pcf_kgco2e_per_unit: index ? '1.500000000000' : '1.123456789012',
  pcf_unit: 'kgCO2e/kg',
  circularity_score: '85.1250',
  recycled_content_pct: '92.5000',
  recyclable_pct: '95.0000',
  evidence_quality_score: '82.2500',
  lead_time_days: 8,
  unit_cost: '3.123456',
  currency: 'USD',
  effective_from: '2026-07-01',
  effective_to: null,
  is_active: index !== 6,
  evidence_item_id: index === 1 ? null : id(4000 + index),
  evidence_available: index !== 1,
}))

export const suppliers: Supplier[] = Array.from({ length: 8 }, (_, index) => ({
  id: id(2000 + index),
  supplier_code: `TEST-SUP-${index}`,
  name: `Synthetic supplier ${index}`,
  country_code: index % 2 ? 'DE' : 'IN',
  status: index === 7 ? 'inactive' : 'active',
  risk: 'low',
  metadata: { synthetic: true },
  product_count: products.filter(
    (item) => item.supplier_id === id(2000 + index),
  ).length,
  active_product_count: products.filter(
    (item) => item.supplier_id === id(2000 + index) && item.is_active,
  ).length,
  created_at: '2026-09-01T12:00:00Z',
  updated_at: '2026-09-30T12:00:00Z',
}))

export function catalogResponse(url: URL) {
  const params = url.searchParams
  const active = params.get('active_only') !== 'false'
  const search = params.get('search')?.toLowerCase()
  let items: Supplier[] | Product[]
  if (url.pathname === '/api/procurement/suppliers') {
    items = suppliers.filter(
      (item) =>
        (!active || item.status === 'active') &&
        (!params.get('country_code') ||
          item.country_code === params.get('country_code')) &&
        (!search ||
          `${item.name} ${item.supplier_code}`.toLowerCase().includes(search)),
    )
  } else if (url.pathname === '/api/procurement/products') {
    items = products.filter(
      (item) =>
        (!active || (item.is_active && item.supplier_status === 'active')) &&
        (!params.get('supplier_id') ||
          item.supplier_id === params.get('supplier_id')) &&
        (!params.get('material_code') ||
          item.material_code === params.get('material_code')) &&
        (!params.get('category') || item.category === params.get('category')) &&
        (!search ||
          `${item.name} ${item.product_code} ${item.supplier_name}`
            .toLowerCase()
            .includes(search)),
    )
  } else return undefined
  const offset = Number(params.get('offset') ?? 0)
  const limit = Number(params.get('limit') ?? 10)
  return {
    items: items.slice(offset, offset + limit),
    total: items.length,
    offset,
    limit,
  }
}

export async function mockCatalog(
  page: Page,
  override?: (route: Route, url: URL) => Promise<boolean | void>,
) {
  return mockDashboard(page, async (route, url) => {
    if (override && (await override(route, url))) return true
    const result = catalogResponse(url)
    if (!result) return
    await route.fulfill({ json: result })
    return true
  })
}

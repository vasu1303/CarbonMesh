// Named synthetic records for intercepted browser requests only.
const id = (value: number) => `00000000-0000-4000-8000-${String(value).padStart(12, '0')}`
const option = (value: number, label: string, role: string | null = null) => ({
  id: id(value), label, description: null, status: role ? 'active' : null, role,
})

export const workspaceOptions = {
  actors: [option(4, 'Synthetic analyst', 'sustainability_analyst'), option(6004, 'Synthetic approver', 'approver')],
  metrics: [option(101, 'Hourly electricity (synthetic)'), option(102, 'Purchased material (synthetic)')],
  methods: [option(801, 'Procurement impact (synthetic)'), option(6005, 'Supplier scoring (synthetic)'), option(7500, 'Dispatch optimization (synthetic)')],
  policies: [option(1601, 'Synthetic workspace policy')],
  imports: [option(8100, 'Synthetic activity import')],
  documents: [option(8101, 'synthetic-source.txt'), option(1001, 'synthetic-factor.txt'), option(7200, 'Synthetic hourly forecast')],
  activity: [option(801, 'Synthetic material activity'), option(900001, 'Synthetic raw activity')],
  evidence: [option(1201, 'Synthetic factor evidence'), option(6200, 'assurance-synthetic-evidence.txt'), option(6011, 'Synthetic product evidence'), option(8400, 'Synthetic uploaded evidence')],
  measurements: [option(201, 'Purchased-material emissions (synthetic)'), option(202, 'Earlier material emissions (synthetic)'), option(900000, 'Plant B purchased-material emissions (synthetic)')],
  suppliers: Array.from({ length: 8 }, (_, index) => option(2000 + index, `Synthetic supplier ${index}`)),
  products: [option(1501, 'Recycled aluminium (synthetic)'), ...Array.from({ length: 8 }, (_, index) => option(3000 + index, `Synthetic aluminium ${index}`))],
  standards: [option(6100, 'Synthetic disclosure standard')],
  loads: [option(7100, 'Synthetic batch load')],
  forecasts: [option(7200, 'Synthetic hourly forecast')],
  assurance: [option(6300, 'Synthetic disclosure review')],
  procurement: [option(6001, 'Synthetic aluminium comparison')],
  dispatch: [option(7600, 'Synthetic energy plan')],
  runs: [option(1701, 'Synthetic measurement run')],
  ledger: [option(501, 'Verified synthetic measurement'), option(6010, 'Synthetic procurement decision')],
}

export function workspaceResponse(url: URL) {
  if (url.pathname !== '/api/workspace/options') return undefined
  const params = url.searchParams
  const kind = params.get('kind') as keyof typeof workspaceOptions
  if (!Object.hasOwn(workspaceOptions, kind)) throw new Error(`Unknown workspace record kind: ${kind}`)
  const limit = Number(params.get('limit') ?? 50)
  const offset = Number(params.get('offset') ?? 0)
  const search = params.get('search')?.toLowerCase()
  const inScope = [['company_id', 1], ['site_id', 2], ['reporting_period_id', 3]].every(
    ([key, value]) => !params.has(String(key)) || params.get(String(key)) === id(Number(value)),
  )
  const items = inScope ? workspaceOptions[kind].filter(item =>
    (!params.get('id') || item.id === params.get('id')) &&
    (!params.get('role') || item.role === params.get('role')) &&
    (!search || item.label.toLowerCase().includes(search)),
  ) : []
  return { items: items.slice(offset, offset + limit), total: items.length, limit, offset }
}

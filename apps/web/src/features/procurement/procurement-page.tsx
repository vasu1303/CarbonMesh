import { useIsFetching, useQuery, useQueryClient } from '@tanstack/react-query'
import { Building2, Package, Plus, RefreshCw, X } from 'lucide-react'
import { Link, useOutletContext, useSearchParams } from 'react-router-dom'

import {
  EmptyState,
  PageControls,
  QueryRefresh,
  QueryState,
} from '@/components/query-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { displayText } from '@/lib/presentation'
import type { WorkspaceScope } from '@/lib/workspace'
import { CatalogFiltersForm } from './components/catalog-filters'
import { ProductTable } from './components/product-table'
import { SupplierList } from './components/supplier-list'
import { catalogKey, catalogQueries } from './queries'
import { catalogFiltersSchema, type CatalogFilters } from './schemas'

function Catalog({ filters }: { filters: CatalogFilters }) {
  const scope = useOutletContext<WorkspaceScope>()
  const [params, setParams] = useSearchParams()
  const suppliers = useQuery(
    catalogQueries.suppliers(scope.company_id, filters),
  )
  const products = useQuery(catalogQueries.products(scope.company_id, filters))
  const context = useQuery(catalogQueries.context(scope))
  const client = useQueryClient()
  const fetching = useIsFetching({ queryKey: catalogKey }) > 0
  const view = filters.view
  const selectedSupplier =
    suppliers.data?.items.find((item) => item.id === filters.supplier)?.name ??
    products.data?.items[0]?.supplier_name ??
    'Selected supplier'

  function update(
    values: Record<string, string | number | null>,
    resetPages = true,
  ) {
    const next = new URLSearchParams(params)
    if (resetPages) {
      next.delete('supplier_offset')
      next.delete('product_offset')
    }
    for (const [key, value] of Object.entries(values)) {
      if (value === null || value === '') next.delete(key)
      else next.set(key, String(value))
    }
    setParams(next, { flushSync: true })
  }

  function pagination(
    page: { total: number; offset: number; limit: number; items: unknown[] },
    kind: 'supplier' | 'product',
  ) {
    return (
      <div className="mt-5 flex flex-wrap items-center justify-between gap-4 border-t pt-4">
        <div className="flex items-center gap-2">
          <label
            htmlFor={`${kind}-page-size`}
            className="text-xs text-muted-foreground"
          >
            Rows per page
          </label>
          <NativeSelect
            id={`${kind}-page-size`}
            size="sm"
            value={filters.limit}
            onChange={(event) => update({ limit: event.target.value })}
          >
            {[5, 10, 25, 50].map((limit) => (
              <NativeSelectOption key={limit} value={limit}>
                {limit}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </div>
        <div className="min-w-48">
          {page.items.length === 0 && page.offset > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => update({ [`${kind}_offset`]: 0 }, false)}
            >
              Return to first page
            </Button>
          )}
          <PageControls
            {...page}
            count={page.items.length}
            onChange={(offset) => update({ [`${kind}_offset`]: offset }, false)}
          />
        </div>
      </div>
    )
  }

  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-4 px-5 py-6 sm:px-8">
        <div>
          <p className="mb-2 text-xs font-medium text-muted-foreground">
            Procurement
          </p>
          <h1 className="text-2xl font-semibold">Suppliers</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Suppliers, product footprints and commercial terms
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button asChild size="sm">
            <Link to="/procurement/scenarios/new">
              <Plus />
              New scenario
            </Link>
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={fetching}
            onClick={() =>
              void client.invalidateQueries({ queryKey: catalogKey })
            }
          >
            <RefreshCw className={fetching ? 'motion-safe:animate-spin' : ''} />
            Refresh catalog
          </Button>
        </div>
      </div>
      <section
        aria-label="Catalog context"
        className="mx-5 mb-5 border-y py-3 sm:mx-8"
      >
        <QueryState query={context}>
          {(data) => (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
              <span className="font-medium">
                {displayText(data.company.name)}
              </span>
              <span className="text-muted-foreground">
                Company-wide catalog
              </span>
              <Badge variant="outline" className="sm:ml-auto">
                {data.company.is_synthetic
                  ? 'Synthetic data'
                  : 'Workspace data'}
              </Badge>
            </div>
          )}
        </QueryState>
        {context.isError && !context.data && (
          <p className="pb-2 text-xs text-amber-700 dark:text-amber-400">
            Catalog provenance unavailable. Records below are not verified for
            demo provenance.
          </p>
        )}
      </section>
      <Tabs
        value={view}
        onValueChange={(value) => update({ view: value }, false)}
        className="gap-0"
      >
        <div className="flex flex-wrap items-center justify-between gap-4 px-5 pb-5 sm:px-8">
          <TabsList aria-label="Procurement catalog">
            <TabsTrigger value="suppliers">
              <Building2 className="size-4" />
              Suppliers
              {!suppliers.isError && suppliers.data && (
                <span className="ml-1 font-mono text-xs">
                  {suppliers.data.total}
                </span>
              )}
            </TabsTrigger>
            <TabsTrigger value="products">
              <Package className="size-4" />
              Products
              {!products.isError && products.data && (
                <span className="ml-1 font-mono text-xs">
                  {products.data.total}
                </span>
              )}
            </TabsTrigger>
          </TabsList>
          <label className="flex items-center gap-2 text-xs font-medium">
            <Input
              type="checkbox"
              className="size-4 rounded accent-emerald-700"
              checked={filters.active === 'true'}
              onChange={(event) =>
                update({ active: String(event.target.checked) })
              }
            />
            Active only
          </label>
        </div>
        <div className="border-y bg-card px-5 py-5 sm:px-8">
          <CatalogFiltersForm
            key={params.toString()}
            filters={filters}
            onApply={(values) =>
              update(
                view === 'suppliers'
                  ? { supplier_search: values.search, country: values.country }
                  : {
                      product_search: values.search,
                      material: values.material,
                      category: values.category,
                    },
              )
            }
            onClear={() => setParams({ view }, { flushSync: true })}
          />
          {view === 'products' && filters.supplier && (
            <div className="mt-4 flex min-w-0 items-start gap-2 text-xs">
              <span className="min-w-0 py-2 wrap-anywhere text-muted-foreground">
                Supplier:{' '}
                <span className="text-foreground">
                  {displayText(selectedSupplier)}
                </span>
              </span>
              <Button
                variant="ghost"
                size="icon"
                className="size-8 shrink-0"
                aria-label="Clear supplier filter"
                title="Clear supplier filter"
                onClick={() => update({ supplier: null })}
              >
                <X />
              </Button>
            </div>
          )}
          <TabsContent value="suppliers" className="mt-6 min-w-0">
            <section aria-label="Supplier results">
              <div className="mb-4 flex items-center justify-between gap-3">
                <h2 className="text-sm font-semibold">Suppliers</h2>
                <QueryRefresh query={suppliers} label="suppliers" />
              </div>
              <QueryState query={suppliers}>
                {(page) => (
                  <>
                    {page.items.length ? (
                      <SupplierList
                        items={page.items}
                        onProducts={(id) =>
                          update({
                            view: 'products',
                            supplier: id,
                            product_search: null,
                            material: null,
                            category: null,
                          })
                        }
                      />
                    ) : (
                      <EmptyState
                        title={
                          page.offset
                            ? 'No suppliers on this page'
                            : 'No suppliers found'
                        }
                        detail="No catalog records match these filters."
                      />
                    )}
                    {pagination(page, 'supplier')}
                  </>
                )}
              </QueryState>
            </section>
          </TabsContent>
          <TabsContent value="products" className="mt-6 min-w-0">
            <section aria-label="Product results" className="min-w-0">
              <div className="mb-4 flex items-center justify-between gap-3">
                <h2 className="text-sm font-semibold">Products</h2>
                <QueryRefresh query={products} label="products" />
              </div>
              <QueryState query={products}>
                {(page) => (
                  <>
                    {page.items.length ? (
                      <ProductTable items={page.items} />
                    ) : (
                      <EmptyState
                        title={
                          page.offset
                            ? 'No products on this page'
                            : 'No products found'
                        }
                        detail="No catalog records match these filters."
                      />
                    )}
                    {pagination(page, 'product')}
                  </>
                )}
              </QueryState>
            </section>
          </TabsContent>
        </div>
      </Tabs>
    </>
  )
}

export default function ProcurementPage() {
  const [params, setParams] = useSearchParams()
  const parsed = catalogFiltersSchema.safeParse(Object.fromEntries(params))
  if (!parsed.success)
    return (
      <section role="alert" className="space-y-4 px-5 py-8 sm:px-8">
        <h1 className="text-xl font-semibold">Invalid catalog filters</h1>
        <p className="text-sm text-muted-foreground">
          The catalog URL contains an invalid identifier or filter value.
        </p>
        <Button variant="outline" onClick={() => setParams({})}>
          Reset filters
        </Button>
      </section>
    )
  return <Catalog filters={parsed.data} />
}

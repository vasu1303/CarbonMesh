import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery } from '@tanstack/react-query'
import { ArrowRight, Plus, Search } from 'lucide-react'
import { useRef, useState } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { Link, useNavigate, useOutletContext } from 'react-router-dom'
import { z } from 'zod'
import {
  EmptyState,
  PageControls,
  QueryRefresh,
  QueryState,
} from '@/components/query-state'
import { RecordSelect } from '@/components/record-select'
import { displayText, humanize } from '@/lib/presentation'
import { OpenArtifact } from '@/features/assurance/workflow-ui'
import { ActorField } from '@/features/data/intake-ui'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { useWorkspaceActor } from '@/services/use-workspace-actor'
import {
  CommandError,
  Field,
  WorkspaceProvenance,
} from '@/features/approvals/review-components'
import { measurementQueries } from '@/features/measurements/queries'
import type { WorkspaceScope } from '@/lib/workspace'
import { apiRequest } from '@/services/api'
import { catalogQueries } from './queries'
import { catalogFiltersSchema, type Product } from './schemas'
import { scenarioFormSchema, scenarioSchema } from './scenario-schemas'
import { scenarioInScope } from './scenario-queries'

function CreateScenario() {
  const scope = useOutletContext<WorkspaceScope>()
  const actor = useWorkspaceActor()
  const navigate = useNavigate()
  const [filters, setFilters] = useState(
    catalogFiltersSchema.parse({ limit: 10 }),
  )
  const [measurementOffset, setMeasurementOffset] = useState(0)
  const [selected, setSelected] = useState<Product | null>(null)
  const [selectedMeasurement, setSelectedMeasurement] = useState<{
    id: string
    label: string
  } | null>(null)
  const retry = useRef<{ payload: string; key: string } | null>(null)
  const suppliers = useQuery(
    catalogQueries.suppliers(scope.company_id, filters),
  )
  const products = useQuery(catalogQueries.products(scope.company_id, filters))
  const measurements = useQuery(
    measurementQueries.list(scope, {
      status: 'verified',
      category: 'purchased_material',
      limit: 10,
      offset: measurementOffset,
    }),
  )
  const form = useForm<
    z.input<typeof scenarioFormSchema>,
    unknown,
    z.output<typeof scenarioFormSchema>
  >({
    resolver: zodResolver(scenarioFormSchema),
    defaultValues: {
      current_product_id: '',
      carbon_measurement_id: '',
      method_definition_id: '',
      requested_by: actor.actorId,
      quantity: '',
      quantity_unit: '',
      current_unit_cost: '',
      currency: '',
      max_cost_increase_pct: '',
      max_lead_time_days: '',
      minimum_circularity_score: '',
      allowed_material_codes: '',
      excluded_risk_levels: '',
      approval_expires_at: '',
    },
  })
  const watched = useWatch({ control: form.control })
  const create = useMutation({
    mutationFn: async (values: z.output<typeof scenarioFormSchema>) => {
      const body = {
        company_id: scope.company_id,
        site_id: scope.site_id,
        reporting_period_id: scope.reporting_period_id,
        current_product_id: values.current_product_id,
        carbon_measurement_id: values.carbon_measurement_id,
        method_definition_id: values.method_definition_id,
        requested_by: values.requested_by,
        quantity: values.quantity,
        quantity_unit: values.quantity_unit,
        current_unit_cost: values.current_unit_cost || null,
        currency: values.currency || null,
        max_cost_increase_pct: values.max_cost_increase_pct,
        max_lead_time_days: values.max_lead_time_days,
        minimum_circularity_score: values.minimum_circularity_score,
        material_constraints: {
          allowed_material_codes: values.allowed_material_codes,
          excluded_risk_levels: values.excluded_risk_levels,
        },
        approval_expires_at: values.approval_expires_at
          ? new Date(values.approval_expires_at).toISOString()
          : null,
      }
      const payload = JSON.stringify(body)
      if (retry.current?.payload !== payload)
        retry.current = { payload, key: crypto.randomUUID() }
      return apiRequest(
        '/procurement/scenarios',
        scenarioSchema.refine(
          (v) =>
            scenarioInScope(v, scope) &&
            v.current_product.id === values.current_product_id &&
            v.carbon_measurement_id === values.carbon_measurement_id &&
            v.method.id === values.method_definition_id,
        ),
        {
          signal: new AbortController().signal,
          method: 'POST',
          body: { ...body, idempotency_key: retry.current.key },
        },
      )
    },
    onSuccess: (result) => navigate(`/procurement/scenarios/${result.id}`),
  })
  const errors = form.formState.errors
  const textField = (
    name: keyof z.input<typeof scenarioFormSchema>,
    label: string,
    placeholder?: string,
    type = 'text',
  ) => (
    <Field label={label} error={errors[name]?.message}>
      <Input
        {...form.register(name)}
        placeholder={placeholder}
        type={type}
        autoComplete="off"
      />
    </Field>
  )

  return (
    <div className="min-w-0 space-y-6 px-5 py-6 sm:px-8">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="mb-2 text-xs text-muted-foreground">Procurement</p>
          <h1 className="text-2xl font-semibold">New scenario</h1>
        </div>
        <Button asChild variant="outline" size="sm">
          <Link to="/procurement/suppliers">
            Supplier catalog
            <ArrowRight />
          </Link>
        </Button>
      </header>
      <WorkspaceProvenance />
      <OpenArtifact kind="Scenario" path="/procurement/scenarios" />
      <section
        aria-label="Supplier and product selection"
        className="space-y-4"
      >
        <h2 className="text-sm font-semibold">Current supplier product</h2>
        <form
          className="grid items-end gap-3 sm:grid-cols-2 xl:grid-cols-4"
          onSubmit={(event) => {
            event.preventDefault()
            const values = new FormData(event.currentTarget)
            setFilters({
              ...filters,
              supplier_search: String(
                values.get('supplier_search') || '',
              ).trim(),
              product_search: String(values.get('product_search') || '').trim(),
              supplier_offset: 0,
              product_offset: 0,
            })
          }}
        >
          <Field label="Search suppliers">
            <Input name="supplier_search" maxLength={200} />
          </Field>
          <Field label="Search products">
            <Input name="product_search" maxLength={200} />
          </Field>
          <Button variant="outline" size="sm" className="justify-self-start">
            <Search />
            Search catalog
          </Button>
        </form>
        <div className="grid min-w-0 gap-6 lg:grid-cols-2">
          <section aria-label="Supplier selector">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-medium">Suppliers</h3>
              <QueryRefresh query={suppliers} label="supplier selector" />
            </div>
            <QueryState query={suppliers}>
              {(data) => (
                <>
                  <Field label="Supplier filter">
                    <NativeSelect
                      className="w-full"
                      value={filters.supplier}
                      onChange={(e) =>
                        setFilters({
                          ...filters,
                          supplier: e.target.value,
                          product_offset: 0,
                        })
                      }
                    >
                      <NativeSelectOption value="">
                        All suppliers
                      </NativeSelectOption>
                      {filters.supplier &&
                        !data.items.some((v) => v.id === filters.supplier) && (
                          <NativeSelectOption value={filters.supplier}>
                            Selected supplier
                          </NativeSelectOption>
                        )}
                      {data.items.map((v) => (
                        <NativeSelectOption key={v.id} value={v.id}>
                          {displayText(v.name)}
                        </NativeSelectOption>
                      ))}
                    </NativeSelect>
                  </Field>
                  <PageControls
                    {...data}
                    count={data.items.length}
                    onChange={(offset) =>
                      setFilters({ ...filters, supplier_offset: offset })
                    }
                  />
                </>
              )}
            </QueryState>
          </section>
          <section aria-label="Product selector">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-medium">Products</h3>
              <QueryRefresh query={products} label="product selector" />
            </div>
            <QueryState query={products}>
              {(data) => (
                <>
                  <Field
                    label="Current product"
                    error={errors.current_product_id?.message}
                  >
                    <NativeSelect
                      className="w-full"
                      value={selected?.id || ''}
                      disabled={create.isPending || products.isError}
                      onChange={(e) => {
                        const item =
                          data.items.find((v) => v.id === e.target.value) ??
                          null
                        setSelected(item)
                        form.setValue('current_product_id', item?.id || '', {
                          shouldValidate: true,
                        })
                      }}
                    >
                      <NativeSelectOption value="">
                        Select a product
                      </NativeSelectOption>
                      {selected &&
                        !data.items.some((v) => v.id === selected.id) && (
                          <NativeSelectOption value={selected.id}>
                            {displayText(selected.name)}
                          </NativeSelectOption>
                        )}
                      {data.items.map((v) => (
                        <NativeSelectOption key={v.id} value={v.id}>
                          {displayText(v.name)} / {displayText(v.supplier_name)}
                        </NativeSelectOption>
                      ))}
                    </NativeSelect>
                  </Field>
                  {!data.items.length && (
                    <EmptyState
                      title="No products found"
                      detail="No active products match the catalog filters."
                    />
                  )}
                  <PageControls
                    {...data}
                    count={data.items.length}
                    onChange={(offset) =>
                      setFilters({ ...filters, product_offset: offset })
                    }
                  />
                </>
              )}
            </QueryState>
          </section>
        </div>
        {selected && (
          <p className="text-sm wrap-anywhere">
            {displayText(selected.name)} / {selected.currency}{' '}
            {selected.unit_cost} / {selected.pcf_kgco2e_per_unit}{' '}
            {selected.pcf_unit}
          </p>
        )}
      </section>
      <section
        aria-label="Measurement selector"
        className="space-y-3 border-t pt-5"
      >
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Verified measurement</h2>
          <QueryRefresh query={measurements} label="measurement selector" />
        </div>
        <QueryState query={measurements}>
          {(data) => (
            <>
              <Field
                label="Purchased-material measurement"
                error={errors.carbon_measurement_id?.message}
              >
                <NativeSelect
                  className="w-full"
                  value={selectedMeasurement?.id || ''}
                  disabled={create.isPending || measurements.isError}
                  onChange={(e) => {
                    const item = data.items.find((v) => v.id === e.target.value)
                    setSelectedMeasurement(
                      item
                        ? {
                            id: item.id,
                            label: `${humanize(item.metric_key)} / ${item.value_kgco2e} ${item.unit}`,
                          }
                        : null,
                    )
                    form.setValue('carbon_measurement_id', item?.id || '', {
                      shouldValidate: true,
                    })
                  }}
                >
                  <NativeSelectOption value="">
                    Select a verified measurement
                  </NativeSelectOption>
                  {selectedMeasurement &&
                    !data.items.some(
                      (v) => v.id === selectedMeasurement.id,
                    ) && (
                      <NativeSelectOption value={selectedMeasurement.id}>
                        {displayText(selectedMeasurement.label)}
                      </NativeSelectOption>
                    )}
                  {data.items.map((v) => (
                    <NativeSelectOption key={v.id} value={v.id}>
                      {humanize(v.metric_key)} / {v.value_kgco2e} {v.unit}
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
              </Field>
              {!data.items.length && (
                <EmptyState
                  title="No verified measurements"
                  detail="A verified purchased-material measurement is required for this context."
                />
              )}
              <PageControls
                {...data}
                count={data.items.length}
                onChange={setMeasurementOffset}
              />
            </>
          )}
        </QueryState>
      </section>
      <form
        onSubmit={form.handleSubmit((values) => create.mutate(values))}
        className="space-y-6"
      >
        <fieldset
          disabled={create.isPending}
          className="space-y-5 border-t pt-5"
        >
          <legend className="text-sm font-semibold">
            Scenario requirements
          </legend>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {textField('quantity', 'Quantity', 'Required quantity')}
            {textField('quantity_unit', 'Quantity unit', 'Required unit')}
            <Field
              label="Scoring method"
              error={errors.method_definition_id?.message}
            >
              <RecordSelect
                kind="methods"
                {...form.register('method_definition_id')}
                value={watched.method_definition_id ?? ''}
                required
              />
            </Field>
            <ActorField
              registration={form.register('requested_by')}
              value={watched.requested_by ?? ''}
              error={errors.requested_by?.message}
            />
            {textField(
              'current_unit_cost',
              'Current unit cost override (optional)',
              'Use product cost',
            )}
            {textField(
              'currency',
              'Currency override (optional)',
              'Use product currency',
            )}
          </div>
          <h2 className="text-sm font-semibold">Hard constraints</h2>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {textField('max_cost_increase_pct', 'Maximum cost increase (%)')}
            {textField('max_lead_time_days', 'Maximum lead time (days)')}
            {textField(
              'minimum_circularity_score',
              'Minimum circularity score',
            )}
            {textField(
              'allowed_material_codes',
              'Allowed material codes (comma separated)',
              'Optional material restriction',
            )}
            {textField(
              'excluded_risk_levels',
              'Excluded risk levels (comma separated)',
              'Optional risk restriction',
            )}
            {textField(
              'approval_expires_at',
              'Approval expiry (optional, local time)',
              undefined,
              'datetime-local',
            )}
          </div>
        </fieldset>
        <CommandError error={create.error} />
        <Button
          type="submit"
          disabled={
            create.isPending || products.isError || measurements.isError
          }
        >
          <Plus />
          {create.isPending
            ? 'Creating scenario...'
            : 'Create and assess scenario'}
        </Button>
      </form>
    </div>
  )
}
export default function ScenarioCreatePage() {
  const scope = useOutletContext<WorkspaceScope>()
  return (
    <CreateScenario
      key={`${scope.company_id}:${scope.site_id}:${scope.reporting_period_id}}`}
    />
  )
}

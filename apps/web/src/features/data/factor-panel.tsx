import { zodResolver } from '@hookform/resolvers/zod'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Search } from 'lucide-react'
import { useState } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { Link, useOutletContext, useSearchParams } from 'react-router-dom'
import { z } from 'zod'

import {
  EmptyState,
  PageControls,
  QueryRefresh,
  QueryState,
} from '@/components/query-state'
import { RecordSelect } from '@/components/record-select'
import { displayText, humanize } from '@/lib/presentation'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useWorkspaceActor } from '@/services/use-workspace-actor'
import type { WorkspaceScope } from '@/lib/workspace'
import { apiRequest, retryApiQuery } from '@/services/api'
import {
  ActorField,
  CommandError,
  Field,
  MetricField,
  formGrid,
  sectionClass,
} from './intake-ui'
import { useCommand } from './commands'
import {
  factorFormSchema,
  factorListSchema,
  factorRegistrationSchema,
  type FactorForm,
} from './schemas'

function FactorRegistration() {
  const scope = useOutletContext<WorkspaceScope>()
  const actor = useWorkspaceActor()
  const client = useQueryClient()
  const [params] = useSearchParams()
  const command = useCommand<z.infer<typeof factorRegistrationSchema>>()
  const form = useForm<FactorForm>({
    resolver: zodResolver(factorFormSchema),
    defaultValues: {
      actor_id: actor.actorId,
      metric_definition_id: '',
      evidence_item_id: params.get('evidence') ?? '',
      factor_code: '',
      version: '',
      name: '',
      material_code: '',
      product_code: '',
      geography: '',
      factor_value: '',
      effective_from: '',
      effective_to: '',
      source_quality: '',
      factor_specificity: '',
      factor_recency: '',
    },
  })
  const watched = useWatch({ control: form.control })
  const fields = [
    ['factor_code', 'Factor code', 100],
    ['version', 'Version', 50],
    ['name', 'Factor name', 255],
    ['material_code', 'Material code', 100],
    ['product_code', 'Product code (optional)', 100],
    ['geography', 'Geography', 100],
    ['factor_value', 'Factor value (kgCO2e/kg)', 25],
    ['source_quality', 'Source quality (0 to 1)', 7],
    ['factor_specificity', 'Factor specificity (0 to 1)', 7],
    ['factor_recency', 'Factor recency (0 to 1)', 7],
  ] as const
  return (
    <section className={sectionClass} aria-label="Register emission factor">
      <h2 className="text-sm font-semibold">
        Register purchased-material factor
      </h2>
      <form
        className="space-y-4"
        onSubmit={form.handleSubmit((values) =>
          command.run(
            () =>
              apiRequest(
                '/emission-factors',
                factorRegistrationSchema.refine(
                  (data) =>
                    data.factor.company_id === scope.company_id &&
                    data.factor.metric_definition_id ===
                      values.metric_definition_id &&
                    data.factor.evidence_item_id === values.evidence_item_id,
                ),
                {
                  signal: new AbortController().signal,
                  body: {
                    ...values,
                    company_id: scope.company_id,
                    product_code: values.product_code || null,
                    effective_to: values.effective_to || null,
                    numerator_unit: 'kgCO2e',
                    denominator_unit: 'kg',
                  },
                },
              ),
            () => {
              void client.invalidateQueries({ queryKey: ['intake-factors'] })
            },
          ),
        )}
      >
        <fieldset disabled={command.pending} className="space-y-4">
          <div className={formGrid}>
            <ActorField
              registration={form.register('actor_id')}
              value={watched.actor_id ?? ''}
              error={form.formState.errors.actor_id?.message}
            />
            <MetricField
              materialOnly
              value={watched.metric_definition_id ?? ''}
              registration={form.register('metric_definition_id')}
              error={form.formState.errors.metric_definition_id?.message}
            />
            <Field
              label="Supporting evidence"
              error={form.formState.errors.evidence_item_id?.message}
            >
              <RecordSelect
                kind="evidence"
                {...form.register('evidence_item_id')}
                value={watched.evidence_item_id ?? ''}
                required
              />
            </Field>
            {fields.map(([name, label, max]) => (
              <Field
                key={name}
                label={label}
                error={form.formState.errors[name]?.message}
              >
                <Input
                  {...form.register(name)}
                  required={name !== 'product_code'}
                  maxLength={max}
                  inputMode={
                    [
                      'factor_value',
                      'source_quality',
                      'factor_specificity',
                      'factor_recency',
                    ].includes(name)
                      ? 'decimal'
                      : 'text'
                  }
                  aria-invalid={!!form.formState.errors[name]}
                />
              </Field>
            ))}
            <Field
              label="Effective from"
              error={form.formState.errors.effective_from?.message}
            >
              <Input
                {...form.register('effective_from')}
                type="date"
                required
              />
            </Field>
            <Field
              label="Effective to (optional)"
              error={form.formState.errors.effective_to?.message}
            >
              <Input {...form.register('effective_to')} type="date" />
            </Field>
          </div>
          <Button type="submit" disabled={command.pending}>
            <Plus />
            {command.pending ? 'Registering...' : 'Register factor'}
          </Button>
        </fieldset>
        <CommandError error={command.error} />
        {command.data && (
          <div role="status" className="space-y-2 text-sm">
            <p>
              {command.data.replayed
                ? 'Existing factor verified'
                : 'Factor registered'}
              : {displayText(command.data.factor.name)}
            </p>
            <Link className="mr-4 text-emerald-700 underline" to="/measurement">
              Continue to measurement
            </Link>
            <Link
              className="text-emerald-700 underline dark:text-emerald-400"
              to={`/ledger?event=${command.data.ledger_event_id}`}
            >
              View registration ledger event
            </Link>
          </div>
        )}
      </form>
    </section>
  )
}

const filtersSchema = z.object({
  material: z.string().trim().max(100),
  product: z.string().trim().max(100),
  active: z.enum(['all', 'true', 'false']),
  limit: z.enum(['10', '25', '50', '100']),
})
export function FactorPanel() {
  const scope = useOutletContext<WorkspaceScope>()
  const [filters, setFilters] = useState<z.infer<typeof filtersSchema>>({
    material: '',
    product: '',
    active: 'all',
    limit: '25',
  })
  const [offset, setOffset] = useState(0)
  const form = useForm<z.infer<typeof filtersSchema>>({
    resolver: zodResolver(filtersSchema),
    defaultValues: filters,
  })
  const params = {
    company_id: scope.company_id,
    material_code: filters.material || undefined,
    product_code: filters.product || undefined,
    active: filters.active === 'all' ? undefined : filters.active === 'true',
    limit: Number(filters.limit),
    offset,
  }
  const catalog = useQuery({
    queryKey: ['intake-factors', params],
    queryFn: ({ signal }) =>
      apiRequest(
        '/emission-factors',
        factorListSchema.refine(
          (data) =>
            data.limit === params.limit &&
            data.offset === offset &&
            data.items.length <= data.limit &&
            data.items.every(
              (item) =>
                item.company_id === scope.company_id &&
                (!params.material_code ||
                  item.material_code === params.material_code) &&
                (!params.product_code ||
                  item.product_code === params.product_code),
            ),
        ),
        { signal, params },
      ),
    staleTime: 30_000,
    retry: retryApiQuery,
  })
  return (
    <>
      <section aria-label="Emission factor catalog" className={sectionClass}>
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Emission factor catalog</h2>
          <QueryRefresh query={catalog} label="emission factors" />
        </div>
        <form
          onSubmit={form.handleSubmit((values) => {
            setFilters(values)
            setOffset(0)
          })}
          className="flex flex-wrap items-start gap-3"
        >
          <Field
            label="Filter material code"
            error={form.formState.errors.material?.message}
          >
            <Input {...form.register('material')} maxLength={100} />
          </Field>
          <Field
            label="Filter product code"
            error={form.formState.errors.product?.message}
          >
            <Input {...form.register('product')} maxLength={100} />
          </Field>
          <Field label="Factor status">
            <NativeSelect {...form.register('active')}>
              <NativeSelectOption value="all">All statuses</NativeSelectOption>
              <NativeSelectOption value="true">Active</NativeSelectOption>
              <NativeSelectOption value="false">Not active</NativeSelectOption>
            </NativeSelect>
          </Field>
          <Field label="Rows per page">
            <NativeSelect {...form.register('limit')}>
              {['10', '25', '50', '100'].map((value) => (
                <NativeSelectOption key={value}>{value}</NativeSelectOption>
              ))}
            </NativeSelect>
          </Field>
          <Button type="submit" className="mt-6" variant="outline">
            <Search />
            Apply filters
          </Button>
        </form>
        <QueryState query={catalog}>
          {(data) => (
            <>
              {data.items.length ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Factor</TableHead>
                      <TableHead>Scope</TableHead>
                      <TableHead>Value</TableHead>
                      <TableHead>Effective</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Evidence</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.items.map((item) => (
                      <TableRow key={item.id}>
                        <TableCell className="min-w-48 whitespace-normal">
                          <p className="font-medium">
                            {displayText(item.name)}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            Version {displayText(item.version)}
                          </p>
                        </TableCell>
                        <TableCell>
                          {humanize(item.material_code ?? 'Unscoped')}
                          <p className="text-xs text-muted-foreground">
                            {displayText(item.product_code ?? 'All products')} /{' '}
                            {displayText(item.geography)}
                          </p>
                        </TableCell>
                        <TableCell className="font-mono">
                          {item.factor_value}
                          <p className="text-xs text-muted-foreground">
                            {item.numerator_unit}/{item.denominator_unit}
                          </p>
                        </TableCell>
                        <TableCell>
                          {item.effective_from}
                          <p className="text-xs text-muted-foreground">
                            {item.effective_to ?? 'Open ended'}
                          </p>
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline">
                            {humanize(item.status)}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <details>
                            <summary className="cursor-pointer text-xs">
                              Evidence and quality
                            </summary>
                            <dl className="mt-2 space-y-2 text-xs">
                              <div>
                                <dt>Supporting evidence</dt>
                                <dd className="font-mono">
                                  {item.evidence_item_id ? 'Linked' : 'Missing'}
                                </dd>
                              </div>
                              <div>
                                <dt>Source quality</dt>
                                <dd>{item.source_quality}</dd>
                              </div>
                              <div>
                                <dt>Specificity</dt>
                                <dd>{item.factor_specificity}</dd>
                              </div>
                              <div>
                                <dt>Recency</dt>
                                <dd>{item.factor_recency}</dd>
                              </div>
                            </dl>
                          </details>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <EmptyState
                  title="No emission factors found"
                  detail="No catalog records match these filters."
                />
              )}
              <PageControls
                {...data}
                count={data.items.length}
                onChange={setOffset}
              />
            </>
          )}
        </QueryState>
      </section>
      <details className="border-t py-4">
        <summary className="cursor-pointer text-sm font-medium">
          Register a factor
        </summary>
        <FactorRegistration />
      </details>
    </>
  )
}

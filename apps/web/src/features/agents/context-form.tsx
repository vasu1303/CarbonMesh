import { useQuery } from '@tanstack/react-query'
import { ArrowRight } from 'lucide-react'
import { useId, useState } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { z } from 'zod'
import { EmptyState, PageControls, QueryState } from '@/components/query-state'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { useWorkspaceActor } from '@/features/auth/use-workspace-actor'
import type { WorkspaceScope } from '@/lib/workspace'
import { ApiError } from '@/services/api'
import { selectorOptions, type SelectorKind } from './queries'
import { agentContextSchema, promptSchema, type AgentContext } from './schemas'

type Values = Record<string, string>
const idFields = [
  'carbon_measurement_id',
  'current_product_id',
  'method_definition_id',
  'standard_id',
  'disclosure_draft_id',
  'procurement_scenario_id',
  'flexible_load_id',
  'dispatch_scenario_id',
  'forecast_id',
  'policy_definition_id',
] as const
const listFields = [
  'activity_record_ids',
  'requirement_ids',
  'evidence_item_ids',
  'metric_keys',
  'material_scope',
  'supplier_product_ids',
] as const
const costFields = [
  'max_cost_increase_pct',
  'max_lead_time_days',
  'minimum_circularity_score',
] as const
const dispatchFields = [
  'window_start',
  'window_end',
  'duration_minutes',
  'max_delay_minutes',
  'maximum_power_kw',
  'blackout_constraint_ids',
] as const
const freshFields = [
  'history_start',
  'history_end',
  'history_fixture_variant',
  'procurement_quantity',
  'procurement_method_id',
  'dispatch_method_id',
  'dispatch_baseline_start',
] as const
const split = (value = '') =>
  value
    .split(/[\n,]/)
    .map((v) => v.trim())
    .filter(Boolean)
const optional = (v?: string) => v?.trim() || undefined
const integer = (v?: string) =>
  optional(v) === undefined ? undefined : Number(v)

function defaults(
  scope: WorkspaceScope,
  actor: string,
  context?: AgentContext,
  workflow = 'measurement',
): Values {
  const values: Values = {
    actor_id: actor,
    company_id: scope.company_id,
    site_id: context?.site_id ?? scope.site_id,
    reporting_period_id:
      context?.reporting_period_id ?? scope.reporting_period_id,
    mode: context?.fresh_inputs ? 'fresh' : 'prepared',
    module: workflow === 'cross_module' ? 'four_module' : workflow,
    grid_source_mode: context?.grid_source_mode ?? 'live',
    measurement_paths: 'scope2',
    query: '',
  }
  for (const name of idFields) values[name] = context?.[name] ?? ''
  for (const name of listFields)
    values[name] = context?.[name]?.join(', ') ?? ''
  for (const name of costFields)
    values[`cost_${name}`] = String(context?.constraints[name] ?? '')
  for (const name of dispatchFields) {
    const value = context?.dispatch_constraints[name]
    values[`dispatch_${name}`] = Array.isArray(value)
      ? value.join(', ')
      : String(value ?? '')
  }
  for (const name of freshFields)
    values[`fresh_${name}`] = String(context?.fresh_inputs?.[name] ?? '')
  const measurements = context?.fresh_inputs?.measurements ?? []
  if (measurements.length)
    values.measurement_paths =
      measurements.length === 2
        ? 'both'
        : measurements[0].output_metric_key ===
            'emissions.scope2.location_based'
          ? 'scope2'
          : 'material'
  for (const item of measurements) {
    const prefix =
      item.output_metric_key === 'emissions.scope2.location_based'
        ? 'scope2'
        : 'material'
    for (const name of [
      'material_code',
      'geography',
      'grid_zone',
      'grid_method_version',
    ] as const)
      values[`${prefix}_${name}`] = item[name] ?? ''
    values[`${prefix}_activity_record_ids`] =
      item.activity_record_ids?.join(', ') ?? ''
  }
  return values
}

function buildContext(v: Values, company: string, actor: string): AgentContext {
  const context: Record<string, unknown> = {
    company_id: company,
    actor_id: actor || v.actor_id,
    site_id: optional(v.site_id),
    reporting_period_id: optional(v.reporting_period_id),
    grid_source_mode: v.grid_source_mode,
    constraints: {
      max_cost_increase_pct: optional(v.cost_max_cost_increase_pct),
      max_lead_time_days: integer(v.cost_max_lead_time_days),
      minimum_circularity_score: optional(v.cost_minimum_circularity_score),
    },
    dispatch_constraints: {
      window_start: optional(v.dispatch_window_start),
      window_end: optional(v.dispatch_window_end),
      duration_minutes: integer(v.dispatch_duration_minutes),
      max_delay_minutes: integer(v.dispatch_max_delay_minutes),
      maximum_power_kw: optional(v.dispatch_maximum_power_kw),
      blackout_constraint_ids: split(v.dispatch_blackout_constraint_ids),
    },
  }
  for (const name of idFields) context[name] = optional(v[name])
  for (const name of listFields) context[name] = split(v[name])
  if (v.mode === 'fresh') {
    if (v.module === 'assurance' || v.module === 'four_module')
      delete context.disclosure_draft_id
    if (v.module === 'procurement' || v.module === 'four_module')
      delete context.procurement_scenario_id
    if (v.module === 'dispatch' || v.module === 'four_module')
      delete context.dispatch_scenario_id
    const measurements =
      v.module === 'measurement' || v.module === 'four_module'
        ? ['scope2', 'material']
            .filter(
              (kind) =>
                v.measurement_paths === kind || v.measurement_paths === 'both',
            )
            .map((kind) => ({
              material_code: v[`${kind}_material_code`],
              output_metric_key:
                kind === 'scope2'
                  ? 'emissions.scope2.location_based'
                  : 'emissions.scope3.category1',
              activity_record_ids: split(v[`${kind}_activity_record_ids`]),
              geography: optional(v[`${kind}_geography`]),
              grid_zone: optional(v[`${kind}_grid_zone`]),
              grid_method_version: optional(v[`${kind}_grid_method_version`]),
            }))
        : []
    const fresh: Record<string, unknown> = {
      measurements,
      procurement_quantity_unit: 'kg',
    }
    for (const name of freshFields) fresh[name] = optional(v[`fresh_${name}`])
    context.fresh_inputs = fresh
    if (measurements.length) {
      // Fresh calculations resolve methods from output metrics, not prepared targets.
      delete context.carbon_measurement_id
      delete context.method_definition_id
      delete context.activity_record_ids
    }
  }
  return agentContextSchema.parse(context)
}

export function CommandError({ error }: { error: Error | null }) {
  if (!error) return null
  return (
    <div
      role="alert"
      className="space-y-1 border-l-2 border-amber-500 pl-3 text-sm"
    >
      <p>{error.message}</p>
      {error instanceof ApiError && (
        <>
          <p className="text-xs text-muted-foreground">
            {error.code}
            {error.terminalState ? ` / ${error.terminalState}` : ''}
          </p>
          {error.fieldDetails && (
            <ul className="text-xs">
              {Object.entries(error.fieldDetails)
                .filter(([, value]) => typeof value === 'string')
                .map(([field, value]) => (
                  <li key={field}>
                    {field}: {String(value)}
                  </li>
                ))}
            </ul>
          )}
          {error.traceId && (
            <p className="break-all font-mono text-xs">
              Trace: {error.traceId}
            </p>
          )}
        </>
      )}
    </div>
  )
}

function RecordSelector({
  kind,
  scope,
  id,
  value,
  onChange,
}: {
  kind: SelectorKind
  scope: WorkspaceScope
  id: string
  value: string
  onChange: (value: string) => void
}) {
  const [offset, setOffset] = useState(0)
  const query = useQuery(selectorOptions(kind, scope, offset))
  return (
    <div className="min-w-0 space-y-2">
      <QueryState query={query}>
        {(data) => (
          <>
            {data.items.length ? (
              <NativeSelect
                id={`${id}-select`}
                aria-label={`Select ${kind}`}
                className="w-full"
                value={data.items.some((i) => i.id === value) ? value : ''}
                onChange={(e) => onChange(e.target.value)}
              >
                <NativeSelectOption value="">
                  Choose a recorded item
                </NativeSelectOption>
                {data.items.map((item) => (
                  <NativeSelectOption key={item.id} value={item.id}>
                    {item.label}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            ) : (
              <EmptyState
                title="No matching records"
                detail="A known record UUID can be entered below."
              />
            )}
            {data.total > data.limit && (
              <PageControls
                {...data}
                count={data.items.length}
                onChange={setOffset}
              />
            )}
          </>
        )}
      </QueryState>
      <Input
        id={id}
        aria-label={`Known ${kind} UUID`}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Known record UUID"
      />
    </div>
  )
}

export function AgentContextForm({
  scope,
  initialContext,
  workflow,
  promptRequired = true,
  pending,
  error,
  disabled,
  onSubmit,
  submitLabel = 'Start run',
  missingFields = [],
}: {
  scope: WorkspaceScope
  initialContext?: AgentContext
  workflow?: string
  promptRequired?: boolean
  pending: boolean
  error: Error | null
  disabled?: boolean
  onSubmit: (
    context: AgentContext,
    query?: string,
    previousRunId?: string,
  ) => void
  submitLabel?: string
  missingFields?: string[]
}) {
  const actor = useWorkspaceActor()
  const prefix = useId()
  const { register, handleSubmit, control, setValue } = useForm<Values>({
    defaultValues: defaults(scope, actor.actorId, initialContext, workflow),
  })
  const formValues = useWatch({ control })
  const valueOf = (name: string) => formValues[name] ?? ''
  const [issues, setIssues] = useState<string[]>([])
  const module = valueOf('module'),
    fresh = valueOf('mode') === 'fresh'
  const includes = (name: string) => module === name || module === 'four_module'
  const measurements = valueOf('measurement_paths')
  const field = (
    name: string,
    label: string,
    options: {
      required?: boolean
      help?: string
      selector?: SelectorKind
      numeric?: boolean
    } = {},
  ) => (
    <div key={name} className="min-w-0 space-y-1.5">
      <Label htmlFor={`${prefix}-${name}`} className="text-sm font-medium">
        {label}
        {options.required ? ' (required)' : ''}
      </Label>
      {options.selector ? (
        <RecordSelector
          kind={options.selector}
          scope={scope}
          id={`${prefix}-${name}`}
          value={valueOf(name)}
          onChange={(v) => setValue(name, v)}
        />
      ) : (
        <Input
          id={`${prefix}-${name}`}
          {...register(name)}
          inputMode={options.numeric ? 'decimal' : undefined}
          required={options.required}
          maxLength={name.includes('_ids') ? 120000 : 4000}
        />
      )}
      {options.help && (
        <p className="text-xs text-muted-foreground">{options.help}</p>
      )}
    </div>
  )
  const select = (name: string, label: string, choices: [string, string][]) => (
    <div className="space-y-1.5">
      <Label htmlFor={`${prefix}-${name}`} className="text-sm font-medium">
        {label}
      </Label>
      <NativeSelect
        id={`${prefix}-${name}`}
        {...register(name)}
        className="w-full"
      >
        {choices.map(([value, title]) => (
          <NativeSelectOption key={value} value={value}>
            {title}
          </NativeSelectOption>
        ))}
      </NativeSelect>
    </div>
  )
  const submit = handleSubmit((values) => {
    try {
      const context = buildContext(values, scope.company_id, actor.actorId)
      const query = promptRequired
        ? promptSchema.parse(values.query)
        : undefined
      const previous = optional(values.previous_run_id)
      if (previous) z.uuid().parse(previous)
      const missing: string[] = []
      if (
        includes('measurement') &&
        !fresh &&
        !context.carbon_measurement_id &&
        !context.material_scope?.length &&
        !context.activity_record_ids?.length
      )
        missing.push(
          'Provide a measurement, material code, or activity record UUIDs.',
        )
      if (includes('assurance') && !context.standard_id)
        missing.push('Select a standard or enter its UUID.')
      if (includes('procurement')) {
        if (!context.material_scope?.length)
          missing.push('Material codes are required for procurement.')
        if (fresh && !context.current_product_id)
          missing.push('Select the current product or enter its UUID.')
      }
      if (includes('dispatch') && !context.flexible_load_id)
        missing.push('Select a flexible load or enter its UUID.')
      for (const source of context.fresh_inputs?.measurements ?? []) {
        if (
          source.output_metric_key === 'emissions.scope3.category1' &&
          !context.material_scope?.includes(source.material_code)
        )
          missing.push(
            'Purchased-material code must be included in the material scope.',
          )
        if (
          context.metric_keys?.length &&
          !context.metric_keys.includes(source.output_metric_key)
        )
          missing.push(
            'Each calculation path must be included in the explicit metric scope.',
          )
      }
      if (missing.length) {
        setIssues(missing)
        return
      }
      setIssues([])
      onSubmit(context, query, previous)
    } catch (issue) {
      setIssues(
        issue instanceof z.ZodError
          ? issue.issues.map(
              (item) => `${item.path.join('.') || 'Request'}: ${item.message}`,
            )
          : ['Check the request fields.'],
      )
    }
  })
  return (
    <form
      onSubmit={submit}
      className="space-y-6"
      aria-label={initialContext ? 'Clarification form' : 'Agent request'}
    >
      <fieldset disabled={pending || disabled} className="min-w-0 space-y-6">
        {missingFields.length > 0 && (
          <div className="text-sm">
            <p className="font-medium">Required by this run</p>
            <ul className="mt-2 list-inside list-disc break-words text-muted-foreground">
              {missingFields.map((name) => (
                <li key={name}>{name}</li>
              ))}
            </ul>
          </div>
        )}
        {promptRequired && (
          <div className="space-y-1.5">
            <Label className="text-sm font-medium" htmlFor={`${prefix}-query`}>
              {initialContext ? 'Clarified request' : 'Request'}
            </Label>
            <Textarea
              id={`${prefix}-query`}
              {...register('query')}
              required
              minLength={3}
              maxLength={4000}
              rows={4}
              className="min-h-28"
            />
          </div>
        )}
        <div className="grid min-w-0 gap-4 sm:grid-cols-2">
          {actor.actorId ? (
            <div className="space-y-1 text-sm">
              <p className="font-medium">
                {actor.authenticated ? 'Signed-in actor' : 'Configured actor'}
              </p>
              <p className="break-all font-mono text-xs">{actor.actorId}</p>
              {actor.role && (
                <p className="text-muted-foreground">
                  {actor.role.replaceAll('_', ' ')}
                </p>
              )}
            </div>
          ) : (
            field('actor_id', 'Actor UUID', { required: true })
          )}
          {select('module', 'Input sections', [
            ['measurement', 'Measurement'],
            ['assurance', 'Assurance'],
            ['procurement', 'Procurement'],
            ['dispatch', 'Dispatch'],
            ['four_module', 'All modules'],
          ])}
          {select('mode', 'Source inputs', [
            ['prepared', 'Prepared records'],
            ['fresh', 'Fresh calculations'],
          ])}
          {select('grid_source_mode', 'Grid source', [
            ['live', 'Live provider'],
            ['fixture', 'Synthetic fixture (explicit opt-in)'],
          ])}
        </div>
        {valueOf('grid_source_mode') === 'fixture' && (
          <p className="text-sm text-amber-700 dark:text-amber-400">
            Synthetic grid fixtures selected.
          </p>
        )}
        <details className="border-y py-3">
          <summary className="cursor-pointer text-sm font-medium">
            Workspace identifiers
          </summary>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <p className="break-all text-xs sm:col-span-2">
              Company: {scope.company_id}
            </p>
            {initialContext ? (
              <>
                {field('site_id', 'Site UUID', { required: true })}
                {field('reporting_period_id', 'Reporting period UUID', {
                  required: true,
                })}
              </>
            ) : (
              <>
                <p className="break-all text-xs">Site: {scope.site_id}</p>
                <p className="break-all text-xs">
                  Reporting period: {scope.reporting_period_id}
                </p>
              </>
            )}
          </div>
        </details>
        {includes('measurement') && (
          <section aria-label="Measurement inputs" className="space-y-4">
            <h3 className="text-base font-semibold">Measurement</h3>
            {!fresh ? (
              <div className="grid gap-4 sm:grid-cols-2">
                {field('carbon_measurement_id', 'Verified measurement', {
                  selector: 'measurements',
                })}
                {field('material_scope', 'Material codes', {
                  help: 'Comma-separated. Supply a measurement, material scope, or activity IDs.',
                })}
                {field('activity_record_ids', 'Activity record UUIDs', {
                  help: 'Comma-separated existing activity records.',
                })}
              </div>
            ) : (
              <>
                {select('measurement_paths', 'Calculation paths', [
                  ['scope2', 'Hourly electricity'],
                  ['material', 'Purchased material'],
                  ['both', 'Electricity and material'],
                ])}
                {['scope2', 'material']
                  .filter(
                    (kind) => measurements === kind || measurements === 'both',
                  )
                  .map((kind) => (
                    <div
                      key={kind}
                      className="grid gap-4 border-l-2 border-emerald-600 pl-4 sm:grid-cols-2"
                    >
                      <h4 className="text-sm font-medium sm:col-span-2">
                        {kind === 'scope2'
                          ? 'Hourly electricity'
                          : 'Purchased material'}
                      </h4>
                      {field(`${kind}_material_code`, 'Material code', {
                        required: true,
                      })}
                      {field(
                        `${kind}_activity_record_ids`,
                        'Source activity UUIDs',
                        {
                          help: 'Comma-separated; blank uses matching scoped activity.',
                        },
                      )}
                      {field(`${kind}_geography`, 'Geography')}
                      {kind === 'scope2' && (
                        <>
                          {field(`${kind}_grid_zone`, 'Grid zone')}
                          {field(
                            `${kind}_grid_method_version`,
                            'Grid method version',
                          )}
                        </>
                      )}
                    </div>
                  ))}
                <p className="text-xs text-muted-foreground">
                  Fresh calculations use metric-selected methods. Prepared
                  measurement and method selectors are excluded.
                </p>
                {(measurements === 'material' || measurements === 'both') &&
                  !includes('procurement') &&
                  field('material_scope', 'Material codes in scope', {
                    required: true,
                    help: 'Must include the purchased-material code above.',
                  })}
                <details className="border-t pt-3">
                  <summary className="cursor-pointer text-sm font-medium">
                    Historical grid range
                  </summary>
                  <div className="mt-4 grid gap-4 sm:grid-cols-2">
                    {field(
                      'fresh_history_start',
                      'History start (UTC offset)',
                      { help: 'ISO timestamp with Z or an explicit offset.' },
                    )}
                    {field('fresh_history_end', 'History end (UTC offset)')}
                    {valueOf('grid_source_mode') === 'fixture' &&
                      select(
                        'fresh_history_fixture_variant',
                        'History fixture',
                        [
                          ['', 'Provider default'],
                          ['quality_cases_v1', 'Quality cases'],
                          ['complete_q3_v1', 'Complete quarter'],
                        ],
                      )}
                  </div>
                </details>
              </>
            )}
          </section>
        )}
        {includes('assurance') && (
          <section
            aria-label="Assurance inputs"
            className="space-y-4 border-t pt-5"
          >
            <h3 className="text-base font-semibold">Assurance</h3>
            <div className="grid gap-4 sm:grid-cols-2">
              {field('standard_id', 'Standard (required)', {
                selector: 'standards',
              })}
              {!fresh &&
                field('disclosure_draft_id', 'Disclosure draft UUID', {
                  required: true,
                })}
              {field('requirement_ids', 'Requirement UUIDs', {
                help: 'Optional comma-separated subset of the standard.',
              })}
              {field('evidence_item_ids', 'Evidence UUIDs', {
                help: 'Optional comma-separated evidence scope.',
              })}
            </div>
          </section>
        )}
        {includes('procurement') && (
          <section
            aria-label="Procurement inputs"
            className="space-y-4 border-t pt-5"
          >
            <h3 className="text-base font-semibold">Procurement</h3>
            <div className="grid gap-4 sm:grid-cols-2">
              {(fresh || !includes('measurement')) &&
                field('material_scope', 'Material codes', {
                  required: true,
                  help: 'Comma-separated material scope.',
                })}
              {fresh ? (
                <>
                  {field('current_product_id', 'Current product (required)', {
                    selector: 'products',
                  })}
                  {field('fresh_procurement_quantity', 'Quantity (kg)', {
                    required: true,
                    numeric: true,
                  })}
                  {field(
                    'fresh_procurement_method_id',
                    'Procurement method UUID',
                    { required: true },
                  )}
                </>
              ) : (
                field('procurement_scenario_id', 'Procurement scenario UUID', {
                  required: true,
                })
              )}
              {field(
                'cost_max_cost_increase_pct',
                'Maximum cost increase (%)',
                { required: true, numeric: true },
              )}
              {field('cost_max_lead_time_days', 'Maximum lead time (days)', {
                required: true,
                numeric: true,
              })}
              {field(
                'cost_minimum_circularity_score',
                'Minimum circularity score',
                { required: true, numeric: true },
              )}
              {field('supplier_product_ids', 'Candidate product UUIDs', {
                help: 'Optional comma-separated candidate scope.',
              })}
            </div>
          </section>
        )}
        {includes('dispatch') && (
          <section
            aria-label="Dispatch inputs"
            className="space-y-4 border-t pt-5"
          >
            <h3 className="text-base font-semibold">Dispatch / advisory</h3>
            <div className="grid gap-4 sm:grid-cols-2">
              {field('flexible_load_id', 'Flexible load (required)', {
                selector: 'loads',
              })}
              {!fresh &&
                field('dispatch_scenario_id', 'Dispatch scenario UUID', {
                  required: true,
                })}
              {field('dispatch_window_start', 'Window start (UTC offset)', {
                required: true,
                help: 'ISO timestamp with Z or an explicit offset.',
              })}
              {field('dispatch_window_end', 'Window end (UTC offset)', {
                required: fresh,
              })}
              {field('dispatch_duration_minutes', 'Duration (minutes)', {
                numeric: true,
              })}
              {field('dispatch_max_delay_minutes', 'Maximum delay (minutes)', {
                required: fresh,
                numeric: true,
              })}
              {field('dispatch_maximum_power_kw', 'Maximum power (kW)', {
                numeric: true,
              })}
              {fresh && (
                <>
                  {field('fresh_dispatch_method_id', 'Dispatch method UUID', {
                    required: true,
                  })}
                  {field(
                    'fresh_dispatch_baseline_start',
                    'Baseline start (UTC offset)',
                    { required: true },
                  )}
                </>
              )}
              {field('forecast_id', 'Existing forecast UUID')}
              {field(
                'dispatch_blackout_constraint_ids',
                'Blackout constraint UUIDs',
                { help: 'Comma-separated operating constraints.' },
              )}
            </div>
          </section>
        )}
        <details className="border-t pt-4">
          <summary className="cursor-pointer text-sm font-medium">
            Advanced record scope
          </summary>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            {!(fresh && includes('measurement')) &&
              field('method_definition_id', 'Prepared measurement method UUID')}
            {field('policy_definition_id', 'Policy UUID')}
            {field('metric_keys', 'Metric keys', {
              help: 'Optional comma-separated registered metric keys.',
            })}
            {!initialContext && field('previous_run_id', 'Previous run UUID')}
            {!includes('measurement') &&
              field('carbon_measurement_id', 'Verified measurement', {
                selector: 'measurements',
              })}
          </div>
        </details>
        {issues.length > 0 && (
          <ul
            role="alert"
            className="space-y-1 text-sm text-amber-700 dark:text-amber-400"
          >
            {issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        )}
        <CommandError error={error} />
        <Button type="submit" disabled={pending || disabled}>
          <ArrowRight />
          {pending ? 'Submitting...' : submitLabel}
        </Button>
      </fieldset>
    </form>
  )
}

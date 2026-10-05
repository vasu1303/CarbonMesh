import { ArrowRight } from 'lucide-react'
import { useEffect, useId, useState, type ComponentProps } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { Link } from 'react-router-dom'
import { z } from 'zod'
import { RecordSelect } from '@/components/record-select'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { useWorkspaceActor } from '@/services/use-workspace-actor'
import type { WorkspaceScope } from '@/lib/workspace'
import { displayText, humanize } from '@/lib/presentation'
import { CommandError } from '@/features/approvals/review-components'
import { BusinessSelect, type BusinessKind } from './business-select'
import { RecordChoices } from './record-choices'
import { agentContextSchema, promptSchema, type AgentContext } from './schemas'

export { CommandError }
type SelectorKind = ComponentProps<typeof RecordSelect>['kind']

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
const split = (value = '') =>
  value
    .split(/[\n,]/)
    .map((v) => v.trim())
    .filter(Boolean)
const optional = (v?: string) => v?.trim() || undefined
const integer = (v?: string) =>
  optional(v) === undefined ? undefined : Number(v)
const dateInput = (value?: string | null) =>
  value ? new Date(value).toISOString().replace(/Z$/, '') : ''
const dateValue = (value?: string) =>
  optional(value)
    ? new Date(
        /(Z|[+-]\d{2}:\d{2})$/.test(value!) ? value! : `${value}Z`,
      ).toISOString()
    : undefined

function defaults(
  scope: WorkspaceScope,
  context?: AgentContext,
  workflow = 'measurement',
): Values {
  const values: Values = {
    actor_id: context?.actor_id ?? '',
    site_id: context?.site_id ?? scope.site_id,
    reporting_period_id:
      context?.reporting_period_id ?? scope.reporting_period_id,
    module: workflow === 'cross_module' ? 'four_module' : workflow,
    grid_source_mode: context?.grid_source_mode ?? 'live',
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
  values.dispatch_window_start = dateInput(
    context?.dispatch_constraints.window_start,
  )
  values.dispatch_window_end = dateInput(
    context?.dispatch_constraints.window_end,
  )
  return values
}

function buildContext(v: Values, company: string, actor: string): AgentContext {
  const context: Record<string, unknown> = {
    company_id: company,
    actor_id: actor,
    site_id: optional(v.site_id),
    reporting_period_id: optional(v.reporting_period_id),
    grid_source_mode: v.grid_source_mode,
    constraints: {
      max_cost_increase_pct: optional(v.cost_max_cost_increase_pct),
      max_lead_time_days: integer(v.cost_max_lead_time_days),
      minimum_circularity_score: optional(v.cost_minimum_circularity_score),
    },
    dispatch_constraints: {
      window_start: dateValue(v.dispatch_window_start),
      window_end: dateValue(v.dispatch_window_end),
      duration_minutes: integer(v.dispatch_duration_minutes),
      max_delay_minutes: integer(v.dispatch_max_delay_minutes),
      maximum_power_kw: optional(v.dispatch_maximum_power_kw),
      blackout_constraint_ids: split(v.dispatch_blackout_constraint_ids),
    },
  }
  for (const name of idFields) context[name] = optional(v[name])
  for (const name of listFields) context[name] = split(v[name])
  return agentContextSchema.parse(context)
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
  const {
    register,
    handleSubmit,
    control,
    setValue,
    getValues,
    getFieldState,
  } = useForm<Values>({
    defaultValues: defaults(scope, initialContext, workflow),
  })
  useEffect(() => {
    if (
      !initialContext &&
      actor.actorId &&
      !getValues('actor_id') &&
      !getFieldState('actor_id').isDirty
    )
      setValue('actor_id', actor.actorId)
  }, [actor.actorId, initialContext, getValues, getFieldState, setValue])
  const formValues = useWatch({ control })
  const valueOf = (name: string) => formValues[name] ?? ''
  const [issues, setIssues] = useState<string[]>([])
  const module = valueOf('module')
  const includes = (name: string) => module === name || module === 'four_module'
  const field = (
    name: string,
    label: string,
    options: {
      required?: boolean
      help?: string
      selector?: SelectorKind
      business?: BusinessKind
      multiple?: boolean
      numeric?: boolean
      date?: boolean
    } = {},
  ) => (
    <div key={name} className="min-w-0 space-y-1.5">
      <Label htmlFor={`${prefix}-${name}`} className="text-sm font-medium">
        {label}
        {options.required ? ' (required)' : ''}
      </Label>
      {options.selector && options.multiple ? (
        <RecordChoices
          kind={options.selector}
          label={label}
          value={split(valueOf(name))}
          onChange={(values) => setValue(name, values.join(', '))}
        />
      ) : options.selector ? (
        <RecordSelect
          kind={options.selector}
          id={`${prefix}-${name}`}
          value={valueOf(name)}
          required={options.required}
          placeholder={`Choose ${label.toLowerCase()}`}
          onChange={(event) => {
            setValue(name, event.target.value)
            if (name === 'standard_id') setValue('requirement_ids', '')
            if (name === 'flexible_load_id')
              setValue('dispatch_blackout_constraint_ids', '')
          }}
        />
      ) : options.business ? (
        <BusinessSelect
          kind={options.business}
          scope={scope}
          id={`${prefix}-${name}`}
          multiple={options.multiple}
          required={options.required}
          value={options.multiple ? split(valueOf(name)) : valueOf(name)}
          parentId={
            options.business === 'requirements'
              ? valueOf('standard_id')
              : valueOf('flexible_load_id')
          }
          onChange={(event) =>
            setValue(
              name,
              options.multiple
                ? Array.from(
                    event.target.selectedOptions,
                    (item) => item.value,
                  ).join(', ')
                : event.target.value,
            )
          }
        />
      ) : (
        <Input
          id={`${prefix}-${name}`}
          {...register(name)}
          inputMode={options.numeric ? 'decimal' : undefined}
          type={
            options.date
              ? 'datetime-local'
              : options.numeric
                ? 'number'
                : 'text'
          }
          step={options.numeric || options.date ? 'any' : undefined}
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
      if (!values.actor_id) {
        setIssues(['Choose a requester.'])
        return
      }
      const context = buildContext(values, scope.company_id, values.actor_id)
      const query = promptRequired
        ? promptSchema.parse(values.query)
        : undefined
      const previous = optional(values.previous_run_id)
      if (previous) z.uuid().parse(previous)
      const missing: string[] = []
      if (
        includes('measurement') &&
        !context.carbon_measurement_id &&
        !context.material_scope?.length &&
        !context.activity_record_ids?.length
      )
        missing.push('Choose a measurement, material, or source activity.')
      if (includes('assurance') && !context.standard_id)
        missing.push('Choose a disclosure standard.')
      if (includes('procurement')) {
        if (!context.material_scope?.length)
          missing.push('Choose the materials to compare.')
        if (!optional(values.cost_max_cost_increase_pct))
          missing.push('Set the maximum cost increase.')
        if (!optional(values.cost_max_lead_time_days))
          missing.push('Set the maximum lead time.')
        if (!optional(values.cost_minimum_circularity_score))
          missing.push('Set the minimum circularity score.')
      }
      if (includes('dispatch')) {
        if (!context.flexible_load_id) missing.push('Choose a flexible load.')
        if (!context.dispatch_constraints.window_start)
          missing.push('Set the earliest operating start.')
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
              (item) =>
                `${
                  humanize(
                    item.path
                      .filter((part) => typeof part === 'string')
                      .join(' ')
                      .replaceAll('_id', '')
                      .replaceAll('_ids', ''),
                  ) || 'Request'
                }: ${displayText(item.message).replace(/UUID|IDs?/g, 'selection')}`,
            )
          : ['Check the request fields.'],
      )
    }
  })
  if (initialContext?.fresh_inputs)
    return (
      <div role="alert" className="space-y-2 text-sm">
        <p className="text-amber-700">
          This saved activity cannot be resumed from this workspace.
        </p>
        <Link to="/ask" className="text-emerald-700 underline">
          Start a new request using saved records
        </Link>
      </div>
    )

  return (
    <form
      onSubmit={submit}
      className="space-y-6"
      aria-label={initialContext ? 'Clarification form' : 'Agent request'}
    >
      <fieldset disabled={pending || disabled} className="min-w-0 space-y-6">
        {missingFields.length > 0 && (
          <div className="text-sm">
            <p className="font-medium">This activity needs more information</p>
            <ul className="mt-2 list-inside list-disc text-muted-foreground">
              {missingFields.map((name) => (
                <li key={name}>{humanize(name.replace(/_ids?$/g, ''))}</li>
              ))}
            </ul>
          </div>
        )}
        {promptRequired && (
          <div className="space-y-2">
            <Label htmlFor={`${prefix}-query`}>
              {initialContext
                ? 'Clarify your request'
                : 'What would you like to find out?'}
            </Label>
            <Textarea
              id={`${prefix}-query`}
              {...register('query')}
              required
              minLength={3}
              maxLength={4000}
              rows={4}
              className="min-h-28"
              placeholder="Compare the available options for this reporting period."
            />
          </div>
        )}
        {select('module', 'Related workflow', [
          ['measurement', 'Measure emissions'],
          ['assurance', 'Review disclosure'],
          ['procurement', 'Compare suppliers'],
          ['dispatch', 'Plan a flexible load'],
          ['four_module', 'Connected analysis'],
        ])}
        <div className="space-y-1.5">
          <Label htmlFor={`${prefix}-actor_id`}>Requester</Label>
          <RecordSelect
            id={`${prefix}-actor_id`}
            kind="actors"
            placeholder="Choose a requester"
            {...register('actor_id')}
            value={valueOf('actor_id')}
            required
          />
        </div>
        {includes('measurement') && (
          <section
            aria-label="Measurement inputs"
            className="space-y-4 border-t pt-5"
          >
            <h3 className="text-base font-semibold">Measurement</h3>
            <div className="grid gap-4 sm:grid-cols-2">
              {field('carbon_measurement_id', 'Verified measurement', {
                selector: 'measurements',
              })}
              {field('material_scope', 'Materials', {
                business: 'materials',
                multiple: true,
              })}
            </div>
          </section>
        )}
        {includes('assurance') && (
          <section
            aria-label="Assurance inputs"
            className="space-y-4 border-t pt-5"
          >
            <h3 className="text-base font-semibold">Disclosure</h3>
            <div className="grid gap-4 sm:grid-cols-2">
              {field('standard_id', 'Standard', {
                selector: 'standards',
                required: true,
              })}
              {field('disclosure_draft_id', 'Disclosure draft', {
                required: true,
                selector: 'assurance',
              })}
            </div>
            <details>
              <summary className="cursor-pointer text-sm">
                Requirements and evidence
              </summary>
              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                {field('requirement_ids', 'Requirements', {
                  business: 'requirements',
                  multiple: true,
                })}
                {field('evidence_item_ids', 'Supporting evidence', {
                  selector: 'evidence',
                  multiple: true,
                })}
              </div>
            </details>
          </section>
        )}
        {includes('procurement') && (
          <section
            aria-label="Procurement inputs"
            className="space-y-4 border-t pt-5"
          >
            <h3 className="text-base font-semibold">Supplier comparison</h3>
            <div className="grid gap-4 sm:grid-cols-2">
              {!includes('measurement') &&
                field('material_scope', 'Materials', {
                  required: true,
                  business: 'materials',
                  multiple: true,
                })}
              {field('procurement_scenario_id', 'Procurement scenario', {
                required: true,
                selector: 'procurement',
              })}
            </div>
            <details open={initialContext ? true : undefined}>
              <summary className="cursor-pointer text-sm font-medium">
                Commercial constraints
              </summary>
              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                {field(
                  'cost_max_cost_increase_pct',
                  'Maximum cost increase (%)',
                  { numeric: true },
                )}
                {field('cost_max_lead_time_days', 'Maximum lead time (days)', {
                  numeric: true,
                })}
                {field(
                  'cost_minimum_circularity_score',
                  'Minimum circularity score',
                  { numeric: true },
                )}
                {field('supplier_product_ids', 'Candidate products', {
                  selector: 'products',
                  multiple: true,
                })}
              </div>
            </details>
          </section>
        )}
        {includes('dispatch') && (
          <section
            aria-label="Dispatch inputs"
            className="space-y-4 border-t pt-5"
          >
            <h3 className="text-base font-semibold">Operating plan</h3>
            <p className="text-sm text-muted-foreground">Advisory only</p>
            <div className="grid gap-4 sm:grid-cols-2">
              {field('flexible_load_id', 'Flexible load', {
                selector: 'loads',
                required: true,
              })}
              {field('dispatch_scenario_id', 'Dispatch scenario', {
                required: true,
                selector: 'dispatch',
              })}
              {field('forecast_id', 'Recorded forecast', {
                selector: 'forecasts',
              })}
            </div>
            <details open={initialContext ? true : undefined}>
              <summary className="cursor-pointer text-sm font-medium">
                Operating constraints
              </summary>
              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                {field('dispatch_window_start', 'Earliest start (UTC)', {
                  date: true,
                })}
                {field('dispatch_window_end', 'Latest finish (UTC)', {
                  date: true,
                })}
                {field('dispatch_duration_minutes', 'Duration (minutes)', {
                  numeric: true,
                })}
                {field(
                  'dispatch_max_delay_minutes',
                  'Maximum delay (minutes)',
                  { numeric: true },
                )}
                {field('dispatch_maximum_power_kw', 'Maximum power (kW)', {
                  numeric: true,
                })}
                {field(
                  'dispatch_blackout_constraint_ids',
                  'Unavailable operating periods',
                  { business: 'blackouts', multiple: true },
                )}
              </div>
            </details>
          </section>
        )}
        <details
          className="border-t pt-4"
          open={initialContext ? true : undefined}
        >
          <summary className="cursor-pointer text-sm font-medium">
            Advanced analysis scope
          </summary>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            {field('method_definition_id', 'Measurement method', {
              selector: 'methods',
            })}
            {field('policy_definition_id', 'Policy', { selector: 'policies' })}
            {field('metric_keys', 'Metrics', {
              business: 'metrics',
              multiple: true,
            })}
            {!initialContext &&
              field('previous_run_id', 'Previous activity', {
                selector: 'runs',
              })}
            {!includes('measurement') &&
              field('carbon_measurement_id', 'Verified measurement', {
                selector: 'measurements',
              })}
            {field('activity_record_ids', 'Source activity', {
              selector: 'activity',
              multiple: true,
            })}
          </div>
        </details>
        {issues.length > 0 && (
          <ul
            role="alert"
            className="space-y-1 text-sm text-amber-700 dark:text-amber-400"
          >
            {issues.map((issue) => (
              <li key={issue}>{displayText(issue)}</li>
            ))}
          </ul>
        )}
        <CommandError error={error} />
        <Button
          type="submit"
          disabled={pending || disabled || !valueOf('actor_id')}
        >
          <ArrowRight />
          {pending ? 'Submitting...' : submitLabel}
        </Button>
      </fieldset>
    </form>
  )
}

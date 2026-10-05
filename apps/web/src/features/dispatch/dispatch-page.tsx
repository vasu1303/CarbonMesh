import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FilePlus2, Plus, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useFieldArray, useForm, useWatch } from 'react-hook-form'
import { useNavigate, useOutletContext } from 'react-router-dom'
import {
  EmptyState,
  PageControls,
  QueryRefresh,
  QueryState,
} from '@/components/query-state'
import { RecordSelect } from '@/components/record-select'
import { displayText } from '@/lib/presentation'
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
import { useWorkspaceActor } from '@/features/auth/use-workspace-actor'
import {
  CommandError,
  FieldError,
  OpenArtifact,
  WorkflowContext,
} from '@/features/assurance/workflow-ui'
import { usePayloadKey } from '@/features/assurance/use-payload-key'
import type { WorkspaceScope } from '@/lib/workspace'
import { apiRequest } from '@/services/api'
import { OperatingConstraints } from './dispatch-views'
import { dispatchQueries, scenarioInScope, scenarioKey } from './queries'
import {
  scenarioFormSchema,
  scenarioSchema,
  type ScenarioForm,
} from './schemas'

function DispatchWorkspace({ scope }: { scope: WorkspaceScope }) {
  const actor = useWorkspaceActor()
  const [offset, setOffset] = useState(0)
  const loads = useQuery(dispatchQueries.loads(scope, offset))
  const client = useQueryClient()
  const navigate = useNavigate()
  const keyFor = usePayloadKey()
  const form = useForm<ScenarioForm>({
    resolver: zodResolver(scenarioFormSchema),
    defaultValues: {
      flexible_load_id: '',
      method_definition_id: '',
      forecast_source_document_id: '',
      requested_by: actor.actorId,
      policy_definition_id: '',
      window_start: '',
      window_end: '',
      baseline_start: '',
      maximum_delay_minutes: '',
      available_capacity_kw: '',
      blackout_windows: [],
      approval_expires_at: '',
    },
  })
  const watched = useWatch({ control: form.control })
  const blackouts = useFieldArray({
    control: form.control,
    name: 'blackout_windows',
  })
  useEffect(() => {
    if (actor.actorId) form.setValue('requested_by', actor.actorId)
  }, [actor.actorId, form])
  const create = useMutation({
    mutationFn: (values: ScenarioForm) => {
      const body = {
        company_id: scope.company_id,
        site_id: scope.site_id,
        flexible_load_id: values.flexible_load_id,
        method_definition_id: values.method_definition_id,
        forecast_source_document_id: values.forecast_source_document_id,
        requested_by: values.requested_by,
        policy_definition_id: values.policy_definition_id || null,
        window_start: new Date(`${values.window_start}Z`).toISOString(),
        window_end: new Date(`${values.window_end}Z`).toISOString(),
        baseline_start: new Date(`${values.baseline_start}Z`).toISOString(),
        maximum_delay_minutes: Number(values.maximum_delay_minutes),
        available_capacity_kw: values.available_capacity_kw || null,
        blackout_windows: values.blackout_windows.map((window) => ({
          start: new Date(`${window.start}Z`).toISOString(),
          end: new Date(`${window.end}Z`).toISOString(),
        })),
        approval_expires_at: values.approval_expires_at
          ? new Date(`${values.approval_expires_at}Z`).toISOString()
          : null,
        objective: 'minimum_carbon',
      }
      return apiRequest(
        '/dispatch/scenarios',
        scenarioSchema.refine(
          (scenario) =>
            scenarioInScope(scenario, scope) &&
            scenario.flexible_load.id === values.flexible_load_id &&
            scenario.method.id === values.method_definition_id &&
            scenario.forecast_source_document_id ===
              values.forecast_source_document_id,
        ),
        {
          signal: new AbortController().signal,
          method: 'POST',
          body: { ...body, idempotency_key: keyFor(body) },
        },
      )
    },
    retry: false,
    onSuccess: (scenario) => {
      client.setQueryData(scenarioKey(scope.company_id, scenario.id), scenario)
      navigate(`/dispatch/${scenario.id}`)
    },
  })
  const selectedLoadId = useWatch({
    control: form.control,
    name: 'flexible_load_id',
  })
  const selectedLoad = loads.data?.items.find(
    (load) => load.id === selectedLoadId,
  )
  const fields = [
    ['window_start', 'Earliest start (UTC)', 'datetime-local'],
    ['window_end', 'Latest finish (UTC)', 'datetime-local'],
    ['baseline_start', 'Baseline start (UTC)', 'datetime-local'],
    ['maximum_delay_minutes', 'Maximum delay (minutes)', 'number'],
    ['available_capacity_kw', 'Available capacity (kW, optional)', 'text'],
    [
      'approval_expires_at',
      'Approval expiry (UTC, optional)',
      'datetime-local',
    ],
  ] as const
  return (
    <main className="min-w-0 space-y-6 px-5 py-6 sm:px-8">
      <header>
        <h1 className="text-2xl font-semibold">Energy planning</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Lower-carbon operating windows. Advisory only.
        </p>
      </header>
      <WorkflowContext scope={scope} />
      <section aria-label="Flexible loads" className="min-w-0 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold">Flexible loads</h2>
          <QueryRefresh query={loads} label="flexible loads" />
        </div>
        <QueryState query={loads}>
          {(page) => (
            <>
              {page.items.length ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Load</TableHead>
                      <TableHead>Power (kW)</TableHead>
                      <TableHead>Duration (min)</TableHead>
                      <TableHead>Energy (kWh)</TableHead>
                      <TableHead>Interruptible</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {page.items.map((load) => (
                      <TableRow key={load.id}>
                        <TableCell>
                          <p className="font-medium">
                            {displayText(load.name)}
                          </p>
                        </TableCell>
                        <TableCell className="font-mono">
                          {load.power_kw}
                        </TableCell>
                        <TableCell>{load.duration_minutes}</TableCell>
                        <TableCell className="font-mono">
                          {load.energy_kwh}
                        </TableCell>
                        <TableCell>
                          {load.is_interruptible ? 'Yes' : 'No'}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <EmptyState
                  title="No flexible loads"
                  detail="No active loads are available at this site."
                />
              )}
              <PageControls
                {...page}
                count={page.items.length}
                onChange={setOffset}
              />
            </>
          )}
        </QueryState>
      </section>
      <section
        aria-label="Create dispatch scenario"
        className="space-y-4 border-t pt-5"
      >
        <h2 className="text-base font-semibold">Create advisory scenario</h2>
        <form
          aria-label="Create dispatch scenario"
          onSubmit={form.handleSubmit((values) => create.mutate(values))}
          className="space-y-5"
        >
          <fieldset
            disabled={create.isPending}
            className="grid min-w-0 gap-4 sm:grid-cols-2"
          >
            <div className="min-w-0 sm:col-span-2">
              <label
                htmlFor="dispatch-load"
                className="mb-2 block text-xs font-medium"
              >
                Flexible load
              </label>
              <NativeSelect
                id="dispatch-load"
                className="w-full"
                required
                {...form.register('flexible_load_id')}
              >
                <NativeSelectOption value="">Select a load</NativeSelectOption>
                {loads.data?.items.map((load) => (
                  <NativeSelectOption key={load.id} value={load.id}>
                    {displayText(load.name)}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
              <FieldError
                message={form.formState.errors.flexible_load_id?.message}
              />
            </div>
            {(
              [
                ['method_definition_id', 'Calculation method', 'methods'],
                ['forecast_source_document_id', 'Stored forecast', 'forecasts'],
                ['requested_by', 'Requested by', 'actors'],
                ['policy_definition_id', 'Policy (optional)', 'policies'],
              ] as const
            ).map(([name, label, kind]) => (
              <div key={name} className="min-w-0">
                <label
                  htmlFor={`dispatch-${name}`}
                  className="mb-2 block text-xs font-medium"
                >
                  {label}
                </label>
                <RecordSelect
                  id={`dispatch-${name}`}
                  kind={kind}
                  allowEmpty={name === 'policy_definition_id'}
                  required={name !== 'policy_definition_id'}
                  {...form.register(name)}
                  value={watched[name] ?? ''}
                  aria-invalid={!!form.formState.errors[name]}
                />
                <FieldError message={form.formState.errors[name]?.message} />
              </div>
            ))}
            {fields.map(([name, label, type]) => (
              <div key={name} className="min-w-0">
                <label
                  htmlFor={`dispatch-${name}`}
                  className="mb-2 block text-xs font-medium"
                >
                  {label}
                </label>
                <Input
                  id={`dispatch-${name}`}
                  type={type}
                  min={type === 'number' ? 0 : undefined}
                  step={type === 'number' ? 1 : undefined}
                  required={
                    ![
                      'policy_definition_id',
                      'available_capacity_kw',
                      'approval_expires_at',
                    ].includes(name)
                  }
                  {...form.register(name)}
                  aria-invalid={!!form.formState.errors[name]}
                />
                <FieldError message={form.formState.errors[name]?.message} />
              </div>
            ))}
          </fieldset>
          {selectedLoad && (
            <details>
              <summary className="cursor-pointer text-sm font-medium">
                Recorded load constraints
              </summary>
              <div className="mt-3">
                <OperatingConstraints items={selectedLoad.constraints} />
              </div>
            </details>
          )}
          <fieldset disabled={create.isPending} className="space-y-3">
            <legend className="mb-3 text-sm font-medium">
              Additional blackouts
            </legend>
            {blackouts.fields.map((field, index) => (
              <div
                key={field.id}
                className="grid gap-3 sm:grid-cols-[1fr_1fr_auto]"
              >
                <div>
                  <label
                    htmlFor={`blackout-start-${field.id}`}
                    className="mb-2 block text-xs"
                  >
                    Blackout start (UTC)
                  </label>
                  <Input
                    id={`blackout-start-${field.id}`}
                    type="datetime-local"
                    required
                    {...form.register(`blackout_windows.${index}.start`)}
                  />
                  <FieldError
                    message={
                      form.formState.errors.blackout_windows?.[index]?.start
                        ?.message
                    }
                  />
                </div>
                <div>
                  <label
                    htmlFor={`blackout-end-${field.id}`}
                    className="mb-2 block text-xs"
                  >
                    Blackout end (UTC)
                  </label>
                  <Input
                    id={`blackout-end-${field.id}`}
                    type="datetime-local"
                    required
                    {...form.register(`blackout_windows.${index}.end`)}
                  />
                  <FieldError
                    message={
                      form.formState.errors.blackout_windows?.[index]?.end
                        ?.message
                    }
                  />
                </div>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="mt-6"
                  aria-label="Remove blackout"
                  title="Remove blackout"
                  onClick={() => blackouts.remove(index)}
                >
                  <X />
                </Button>
              </div>
            ))}
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => blackouts.append({ start: '', end: '' })}
            >
              <Plus />
              Add blackout
            </Button>
          </fieldset>
          <CommandError error={create.error} />
          <Button
            type="submit"
            disabled={
              create.isPending || !loads.data?.items.length || loads.isError
            }
          >
            <FilePlus2 />
            {create.isPending ? 'Creating scenario...' : 'Create scenario'}
          </Button>
        </form>
      </section>
      <OpenArtifact kind="Scenario" path="/dispatch" />
    </main>
  )
}
export default function DispatchPage() {
  const scope = useOutletContext<WorkspaceScope>()
  return (
    <DispatchWorkspace
      key={`${scope.company_id}:${scope.site_id}:${scope.reporting_period_id}`}
      scope={scope}
    />
  )
}

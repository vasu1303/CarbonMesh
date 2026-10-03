import { useQuery } from '@tanstack/react-query'
import {
  Children,
  cloneElement,
  isValidElement,
  useId,
  type ReactElement,
  type ReactNode,
} from 'react'
import type { UseFormRegisterReturn } from 'react-hook-form'
import { useOutletContext } from 'react-router-dom'

import { QueryState } from '@/components/query-state'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { useWorkspaceActor } from '@/features/auth/use-workspace-actor'
import type { WorkspaceScope } from '@/lib/workspace'
import { contextSchema } from '@/schemas/context'
import { ApiError, apiRequest, retryApiQuery } from '@/services/api'
import { metricsSchema } from './schemas'

export const formGrid = 'grid min-w-0 gap-4 sm:grid-cols-2 xl:grid-cols-3'
export const sectionClass = 'space-y-4 border-t py-6'

export function Field({
  label,
  error,
  children,
}: {
  label: string
  error?: string
  children: ReactNode
}) {
  const id = useId()
  return (
    <div className="flex min-w-0 flex-col gap-2 text-xs font-medium">
      <Label htmlFor={id} className="text-xs leading-normal">
        {label}
      </Label>
      {Children.map(children, (child, index) =>
        index === 0 && isValidElement(child)
          ? cloneElement(
              child as ReactElement<{
                id?: string
                'aria-invalid'?: boolean
                'aria-describedby'?: string
              }>,
              {
                id,
                ...(error
                  ? { 'aria-invalid': true, 'aria-describedby': `${id}-error` }
                  : {}),
              },
            )
          : child,
      )}
      {error && (
        <span
          id={`${id}-error`}
          role="alert"
          className="font-normal text-destructive"
        >
          {error}
        </span>
      )}
    </div>
  )
}

export function ActorField({
  registration,
  error,
}: {
  registration: UseFormRegisterReturn
  error?: string
}) {
  const actor = useWorkspaceActor()
  return (
    <Field label="Actor UUID" error={error}>
      <Input
        {...registration}
        readOnly={actor.authenticated || !!actor.actorId}
        required
        autoComplete="off"
        aria-invalid={!!error}
      />
      <span className="font-normal text-muted-foreground">
        {actor.authenticated
          ? 'Authenticated principal'
          : 'An existing company actor is required for commands.'}
      </span>
    </Field>
  )
}

export function CommandError({ error }: { error: unknown }) {
  if (!error) return null
  const api = error instanceof ApiError ? error : undefined
  return (
    <div
      role="alert"
      className="space-y-2 border-l-2 border-amber-600 pl-3 text-sm"
    >
      {api && (
        <p className="font-medium">
          {(api.terminalState || api.code).replaceAll('_', ' ')}
        </p>
      )}
      <p>
        {error instanceof Error
          ? error.message
          : 'The request could not be completed.'}
      </p>
      {api?.fieldDetails && (
        <ul className="space-y-1 text-xs">
          {Object.entries(api.fieldDetails)
            .filter(
              (entry): entry is [string, string] =>
                typeof entry[1] === 'string',
            )
            .map(([key, value]) => (
              <li key={key}>
                {key}: {value}
              </li>
            ))}
        </ul>
      )}
      {api?.traceId && (
        <p className="break-all font-mono text-xs text-muted-foreground">
          Trace: {api.traceId}
        </p>
      )}
    </div>
  )
}

export function WorkspaceProvenance() {
  const scope = useOutletContext<WorkspaceScope>()
  const context = useQuery({
    queryKey: ['intake-context', scope],
    queryFn: ({ signal }) =>
      apiRequest(
        '/context/resolve',
        contextSchema.refine(
          (data) =>
            data.company.id === scope.company_id &&
            data.site.id === scope.site_id &&
            data.reporting_period.id === scope.reporting_period_id,
        ),
        {
          signal,
          body: {
            company_id: scope.company_id,
            site_id: scope.site_id,
            reporting_period_id: scope.reporting_period_id,
            metric_definition_ids: [scope.metric_id],
            workflow: 'measurement',
          },
        },
      ),
    staleTime: 60_000,
    retry: retryApiQuery,
  })
  return (
    <section aria-label="Workspace provenance" className="border-y py-3">
      <QueryState query={context}>
        {(data) => (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
            <span className="font-medium">{data.company.name}</span>
            <span>{data.site.name}</span>
            <span className="text-muted-foreground">
              {data.reporting_period.name}
            </span>
            <Badge variant="outline">
              {data.company.is_synthetic
                ? 'Synthetic workspace / API'
                : 'Non-synthetic workspace / API'}
            </Badge>
          </div>
        )}
      </QueryState>
      {context.isError && !context.data && (
        <p className="text-xs text-amber-700">
          Workspace provenance unavailable.
        </p>
      )}
    </section>
  )
}

export function MetricField({
  registration,
  error,
  materialOnly = false,
}: {
  registration: UseFormRegisterReturn
  error?: string
  materialOnly?: boolean
}) {
  const scope = useOutletContext<WorkspaceScope>()
  const listId = useId()
  const metrics = useQuery({
    queryKey: ['intake-metrics', scope.company_id],
    queryFn: ({ signal }) =>
      apiRequest(
        '/semantic/metrics',
        metricsSchema.refine((data) => data.company_id === scope.company_id),
        { signal, params: { company_id: scope.company_id, active_only: true } },
      ),
    staleTime: 60_000,
    retry: retryApiQuery,
  })
  const items = metrics.data?.items.filter(
    (item) => !materialOnly || item.key === 'emissions.scope3.category1',
  )
  return (
    <Field label="Metric definition UUID" error={error}>
      {items?.length ? (
        <NativeSelect
          {...registration}
          aria-invalid={!!error}
          required
          className="w-full min-w-0"
        >
          <NativeSelectOption value="">Select a metric</NativeSelectOption>
          {items.map((item) => (
            <NativeSelectOption key={item.id} value={item.id}>
              {item.name} ({item.version})
            </NativeSelectOption>
          ))}
        </NativeSelect>
      ) : (
        <Input
          {...registration}
          aria-invalid={!!error}
          required
          aria-describedby={listId}
        />
      )}
      <span id={listId} className="font-normal text-muted-foreground">
        {metrics.isPending
          ? 'Loading metric catalog. A known metric UUID may be entered.'
          : metrics.isError
            ? `Catalog unavailable: ${metrics.error.message}`
            : !items?.length
              ? 'No matching catalog entries. A valid metric UUID is required.'
              : 'Versioned metric from the API catalog'}
      </span>
    </Field>
  )
}

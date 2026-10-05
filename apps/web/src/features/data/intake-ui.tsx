import { useQuery } from '@tanstack/react-query'
import {
  Children,
  cloneElement,
  isValidElement,
  useId,
  useEffect,
  type ReactElement,
  type ReactNode,
} from 'react'
import type { UseFormRegisterReturn } from 'react-hook-form'
import { useOutletContext } from 'react-router-dom'

import { QueryState } from '@/components/query-state'
import { RecordSelect } from '@/components/record-select'
import { Badge } from '@/components/ui/badge'
import { Label } from '@/components/ui/label'
import { useWorkspaceActor } from '@/features/auth/use-workspace-actor'
import { displayText, humanize } from '@/lib/presentation'
import type { WorkspaceScope } from '@/lib/workspace'
import { contextSchema } from '@/schemas/context'
import { ApiError, apiRequest, retryApiQuery } from '@/services/api'

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
          {displayText(error)}
        </span>
      )}
    </div>
  )
}

export function ActorField({
  registration,
  error,
  value,
}: {
  registration: UseFormRegisterReturn
  error?: string
  value?: string
}) {
  const actor = useWorkspaceActor()
  useEffect(() => {
    if (actor.actorId && !value)
      void registration.onChange({
        target: { name: registration.name, value: actor.actorId },
        type: 'change',
      })
  }, [actor.actorId, registration, value])
  return (
    <Field label="Requested by" error={error}>
      <RecordSelect
        kind="actors"
        {...registration}
        value={value}
        required
        aria-invalid={!!error}
      />
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
        <p className="font-medium">{humanize(api.terminalState || api.code)}</p>
      )}
      <p>
        {error instanceof Error
          ? displayText(error.message)
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
                {humanize(key)}: {displayText(value)}
              </li>
            ))}
        </ul>
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
            <span className="font-medium">
              {displayText(data.company.name)}
            </span>
            <span>{displayText(data.site.name)}</span>
            <span className="text-muted-foreground">
              {displayText(data.reporting_period.name)}
            </span>
            <Badge variant="outline">
              {data.company.is_synthetic ? 'Synthetic data' : 'Workspace data'}
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
  value,
  materialOnly = false,
}: {
  registration: UseFormRegisterReturn
  error?: string
  materialOnly?: boolean
  value?: string
}) {
  return (
    <Field
      label={materialOnly ? 'Purchased-material metric' : 'Metric'}
      error={error}
    >
      <RecordSelect
        kind="metrics"
        {...registration}
        value={value}
        required
        aria-invalid={!!error}
      />
    </Field>
  )
}

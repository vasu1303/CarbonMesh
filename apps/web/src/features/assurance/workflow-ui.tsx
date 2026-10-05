import { useQuery } from '@tanstack/react-query'
import { ArrowRight } from 'lucide-react'
import { useForm, useWatch } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { Link, useNavigate } from 'react-router-dom'
import { z } from 'zod'
import { QueryState } from '@/components/query-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { RecordSelect } from '@/components/record-select'
import { displayText, humanize } from '@/lib/presentation'
import type { WorkspaceScope } from '@/lib/workspace'
import { contextSchema } from '@/schemas/context'
import { ApiError, apiRequest, retryApiQuery } from '@/services/api'

export function WorkflowContext({ scope }: { scope: WorkspaceScope }) {
  const query = useQuery({
    queryKey: ['assurance-dispatch-context', scope],
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
            workflow: 'cross_module',
          },
        },
      ),
    retry: retryApiQuery,
  })
  return (
    <section
      aria-label="Workspace provenance"
      className="border-y py-3 text-sm"
    >
      <QueryState query={query}>
        {(data) => (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <span className="font-medium">
              {displayText(data.company.name)}
            </span>
            <span>{displayText(data.site.name)}</span>
            <span>{displayText(data.reporting_period.name)}</span>
            <Badge variant="outline">
              {data.company.is_synthetic ? 'Synthetic data' : 'Workspace data'}
            </Badge>
          </div>
        )}
      </QueryState>
      {query.isError && (
        <p className="text-xs text-amber-700">
          Current provenance unavailable.
        </p>
      )}
    </section>
  )
}

export function CommandError({ error }: { error: Error | null }) {
  if (!error) return null
  return (
    <div
      role="alert"
      className="space-y-1 break-words text-sm text-destructive"
    >
      <p>{displayText(error.message)}</p>
      {error instanceof ApiError && (
        <>
          {error.terminalState && <p>{humanize(error.terminalState)}</p>}
          {error.fieldDetails && (
            <dl className="space-y-1 text-xs">
              {Object.entries(error.fieldDetails).map(([field, value]) => (
                <div key={field}>
                  <dt className="font-medium">{humanize(field)}</dt>
                  <dd>
                    {typeof value === 'string'
                      ? displayText(value)
                      : Array.isArray(value)
                        ? value
                            .filter((item) => typeof item === 'string')
                            .map(displayText)
                            .join('; ')
                        : 'Rejected by server validation'}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </>
      )}
    </div>
  )
}
export function FieldError({ message }: { message?: string }) {
  return message ? (
    <p role="alert" className="mt-1 text-xs text-destructive">
      {displayText(message)}
    </p>
  ) : null
}
export function StateBadge({ state }: { state: string }) {
  const good = ['supported', 'valid', 'approved', 'success'].includes(state)
  return (
    <Badge
      variant="outline"
      className={
        good
          ? 'border-emerald-300 text-emerald-800 dark:text-emerald-300'
          : 'text-muted-foreground'
      }
    >
      {humanize(state)}
    </Badge>
  )
}
export function LedgerLink({
  id,
  children,
}: {
  id: string
  children?: React.ReactNode
}) {
  return (
    <Link
      className="break-all text-emerald-700 underline underline-offset-4 dark:text-emerald-400"
      to={`/ledger?event=${id}`}
    >
      {children ?? 'View evidence trail'}
    </Link>
  )
}
export function HashValue({
  label,
  value,
}: {
  label: string
  value: string | null
}) {
  return (
    <div className="min-w-0 space-y-1">
      <dt className="text-xs text-muted-foreground">
        {humanize(label.replace(/hash|checksum|signature/gi, 'integrity'))}
      </dt>
      <dd className="text-xs">
        {value ? 'Integrity recorded' : 'Integrity missing'}
      </dd>
    </div>
  )
}
const openSchema = z.object({ id: z.uuid('Select a record.') })
export function OpenArtifact({ kind, path }: { kind: string; path: string }) {
  const navigate = useNavigate()
  const form = useForm<z.infer<typeof openSchema>>({
    resolver: zodResolver(openSchema),
    defaultValues: { id: '' },
  })
  const watched = useWatch({ control: form.control })
  return (
    <form
      aria-label={`Open ${kind}`}
      onSubmit={form.handleSubmit(({ id }) => navigate(`${path}/${id}`))}
      className="flex flex-wrap items-start gap-3 border-t pt-5"
    >
      <div className="min-w-0 basis-80 grow">
        <label
          htmlFor={`open-${kind}`}
          className="mb-2 block text-xs font-medium"
        >
          {kind}
        </label>
        <RecordSelect
          kind={
            path === '/assurance'
              ? 'assurance'
              : path === '/dispatch'
                ? 'dispatch'
                : 'procurement'
          }
          id={`open-${kind}`}
          {...form.register('id')}
          value={watched.id ?? ''}
          aria-invalid={!!form.formState.errors.id}
          required
        />
        <FieldError message={form.formState.errors.id?.message} />
      </div>
      <Button type="submit" variant="outline" className="mt-6">
        <ArrowRight />
        Open {kind.toLowerCase()}
      </Button>
    </form>
  )
}

export function ValidationReasons({
  details,
}: {
  details: Record<string, unknown>
}) {
  const reasons = [
    details.reason,
    details.message,
    ...(Array.isArray(details.reasons) ? details.reasons : []),
    ...(Array.isArray(details.unknown_placeholders)
      ? details.unknown_placeholders
      : []),
  ].filter((item): item is string => typeof item === 'string')
  return reasons.length ? (
    <ul className="list-inside list-disc text-sm text-muted-foreground">
      {reasons.map((reason, index) => (
        <li key={`${index}-${reason}`}>{displayText(reason)}</li>
      ))}
    </ul>
  ) : null
}

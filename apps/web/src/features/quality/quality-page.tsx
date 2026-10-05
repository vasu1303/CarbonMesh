import { zodResolver } from '@hookform/resolvers/zod'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Search } from 'lucide-react'
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
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
import { Textarea } from '@/components/ui/textarea'
import { useWorkspaceActor } from '@/features/auth/use-workspace-actor'
import {
  ActorField,
  CommandError,
  Field,
  WorkspaceProvenance,
  formGrid,
} from '@/features/data/intake-ui'
import { useCommand } from '@/features/data/commands'
import {
  issueListSchema,
  issueSchema,
  optionalUuid,
  uuid,
  type QualityIssue,
} from '@/features/data/schemas'
import type { WorkspaceScope } from '@/lib/workspace'
import { apiRequest, retryApiQuery } from '@/services/api'
import { useWorkspaceOptions } from '@/services/workspace'

const filterSchema = z.object({
  status: z.enum(['open', 'resolved', 'waived']).default('open'),
  severity: z.enum(['', 'info', 'warning', 'error']).default(''),
  code: z.string().trim().max(100).default(''),
  issue_type: z.string().trim().max(100).default(''),
  import_id: optionalUuid.default(''),
  limit: z.coerce.number().int().min(1).max(200).default(25),
  offset: z.coerce
    .number()
    .int()
    .min(0)
    .max(Number.MAX_SAFE_INTEGER)
    .default(0),
})
type Filters = z.infer<typeof filterSchema>
const reviewSchema = z.object({
  actor_id: uuid,
  status: z.enum(['resolved', 'waived']),
  decision_note: z
    .string()
    .trim()
    .min(1, 'A decision note is required.')
    .max(2000),
})

function ReviewIssue({
  issue,
  onReviewed,
  stale,
}: {
  issue: QualityIssue
  onReviewed: (issue: QualityIssue) => void
  stale: boolean
}) {
  const scope = useOutletContext<WorkspaceScope>()
  const actor = useWorkspaceActor()
  const client = useQueryClient()
  const command = useCommand<QualityIssue>()
  const form = useForm<z.infer<typeof reviewSchema>>({
    resolver: zodResolver(reviewSchema),
    defaultValues: {
      actor_id: actor.actorId,
      status: 'resolved',
      decision_note: '',
    },
  })
  const watched = useWatch({ control: form.control })
  const current = command.data ?? issue
  const reviewer = useWorkspaceOptions('actors', {
    id: current.details.review?.actor_id,
    enabled: !!current.details.review,
  })
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Badge variant="outline">{humanize(current.severity)}</Badge>
        <Badge variant="outline">{humanize(current.status)}</Badge>
      </div>
      <p className="text-sm">{displayText(current.message)}</p>
      <dl className="grid gap-3 text-xs sm:grid-cols-2">
        <div>
          <dt className="text-muted-foreground">Field / row</dt>
          <dd>
            {humanize(current.field_name ?? 'Document')} /{' '}
            {current.row_number ?? 'Document'}
          </dd>
        </div>
      </dl>
      {current.import_id && (
        <Link
          className="block break-all text-sm text-emerald-700 underline dark:text-emerald-400"
          to={`/data?import=${current.import_id}`}
        >
          Open source import
        </Link>
      )}
      {current.details.review && (
        <section
          className="space-y-2 border-t pt-4 text-sm"
          aria-label="Recorded review"
        >
          <h3 className="font-medium">Recorded review</h3>
          <p className="text-xs text-muted-foreground">
            Reviewed by{' '}
            {reviewer.isPending
              ? 'Loading reviewer...'
              : displayText(
                  reviewer.data?.items[0]?.label ?? 'Reviewer unavailable',
                )}
          </p>
          <p className="whitespace-pre-wrap wrap-anywhere">
            {displayText(current.details.review.decision_note)}
          </p>
          <p className="text-xs text-muted-foreground">
            {current.details.reviewed_at ?? current.updated_at}
          </p>
        </section>
      )}
      {current.status === 'open' && (
        <form
          onSubmit={form.handleSubmit((values) => {
            if (stale) return
            if (current.severity === 'error' && values.status === 'waived') {
              form.setError('status', {
                message: 'Blocking errors cannot be waived.',
              })
              return
            }
            return command.run(
              () =>
                apiRequest(
                  `/quality/issues/${current.id}`,
                  issueSchema.refine(
                    (data) =>
                      data.id === current.id &&
                      data.company_id === scope.company_id &&
                      data.status === values.status,
                  ),
                  {
                    signal: new AbortController().signal,
                    method: 'PATCH',
                    body: { ...values, company_id: scope.company_id },
                  },
                ),
              (result) => {
                onReviewed(result)
                void client.invalidateQueries({ queryKey: ['intake-quality'] })
                void client.invalidateQueries({ queryKey: ['intake-import'] })
              },
            )
          })}
          className="space-y-4 border-t pt-4"
        >
          <fieldset disabled={command.pending || stale} className="space-y-4">
            <ActorField
              registration={form.register('actor_id')}
              value={watched.actor_id ?? ''}
              error={form.formState.errors.actor_id?.message}
            />
            <Field
              label="Decision"
              error={form.formState.errors.status?.message}
            >
              <NativeSelect {...form.register('status')}>
                <NativeSelectOption value="resolved">
                  Resolve
                </NativeSelectOption>
                <NativeSelectOption
                  value="waived"
                  disabled={current.severity === 'error'}
                >
                  Waive
                </NativeSelectOption>
              </NativeSelect>
            </Field>
            <Field
              label="Decision note"
              error={form.formState.errors.decision_note?.message}
            >
              <Textarea
                {...form.register('decision_note')}
                required
                maxLength={2000}
                rows={4}
                className="min-w-0"
              />
            </Field>
            <p className="text-xs text-muted-foreground">
              Rejected rows need a corrected upload.
            </p>
            <Button type="submit" disabled={command.pending || stale}>
              <Check />
              {command.pending ? 'Recording...' : 'Record decision'}
            </Button>
          </fieldset>
          {stale && (
            <p role="alert" className="text-sm text-amber-700">
              The queue could not be refreshed. Refresh it before recording a
              decision.
            </p>
          )}
        </form>
      )}
      <CommandError error={command.error} />
      {command.data && (
        <p
          role="status"
          className="text-sm text-emerald-700 dark:text-emerald-400"
        >
          Decision recorded.
        </p>
      )}
    </div>
  )
}

function QualityQueue({ filters }: { filters: Filters }) {
  const scope = useOutletContext<WorkspaceScope>()
  const [params, setParams] = useSearchParams()
  const [selected, setSelected] = useState<QualityIssue | null>(null)
  const form = useForm<Filters>({ defaultValues: filters })
  const queryParams = {
    company_id: scope.company_id,
    status: filters.status,
    severity: filters.severity || undefined,
    code: filters.code || undefined,
    issue_type: filters.issue_type || undefined,
    import_id: filters.import_id || undefined,
    limit: filters.limit,
    offset: filters.offset,
  }
  const query = useQuery({
    queryKey: ['intake-quality', queryParams],
    queryFn: ({ signal }) =>
      apiRequest(
        '/quality/issues',
        issueListSchema.refine(
          (data) =>
            data.offset === filters.offset &&
            data.limit === filters.limit &&
            data.items.length <= data.limit &&
            data.items.every(
              (issue) =>
                issue.company_id === scope.company_id &&
                issue.status === filters.status &&
                (!filters.severity || issue.severity === filters.severity) &&
                (!filters.import_id || issue.import_id === filters.import_id) &&
                (!filters.code || issue.code === filters.code) &&
                (!filters.issue_type ||
                  issue.issue_type === filters.issue_type),
            ),
        ),
        { signal, params: queryParams },
      ),
    staleTime: 30_000,
    retry: retryApiQuery,
  })
  const watched = useWatch({ control: form.control })
  function apply(values: Filters) {
    const parsed = filterSchema.safeParse(values)
    if (!parsed.success) {
      for (const issue of parsed.error.issues)
        form.setError(issue.path[0] as keyof Filters, {
          message: issue.message,
        })
      return
    }
    const next = new URLSearchParams()
    Object.entries(parsed.data).forEach(([key, value]) => {
      if (value !== '') next.set(key, String(value))
    })
    setParams(next)
  }
  return (
    <>
      <form
        aria-label="Quality filters"
        onSubmit={form.handleSubmit((values) =>
          apply({ ...values, offset: 0 }),
        )}
        className={`${formGrid} border-b pb-5`}
      >
        <Field label="Issue status">
          <NativeSelect {...form.register('status')}>
            <NativeSelectOption value="open">Open</NativeSelectOption>
            <NativeSelectOption value="resolved">Resolved</NativeSelectOption>
            <NativeSelectOption value="waived">Waived</NativeSelectOption>
          </NativeSelect>
        </Field>
        <Field label="Severity">
          <NativeSelect {...form.register('severity')}>
            <NativeSelectOption value="">All severities</NativeSelectOption>
            <NativeSelectOption value="error">Error</NativeSelectOption>
            <NativeSelectOption value="warning">Warning</NativeSelectOption>
            <NativeSelectOption value="info">Info</NativeSelectOption>
          </NativeSelect>
        </Field>
        <details className="sm:col-span-2 xl:col-span-3">
          <summary className="cursor-pointer text-xs font-medium">
            Additional filters
          </summary>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {' '}
            <Field
              label="Issue code (exact)"
              error={form.formState.errors.code?.message}
            >
              <Input {...form.register('code')} maxLength={100} />
            </Field>
            <Field
              label="Issue type (exact)"
              error={form.formState.errors.issue_type?.message}
            >
              <Input {...form.register('issue_type')} maxLength={100} />
            </Field>
          </div>
        </details>
        <Field label="Import" error={form.formState.errors.import_id?.message}>
          <RecordSelect
            kind="imports"
            placeholder="All imports"
            {...form.register('import_id')}
            value={watched.import_id ?? ''}
          />
        </Field>
        <Field label="Rows per page">
          <NativeSelect {...form.register('limit')}>
            {[10, 25, 50, 100, 200].map((limit) => (
              <NativeSelectOption key={limit} value={limit}>
                {limit}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
        <div className="flex gap-2">
          <Button type="submit" variant="outline">
            <Search />
            Apply filters
          </Button>
          <Button type="button" variant="ghost" onClick={() => setParams({})}>
            Clear filters
          </Button>
        </div>
      </form>
      <section aria-label="Quality issues" className="min-w-0 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Quality issues</h2>
          <QueryRefresh query={query} label="quality issues" />
        </div>
        <QueryState query={query}>
          {(data) => (
            <>
              {data.items.length ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Issue</TableHead>
                      <TableHead>Severity</TableHead>
                      <TableHead>Row</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Import</TableHead>
                      <TableHead>Review</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.items.map((issue) => (
                      <TableRow key={issue.id}>
                        <TableCell className="min-w-64 max-w-lg whitespace-normal">
                          <p className="font-medium">{humanize(issue.code)}</p>
                          <p className="text-xs text-muted-foreground">
                            {displayText(issue.message)}
                          </p>
                          <p className="mt-1 text-xs text-muted-foreground">
                            {humanize(issue.issue_type)} /{' '}
                            {humanize(issue.field_name ?? 'Document')}
                          </p>
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant="outline"
                            className={
                              issue.severity === 'error'
                                ? 'text-destructive'
                                : ''
                            }
                          >
                            {humanize(issue.severity)}
                          </Badge>
                        </TableCell>
                        <TableCell>{issue.row_number ?? 'Document'}</TableCell>
                        <TableCell>{humanize(issue.status)}</TableCell>
                        <TableCell>
                          {issue.import_id ? (
                            <Link
                              className="font-mono text-xs text-emerald-700 underline dark:text-emerald-400"
                              to={`/data?import=${issue.import_id}`}
                            >
                              Open import
                            </Link>
                          ) : (
                            'Not linked'
                          )}
                        </TableCell>
                        <TableCell>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => setSelected(issue)}
                            aria-label={`Review ${humanize(issue.code)} on row ${issue.row_number ?? 'document'}`}
                          >
                            Review
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <EmptyState
                  title="No matching quality issues"
                  detail="No recorded issues match this status and filter combination."
                />
              )}
              <PageControls
                {...data}
                count={data.items.length}
                onChange={(offset) => {
                  const next = new URLSearchParams(params)
                  next.set('offset', String(offset))
                  setParams(next)
                }}
              />
            </>
          )}
        </QueryState>
      </section>
      <Dialog
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) setSelected(null)
        }}
      >
        <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>Review quality issue</DialogTitle>
            <DialogDescription>{displayText(selected?.code)}</DialogDescription>
          </DialogHeader>
          {selected && (
            <ReviewIssue
              key={selected.id}
              issue={selected}
              onReviewed={setSelected}
              stale={query.isError}
            />
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}

export default function QualityPage() {
  const scope = useOutletContext<WorkspaceScope>()
  const [params, setParams] = useSearchParams()
  const parsed = filterSchema.safeParse(Object.fromEntries(params))
  return (
    <div className="min-w-0 space-y-5 px-5 py-6 sm:px-8">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Check data</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Source validation and audited review
          </p>
        </div>
        <Button asChild variant="outline">
          <Link to="/data">Upload corrected data</Link>
        </Button>
      </header>
      <WorkspaceProvenance />
      {parsed.success ? (
        <QualityQueue
          key={`${scope.company_id}:${params.toString()}`}
          filters={parsed.data}
        />
      ) : (
        <section role="alert" className="space-y-3">
          <p>These filters are invalid. Reset them to review quality issues.</p>
          <Button variant="outline" onClick={() => setParams({})}>
            Reset filters
          </Button>
        </section>
      )}
    </div>
  )
}

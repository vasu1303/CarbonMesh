import { zodResolver } from '@hookform/resolvers/zod'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { FileUp, Search } from 'lucide-react'
import { useRef } from 'react'
import { Controller, useForm, useWatch } from 'react-hook-form'
import { Link, useOutletContext, useSearchParams } from 'react-router-dom'
import { z } from 'zod'

import { EmptyState, QueryRefresh, QueryState } from '@/components/query-state'
import { RecordSelect } from '@/components/record-select'
import { displayText, humanize } from '@/lib/presentation'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import type { WorkspaceScope } from '@/lib/workspace'
import { apiRequest, retryApiQuery } from '@/services/api'
import { readImportFile } from './files'
import {
  CommandError,
  Field,
  MetricField,
  formGrid,
  sectionClass,
} from './intake-ui'
import { useCommand, useIdempotencyKey } from './commands'
import {
  importFormSchema,
  importResultSchema,
  uuid,
  type ImportForm,
} from './schemas'

const openSchema = z.object({ import_id: uuid })

function ImportInspection({ id }: { id: string }) {
  const scope = useOutletContext<WorkspaceScope>()
  const status = useQuery({
    queryKey: ['intake-import', scope.company_id, id],
    queryFn: ({ signal }) =>
      apiRequest(
        `/imports/${id}`,
        importResultSchema.refine(
          (data) =>
            data.import_id === id &&
            data.issues.every((issue) => issue.company_id === scope.company_id),
        ),
        { signal, params: { company_id: scope.company_id } },
      ),
    retry: retryApiQuery,
    refetchInterval: (query) =>
      query.state.data?.status === 'processing' ? 3_000 : false,
  })
  return (
    <section className={sectionClass} aria-label="Import status">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold">Import status</h2>
        <QueryRefresh query={status} label="import status" />
      </div>
      <QueryState query={status}>
        {(data) => (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-2">
              <Badge variant="outline">{humanize(data.status)}</Badge>
              <Badge variant="outline">
                {data.is_synthetic
                  ? 'Synthetic source'
                  : 'Non-synthetic source'}
              </Badge>
            </div>
            <dl className="grid gap-4 text-sm sm:grid-cols-3">
              {[
                ['Accepted', data.accepted_count],
                ['Rejected', data.rejected_count],
                ['Issues', data.issue_count],
              ].map(([label, value]) => (
                <div key={label}>
                  <dt className="text-xs text-muted-foreground">{label}</dt>
                  <dd className="mt-1 font-mono">{value}</dd>
                </div>
              ))}
            </dl>
            <p className="text-xs text-muted-foreground">
              Updated {data.updated_at}
            </p>
            <div className="flex flex-wrap gap-4 text-sm">
              <Link
                className="text-emerald-700 underline dark:text-emerald-400"
                to={`/quality?import_id=${data.import_id}`}
              >
                Review quality checks
              </Link>
              {data.accepted_count > 0 && (
                <Link
                  className="text-emerald-700 underline dark:text-emerald-400"
                  to={
                    data.import_type === 'suppliers'
                      ? '/procurement/suppliers'
                      : '/measurement'
                  }
                >
                  {data.import_type === 'suppliers'
                    ? 'Browse supplier products'
                    : 'Continue to measurement'}
                </Link>
              )}
              {data.source_document_id && (
                <Link
                  className="text-emerald-700 underline dark:text-emerald-400"
                  to={`/data?document=${data.source_document_id}`}
                >
                  Open source document
                </Link>
              )}
            </div>
            {data.issues.length > 0 && (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Row</TableHead>
                    <TableHead>Severity</TableHead>
                    <TableHead>Field</TableHead>
                    <TableHead>Issue</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.issues.map((issue) => (
                    <TableRow key={issue.id}>
                      <TableCell>{issue.row_number ?? 'Document'}</TableCell>
                      <TableCell>{humanize(issue.severity)}</TableCell>
                      <TableCell>
                        {humanize(issue.field_name ?? 'Document')}
                      </TableCell>
                      <TableCell className="min-w-56 whitespace-normal">
                        <span className="font-medium">
                          {humanize(issue.code)}
                        </span>
                        <p className="text-muted-foreground">
                          {displayText(issue.message)}
                        </p>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            {data.issues_truncated && (
              <p className="text-xs text-amber-700">
                Showing {data.returned_issue_count} issues from the import
                response. The quality queue contains the full result.
              </p>
            )}
          </div>
        )}
      </QueryState>
    </section>
  )
}

export function ImportPanel() {
  const scope = useOutletContext<WorkspaceScope>()
  const client = useQueryClient()
  const [params, setParams] = useSearchParams()
  const file = useRef<HTMLInputElement>(null)
  const command = useCommand<z.infer<typeof importResultSchema>>()
  const idempotencyKey = useIdempotencyKey()
  const form = useForm<ImportForm>({
    resolver: zodResolver(importFormSchema),
    defaultValues: {
      kind: 'activity',
      source_name: '',
      metric_definition_id: '',
      interval_start: '',
      interval_end: '',
      external_reference: '',
      is_synthetic: false,
    },
  })
  const watched = useWatch({ control: form.control })
  const open = useForm<z.infer<typeof openSchema>>({
    resolver: zodResolver(openSchema),
    defaultValues: {
      import_id: params.get('import') ?? params.get('import_id') ?? '',
    },
  })
  const openValues = useWatch({ control: open.control })
  const kind = useWatch({ control: form.control, name: 'kind' })
  const id = params.get('import') ?? params.get('import_id')
  function openImport(importId: string) {
    const next = new URLSearchParams(params)
    next.set('view', 'imports')
    next.set('import', importId)
    next.delete('import_id')
    next.delete('document')
    setParams(next)
  }
  async function submit(values: ImportForm) {
    await command.run(
      async () => {
        const selected = file.current?.files?.[0]
        if (!selected)
          throw new Error('Select a CSV or JSON file before importing.')
        const upload = await readImportFile(selected)
        const body = {
          company_id: scope.company_id,
          source_name: values.source_name,
          ...upload,
          external_reference: values.external_reference || null,
          is_synthetic: values.is_synthetic,
          ...(values.kind === 'activity'
            ? {
                site_id: scope.site_id,
                reporting_period_id: scope.reporting_period_id,
                metric_definition_id: values.metric_definition_id,
                interval_start: values.interval_start || null,
                interval_end: values.interval_end || null,
              }
            : {}),
        }
        return apiRequest(
          values.kind === 'activity'
            ? '/activities/import'
            : '/imports/suppliers',
          importResultSchema.refine(
            (data) =>
              data.import_type === values.kind &&
              data.is_synthetic === values.is_synthetic &&
              data.issues.every(
                (issue) => issue.company_id === scope.company_id,
              ),
          ),
          {
            signal: new AbortController().signal,
            timeoutMs: 120_000,
            body: {
              ...body,
              idempotency_key: idempotencyKey({
                kind: values.kind,
                ...body,
              }),
            },
          },
        )
      },
      (result) => {
        client.setQueryData(
          ['intake-import', scope.company_id, result.import_id],
          result,
        )
        void client.invalidateQueries({ queryKey: ['intake-quality'] })
        void client.invalidateQueries({ queryKey: ['procurement-catalog'] })
        open.setValue('import_id', result.import_id)
        openImport(result.import_id)
      },
    )
  }
  return (
    <>
      <section aria-label="New import" className={sectionClass}>
        <h2 className="text-sm font-semibold">
          Upload activity or supplier data
        </h2>
        <form
          onSubmit={(event) => {
            void form.handleSubmit(submit)(event)
          }}
          className="space-y-4"
        >
          <fieldset disabled={command.pending} className="space-y-4">
            <div className={formGrid}>
              <Field label="Import type">
                <NativeSelect {...form.register('kind')}>
                  <NativeSelectOption value="activity">
                    Activity
                  </NativeSelectOption>
                  <NativeSelectOption value="suppliers">
                    Supplier products
                  </NativeSelectOption>
                </NativeSelect>
              </Field>
              <Field
                label="Source name"
                error={form.formState.errors.source_name?.message}
              >
                <Input
                  {...form.register('source_name')}
                  required
                  maxLength={160}
                />
              </Field>
              <Field label="Import file">
                <Input
                  ref={file}
                  type="file"
                  accept=".csv,.json,text/csv,application/json"
                  required
                />
              </Field>
              {kind === 'activity' && (
                <MetricField
                  value={watched.metric_definition_id ?? ''}
                  registration={form.register('metric_definition_id')}
                  error={form.formState.errors.metric_definition_id?.message}
                />
              )}
            </div>
            <details className="space-y-3 text-sm">
              <summary className="cursor-pointer font-medium">
                Additional upload details
              </summary>
              <div className={formGrid}>
                <Field
                  label="Source reference (optional)"
                  error={form.formState.errors.external_reference?.message}
                >
                  <Input
                    {...form.register('external_reference')}
                    maxLength={255}
                  />
                </Field>
                {kind === 'activity' && (
                  <>
                    <Field
                      label="Interval start (UTC, optional)"
                      error={form.formState.errors.interval_start?.message}
                    >
                      <Input
                        type="datetime-local"
                        step={3600}
                        {...form.register('interval_start', {
                          setValueAs: (value: string) =>
                            value && !value.endsWith('Z')
                              ? new Date(value + 'Z').toISOString()
                              : value,
                        })}
                      />
                    </Field>
                    <Field
                      label="Interval end (UTC, optional)"
                      error={form.formState.errors.interval_end?.message}
                    >
                      <Input
                        type="datetime-local"
                        step={3600}
                        {...form.register('interval_end', {
                          setValueAs: (value: string) =>
                            value && !value.endsWith('Z')
                              ? new Date(value + 'Z').toISOString()
                              : value,
                        })}
                      />
                    </Field>
                  </>
                )}
              </div>
            </details>
            <div className="flex items-start gap-2">
              <Controller
                name="is_synthetic"
                control={form.control}
                render={({ field }) => (
                  <Checkbox
                    id="intake-import-synthetic"
                    name={field.name}
                    ref={field.ref}
                    checked={field.value}
                    onCheckedChange={(checked) =>
                      field.onChange(checked === true)
                    }
                    onBlur={field.onBlur}
                    disabled={command.pending}
                    className="mt-0.5"
                  />
                )}
              />
              <Label
                htmlFor="intake-import-synthetic"
                className="leading-normal font-normal"
              >
                This file contains synthetic data
              </Label>
            </div>
            <p className="text-xs text-muted-foreground">
              CSV or JSON, up to 5 MiB.
            </p>
            <Button type="submit" disabled={command.pending}>
              <FileUp />
              {command.pending ? 'Importing...' : 'Upload and check'}
            </Button>
          </fieldset>
          <CommandError error={command.error} />
        </form>
      </section>
      <section className={sectionClass} aria-label="Open import">
        <h2 className="text-sm font-semibold">Open an import</h2>
        <form
          onSubmit={open.handleSubmit((values) => openImport(values.import_id))}
          className="flex flex-wrap items-start gap-3"
        >
          <div className="min-w-0 basis-80 grow">
            <Field
              label="Import"
              error={open.formState.errors.import_id?.message}
            >
              <RecordSelect
                kind="imports"
                {...open.register('import_id')}
                value={openValues.import_id ?? ''}
                required
              />
            </Field>
          </div>
          <Button type="submit" className="mt-6" variant="outline">
            <Search />
            Open import
          </Button>
        </form>
      </section>
      {id ? (
        uuid.safeParse(id).success ? (
          <ImportInspection key={id} id={id} />
        ) : (
          <CommandError
            error={
              new Error('This import link is invalid. Select an import above.')
            }
          />
        )
      ) : (
        <EmptyState
          title="No import selected"
          detail="No upload results to show."
        />
      )}
    </>
  )
}

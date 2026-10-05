import { zodResolver } from '@hookform/resolvers/zod'
import { Download, FileUp, Search, ScanText } from 'lucide-react'
import { useRef } from 'react'
import { Controller, useForm, useWatch } from 'react-hook-form'
import { Link, useOutletContext, useSearchParams } from 'react-router-dom'
import { z } from 'zod'

import { RecordSelect } from '@/components/record-select'
import { displayText, humanize } from '@/lib/presentation'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useWorkspaceActor } from '@/features/auth/use-workspace-actor'
import type { WorkspaceScope } from '@/lib/workspace'
import { ApiError, apiRequest, apiUrl } from '@/services/api'
import { readSourceFile } from './files'
import {
  ActorField,
  CommandError,
  Field,
  formGrid,
  sectionClass,
} from './intake-ui'
import { useCommand } from './commands'
import {
  actorSchema,
  sourceFormSchema,
  sourceIndexSchema,
  sourceUploadSchema,
  uuid,
} from './schemas'

const openSchema = z.object({ document_id: uuid })
const downloadErrorSchema = z.object({
  detail: z.object({
    code: z.string(),
    message: z.string(),
    trace_id: z.string().nullish(),
  }),
})

function DocumentActions({ id }: { id: string }) {
  const scope = useOutletContext<WorkspaceScope>()
  const actor = useWorkspaceActor()
  const index = useCommand<z.infer<typeof sourceIndexSchema>>()
  const download = useCommand<string>()
  const form = useForm<z.infer<typeof actorSchema>>({
    resolver: zodResolver(actorSchema),
    defaultValues: { actor_id: actor.actorId },
  })
  const watched = useWatch({ control: form.control })
  async function downloadContent() {
    await download.run(async () => {
      let response: Response
      try {
        response = await fetch(
          apiUrl(`/sources/${id}/content`, { company_id: scope.company_id }),
          {
            credentials: 'include',
            cache: 'no-store',
            signal: AbortSignal.timeout(30_000),
          },
        )
      } catch {
        throw new Error(
          'The source download could not reach the API. Retry when the connection is available.',
        )
      }
      if (!response.ok) {
        const error = downloadErrorSchema.safeParse(
          await response.json().catch(() => null),
        )
        if (error.success)
          throw new ApiError(
            error.data.detail.message,
            error.data.detail.code,
            false,
            error.data.detail.trace_id ?? undefined,
            response.status,
          )
        throw new Error('The source document could not be downloaded.')
      }
      const disposition = response.headers.get('Content-Disposition')
      const encodedName = disposition?.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
      let filename = 'source-document'
      if (encodedName) {
        try {
          filename = decodeURIComponent(encodedName)
        } catch {
          /* Use the generic filename when the header is malformed. */
        }
      }
      const blobUrl = URL.createObjectURL(await response.blob())
      const anchor = document.createElement('a')
      anchor.href = blobUrl
      anchor.download = filename
      anchor.click()
      window.setTimeout(() => URL.revokeObjectURL(blobUrl), 1000)
      return filename
    })
  }
  return (
    <section className={sectionClass} aria-label="Source document">
      <h2 className="text-sm font-semibold">Source document</h2>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          void downloadContent()
        }}
      >
        <Button variant="outline" type="submit" disabled={download.pending}>
          <Download />
          {download.pending ? 'Downloading...' : 'Download source content'}
        </Button>
      </form>
      <CommandError error={download.error} />
      {download.data && (
        <p role="status" className="text-sm">
          Downloaded {displayText(download.data)}
        </p>
      )}
      <form
        className="space-y-4"
        onSubmit={form.handleSubmit((values) =>
          index.run(() =>
            apiRequest(
              `/sources/${id}/index`,
              sourceIndexSchema.refine((data) => data.document_id === id),
              {
                signal: new AbortController().signal,
                timeoutMs: 120_000,
                body: {
                  company_id: scope.company_id,
                  actor_id: values.actor_id,
                },
              },
            ),
          ),
        )}
      >
        <fieldset disabled={index.pending} className="space-y-4">
          <div className="max-w-lg">
            <ActorField
              registration={form.register('actor_id')}
              value={watched.actor_id ?? ''}
              error={form.formState.errors.actor_id?.message}
            />
          </div>
          <Button variant="outline" type="submit" disabled={index.pending}>
            <ScanText />
            {index.pending ? 'Indexing...' : 'Index document'}
          </Button>
        </fieldset>
        <CommandError error={index.error} />
        {index.data && (
          <div role="status" className="space-y-1 text-sm">
            <p>
              {index.data.replayed ? 'Index already current' : 'Index complete'}
              : {index.data.indexed_count} evidence items
            </p>
            <Link className="text-emerald-700 underline" to="/assurance">
              Continue to disclosure
            </Link>
          </div>
        )}
      </form>
    </section>
  )
}

export function SourcePanel() {
  const scope = useOutletContext<WorkspaceScope>()
  const actor = useWorkspaceActor()
  const [params, setParams] = useSearchParams()
  const file = useRef<HTMLInputElement>(null)
  const upload = useCommand<z.infer<typeof sourceUploadSchema>>()
  const form = useForm<z.infer<typeof sourceFormSchema>>({
    resolver: zodResolver(sourceFormSchema),
    defaultValues: {
      actor_id: actor.actorId,
      source_name: '',
      evidence_type: '',
      custom_evidence_type: '',
      external_reference: '',
      is_synthetic: false,
    },
  })
  const watched = useWatch({ control: form.control })
  const id = params.get('document') ?? params.get('document_id')
  const open = useForm<z.infer<typeof openSchema>>({
    resolver: zodResolver(openSchema),
    defaultValues: { document_id: id ?? '' },
  })
  const openValues = useWatch({ control: open.control })
  function openDocument(documentId: string) {
    const next = new URLSearchParams(params)
    next.set('view', 'documents')
    next.set('document', documentId)
    next.delete('document_id')
    next.delete('import')
    setParams(next)
  }
  async function submit(values: z.infer<typeof sourceFormSchema>) {
    await upload.run(
      async () => {
        const selected = file.current?.files?.[0]
        if (!selected)
          throw new Error('Choose a source document before uploading.')
        const documentFile = await readSourceFile(selected)
        const source_type = values.is_synthetic
          ? 'synthetic'
          : documentFile.content_type === 'application/pdf'
            ? 'pdf'
            : documentFile.content_type === 'text/csv'
              ? 'csv'
              : documentFile.content_type === 'application/json'
                ? 'json'
                : 'api'
        return apiRequest(
          '/sources/upload',
          sourceUploadSchema.refine(
            (data) =>
              data.source.company_id === scope.company_id &&
              data.source.site_id === scope.site_id &&
              data.source.is_synthetic === values.is_synthetic &&
              data.document.data_source_id === data.source.id &&
              data.evidence.every(
                (item) => item.source_document_id === data.document.id,
              ),
          ),
          {
            signal: new AbortController().signal,
            timeoutMs: 120_000,
            body: {
              company_id: scope.company_id,
              site_id: scope.site_id,
              reporting_period_id: scope.reporting_period_id,
              actor_id: values.actor_id,
              source_name: values.source_name,
              is_synthetic: values.is_synthetic,
              evidence_type:
                values.evidence_type === 'custom'
                  ? values.custom_evidence_type
                  : values.evidence_type,
              external_reference: values.external_reference || null,
              source_type,
              ...documentFile,
            },
          },
        )
      },
      (result) => {
        open.setValue('document_id', result.document.id)
        openDocument(result.document.id)
      },
    )
  }
  return (
    <>
      <section aria-label="Upload source" className={sectionClass}>
        <h2 className="text-sm font-semibold">Upload evidence document</h2>
        <form
          onSubmit={(event) => {
            void form.handleSubmit(submit)(event)
          }}
          className="space-y-4"
        >
          <fieldset disabled={upload.pending} className="space-y-4">
            <div className={formGrid}>
              <Field
                label="Source name"
                error={form.formState.errors.source_name?.message}
              >
                <Input
                  {...form.register('source_name')}
                  required
                  maxLength={200}
                />
              </Field>
              <Field
                label="Evidence type"
                error={form.formState.errors.evidence_type?.message}
              >
                <NativeSelect {...form.register('evidence_type')} required>
                  <NativeSelectOption value="">
                    Select evidence type
                  </NativeSelectOption>
                  <NativeSelectOption value="supplier_product_declaration">
                    Supplier declaration
                  </NativeSelectOption>
                  <NativeSelectOption value="emission_factor">
                    Emission factor evidence
                  </NativeSelectOption>
                  <NativeSelectOption value="disclosure_support">
                    Disclosure support
                  </NativeSelectOption>
                  <NativeSelectOption value="source_document">
                    Source document
                  </NativeSelectOption>
                  <NativeSelectOption value="standard">
                    Disclosure standard
                  </NativeSelectOption>
                  <NativeSelectOption value="custom">
                    Other evidence category
                  </NativeSelectOption>
                </NativeSelect>
              </Field>
              {watched.evidence_type === 'custom' && (
                <Field
                  label="Evidence category"
                  error={form.formState.errors.custom_evidence_type?.message}
                >
                  <Input
                    {...form.register('custom_evidence_type')}
                    maxLength={40}
                    required
                  />
                </Field>
              )}
              <ActorField
                registration={form.register('actor_id')}
                value={watched.actor_id ?? ''}
                error={form.formState.errors.actor_id?.message}
              />
              <Field label="Source document file">
                <Input
                  ref={file}
                  type="file"
                  accept=".txt,.md,.csv,.json,.pdf"
                  required
                />
              </Field>
            </div>
            <details className="text-sm">
              <summary className="cursor-pointer font-medium">
                Additional document details
              </summary>
              <div className="mt-3 max-w-lg">
                {' '}
                <Field
                  label="External reference (optional)"
                  error={form.formState.errors.external_reference?.message}
                >
                  <Input
                    {...form.register('external_reference')}
                    maxLength={255}
                  />
                </Field>
              </div>
            </details>
            <div className="flex items-start gap-2">
              <Controller
                name="is_synthetic"
                control={form.control}
                render={({ field }) => (
                  <Checkbox
                    id="intake-source-synthetic"
                    name={field.name}
                    ref={field.ref}
                    checked={field.value}
                    onCheckedChange={(checked) =>
                      field.onChange(checked === true)
                    }
                    onBlur={field.onBlur}
                    disabled={upload.pending}
                    className="mt-0.5"
                  />
                )}
              />
              <Label
                htmlFor="intake-source-synthetic"
                className="leading-normal font-normal"
              >
                This document contains synthetic data
              </Label>
            </div>
            <p className="text-xs text-muted-foreground">
              TXT, Markdown, CSV, JSON or text-based PDF, up to 1 MiB.
            </p>
            <Button type="submit" disabled={upload.pending}>
              <FileUp />
              {upload.pending ? 'Uploading...' : 'Upload document'}
            </Button>
          </fieldset>
          <CommandError error={upload.error} />
        </form>
        {upload.data && (
          <section aria-label="Upload receipt" className="space-y-3 pt-3">
            <div className="flex flex-wrap gap-2">
              <Badge variant="outline">
                {upload.data.replayed
                  ? 'Existing document'
                  : 'Document uploaded'}
              </Badge>
              <Badge variant="outline">
                {upload.data.source.is_synthetic
                  ? 'Synthetic source'
                  : 'Non-synthetic source'}
              </Badge>
            </div>
            <p className="break-all text-sm font-medium">
              {displayText(upload.data.document.filename)}
            </p>
            <dl className="grid gap-3 text-xs sm:grid-cols-2">
              <div>
                <dt className="text-muted-foreground">Bytes</dt>
                <dd>{upload.data.decoded_size_bytes}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Evidence items</dt>
                <dd>{upload.data.evidence_count}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Document version</dt>
                <dd>{upload.data.document.version}</dd>
              </div>
            </dl>
            <p className="text-xs text-muted-foreground">
              {upload.data.document.checksum
                ? 'Integrity recorded'
                : 'Integrity missing'}
            </p>
            <Link
              className="text-sm text-emerald-700 underline"
              to="/assurance"
            >
              Continue to disclosure
            </Link>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Locator</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Use</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {upload.data.evidence.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell>{displayText(item.locator)}</TableCell>
                    <TableCell>{humanize(item.evidence_type)}</TableCell>
                    <TableCell>
                      <Link
                        className="text-emerald-700 underline dark:text-emerald-400"
                        to={`/data?view=factors&evidence=${item.id}`}
                      >
                        Register factor
                      </Link>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </section>
        )}
      </section>
      <section className={sectionClass} aria-label="Open source document">
        <h2 className="text-sm font-semibold">Open a source document</h2>
        <form
          onSubmit={open.handleSubmit((values) =>
            openDocument(values.document_id),
          )}
          className="flex flex-wrap items-start gap-3"
        >
          <div className="min-w-0 basis-80 grow">
            <Field
              label="Source document"
              error={open.formState.errors.document_id?.message}
            >
              <RecordSelect
                kind="documents"
                {...open.register('document_id')}
                value={openValues.document_id ?? ''}
                required
              />
            </Field>
          </div>
          <Button type="submit" variant="outline" className="mt-6">
            <Search />
            Open document
          </Button>
        </form>
      </section>
      {id &&
        (uuid.safeParse(id).success ? (
          <DocumentActions key={id} id={id} />
        ) : (
          <CommandError
            error={
              new Error(
                'This document link is invalid. Select a document above.',
              )
            }
          />
        ))}
    </>
  )
}

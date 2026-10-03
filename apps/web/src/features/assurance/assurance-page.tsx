import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FilePlus2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { useNavigate, useOutletContext } from 'react-router-dom'
import {
  EmptyState,
  PageControls,
  QueryRefresh,
  QueryState,
} from '@/components/query-state'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { useWorkspaceActor } from '@/features/auth/use-workspace-actor'
import { measurementQueries } from '@/features/measurements/queries'
import type { WorkspaceScope } from '@/lib/workspace'
import { apiRequest } from '@/services/api'
import { assuranceQueries, draftInScope } from './queries'
import {
  createDraftFormSchema,
  draftSchema,
  type CreateDraftForm,
} from './schemas'
import {
  CommandError,
  FieldError,
  OpenArtifact,
  WorkflowContext,
} from './workflow-ui'
import { usePayloadKey } from './use-payload-key'

function AssuranceWorkspace({ scope }: { scope: WorkspaceScope }) {
  const actor = useWorkspaceActor()
  const [offset, setOffset] = useState(0)
  const [measurementOffset, setMeasurementOffset] = useState(0)
  const standards = useQuery(assuranceQueries.standards(scope, offset))
  const measurements = useQuery(
    measurementQueries.list(scope, {
      status: 'verified',
      category: '',
      limit: 25,
      offset: measurementOffset,
    }),
  )
  const navigate = useNavigate()
  const client = useQueryClient()
  const keyFor = usePayloadKey()
  const form = useForm<CreateDraftForm>({
    resolver: zodResolver(createDraftFormSchema),
    defaultValues: {
      standard_id: '',
      measurement_id: '',
      title: '',
      requested_by: actor.actorId,
      requirement_ids: [],
    },
  })
  const selectedStandard = useWatch({
    control: form.control,
    name: 'standard_id',
  })
  const selectedRequirements = useWatch({
    control: form.control,
    name: 'requirement_ids',
  })
  const standard = standards.data?.items.find(
    (item) => item.id === selectedStandard,
  )
  useEffect(() => {
    if (actor.actorId) form.setValue('requested_by', actor.actorId)
  }, [actor.actorId, form])
  const create = useMutation({
    mutationFn: (values: CreateDraftForm) => {
      const body = {
        ...values,
        requested_by: actor.actorId || values.requested_by,
        title: values.title || null,
        company_id: scope.company_id,
        site_id: scope.site_id,
        reporting_period_id: scope.reporting_period_id,
      }
      return apiRequest(
        '/assurance/drafts',
        draftSchema.refine(
          (draft) =>
            draftInScope(draft, scope) &&
            draft.standard.id === values.standard_id &&
            draft.measurement_id === values.measurement_id,
        ),
        {
          signal: new AbortController().signal,
          method: 'POST',
          body: { ...body, idempotency_key: keyFor(body) },
        },
      )
    },
    retry: false,
    onSuccess: (draft) => {
      client.setQueryData(
        assuranceQueries.draft(scope, draft.id).queryKey,
        draft,
      )
      navigate(`/assurance/${draft.id}`)
    },
  })
  return (
    <main className="min-w-0 space-y-6 px-5 py-6 sm:px-8">
      <header>
        <h1 className="text-2xl font-semibold">Assurance</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          POC disclosure drafts; not assurance opinions or filings.
        </p>
      </header>
      <WorkflowContext scope={scope} />
      <section aria-label="Disclosure standards" className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold">
            Standards and requirements
          </h2>
          <QueryRefresh query={standards} label="standards" />
        </div>
        <QueryState query={standards}>
          {(page) => (
            <>
              {!page.items.length && (
                <EmptyState
                  title="No active standards"
                  detail="No disclosure standard is available for this company."
                />
              )}
              {page.items.map((item) => (
                <details key={item.id} className="border-b py-3">
                  <summary className="cursor-pointer text-sm font-medium">
                    {item.name}{' '}
                    <span className="text-muted-foreground">
                      {item.code} / {item.version}
                    </span>
                  </summary>
                  <p className="my-3 text-sm text-muted-foreground">
                    {item.description}
                  </p>
                  <ul className="divide-y">
                    {item.requirements.map((req) => (
                      <li key={req.id} className="space-y-1 py-3 text-sm">
                        <p className="font-medium">
                          {req.requirement_code}: {req.title}
                        </p>
                        <p>{req.description}</p>
                        <p className="text-xs text-muted-foreground">
                          {req.is_required ? 'Required' : 'Optional'} /{' '}
                          {req.is_active ? 'Active' : 'Inactive'} / Minimum
                          confidence: {req.minimum_confidence}
                        </p>
                      </li>
                    ))}
                  </ul>
                </details>
              ))}
              <PageControls
                {...page}
                count={page.items.length}
                onChange={(next) => {
                  setOffset(next)
                  form.setValue('standard_id', '')
                  form.setValue('requirement_ids', [])
                }}
              />
            </>
          )}
        </QueryState>
      </section>
      <section
        aria-label="Create disclosure draft"
        className="space-y-4 border-t pt-5"
      >
        <h2 className="text-base font-semibold">Create disclosure draft</h2>
        <form
          aria-label="Create disclosure draft"
          onSubmit={form.handleSubmit((values) => create.mutate(values))}
          className="space-y-4"
        >
          <fieldset
            disabled={create.isPending}
            className="grid min-w-0 gap-4 md:grid-cols-2"
          >
            <div className="min-w-0">
              <label
                htmlFor="assurance-standard"
                className="mb-2 block text-xs font-medium"
              >
                Standard
              </label>
              <NativeSelect
                id="assurance-standard"
                className="w-full"
                required
                {...form.register('standard_id', {
                  onChange: () => form.setValue('requirement_ids', []),
                })}
              >
                <NativeSelectOption value="">
                  Select a standard
                </NativeSelectOption>
                {standards.data?.items
                  .filter((item) => item.is_active)
                  .map((item) => (
                    <NativeSelectOption key={item.id} value={item.id}>
                      {item.name} / {item.version}
                    </NativeSelectOption>
                  ))}
              </NativeSelect>
              <FieldError
                message={form.formState.errors.standard_id?.message}
              />
            </div>
            <div className="min-w-0">
              <label
                htmlFor="assurance-measurement"
                className="mb-2 block text-xs font-medium"
              >
                Verified measurement
              </label>
              <QueryState query={measurements}>
                {(page) => (
                  <>
                    <NativeSelect
                      id="assurance-measurement"
                      className="w-full"
                      required
                      {...form.register('measurement_id')}
                    >
                      <NativeSelectOption value="">
                        Select a verified measurement
                      </NativeSelectOption>
                      {page.items.map((item) => (
                        <NativeSelectOption key={item.id} value={item.id}>
                          {item.metric_key} / {item.value_kgco2e} {item.unit} /{' '}
                          {item.id}
                        </NativeSelectOption>
                      ))}
                    </NativeSelect>
                    {!page.items.length && (
                      <p className="mt-2 text-xs text-muted-foreground">
                        No verified measurements in this workspace.
                      </p>
                    )}
                    <PageControls
                      {...page}
                      count={page.items.length}
                      onChange={(next) => {
                        setMeasurementOffset(next)
                        form.setValue('measurement_id', '')
                      }}
                    />
                  </>
                )}
              </QueryState>
              <FieldError
                message={form.formState.errors.measurement_id?.message}
              />
            </div>
            <div>
              <label
                htmlFor="assurance-title"
                className="mb-2 block text-xs font-medium"
              >
                Draft title (optional)
              </label>
              <Input
                id="assurance-title"
                maxLength={255}
                {...form.register('title')}
              />
              <FieldError message={form.formState.errors.title?.message} />
            </div>
            <div>
              <label
                htmlFor="assurance-actor"
                className="mb-2 block text-xs font-medium"
              >
                Requesting actor UUID
              </label>
              <Input
                id="assurance-actor"
                readOnly={!!actor.actorId}
                required
                {...form.register('requested_by')}
              />
              <FieldError
                message={form.formState.errors.requested_by?.message}
              />
            </div>
          </fieldset>
          {standard && (
            <fieldset disabled={create.isPending} className="space-y-3">
              <legend className="mb-3 text-sm font-medium">
                Included requirements
              </legend>
              {standard.requirements
                .filter((item) => item.is_active)
                .map((item) => (
                  <Label
                    key={item.id}
                    htmlFor={`assurance-requirement-${item.id}`}
                    className="flex items-start gap-2 text-sm leading-relaxed font-normal"
                  >
                    <Checkbox
                      id={`assurance-requirement-${item.id}`}
                      className="mt-0.5"
                      disabled={item.is_required || create.isPending}
                      checked={
                        !selectedRequirements.length ||
                        selectedRequirements.includes(item.id)
                      }
                      onCheckedChange={(checked) => {
                        const current = selectedRequirements.length
                          ? selectedRequirements
                          : standard.requirements
                              .filter((req) => req.is_active)
                              .map((req) => req.id)
                        const next =
                          checked === true
                            ? [...current, item.id]
                            : current.filter((id) => id !== item.id)
                        if (!next.length) {
                          form.setError('requirement_ids', {
                            message:
                              'At least one requirement must remain selected.',
                          })
                          return
                        }
                        form.clearErrors('requirement_ids')
                        form.setValue('requirement_ids', next)
                      }}
                    />
                    <span>
                      {item.requirement_code}: {item.title}
                      {item.is_required ? ' (required)' : ' (optional)'}
                    </span>
                  </Label>
                ))}
              <FieldError
                message={form.formState.errors.requirement_ids?.message}
              />
            </fieldset>
          )}
          <CommandError error={create.error} />
          <Button
            type="submit"
            disabled={
              create.isPending ||
              !standards.data?.items.length ||
              !measurements.data?.items.length ||
              standards.isError ||
              measurements.isError
            }
          >
            <FilePlus2 />
            {create.isPending ? 'Creating draft...' : 'Create draft'}
          </Button>
        </form>
      </section>
      <OpenArtifact kind="Draft" path="/assurance" />
    </main>
  )
}
export default function AssurancePage() {
  const scope = useOutletContext<WorkspaceScope>()
  return (
    <AssuranceWorkspace
      key={`${scope.company_id}:${scope.site_id}:${scope.reporting_period_id}`}
      scope={scope}
    />
  )
}

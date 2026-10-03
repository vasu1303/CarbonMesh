import { zodResolver } from '@hookform/resolvers/zod'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plug, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { Link, useOutletContext } from 'react-router-dom'
import type { z } from 'zod'

import { EmptyState, QueryRefresh, QueryState } from '@/components/query-state'
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
import {
  CommandError,
  Field,
  WorkspaceProvenance,
  formGrid,
  sectionClass,
} from '@/features/data/intake-ui'
import { useCommand } from '@/features/data/commands'
import type { WorkspaceScope } from '@/lib/workspace'
import { apiRequest, retryApiQuery } from '@/services/api'
import {
  databaseSchema,
  gridFormSchema,
  gridSyncSchema,
  healthSchema,
  latestGridSchema,
  providerFormSchema,
  providerSchema,
} from './schemas'
import { ResetPanel } from './reset-panel'

function Diagnostics() {
  const health = useQuery({
    queryKey: ['system-health'],
    queryFn: ({ signal }) => apiRequest('/health', healthSchema, { signal }),
    retry: retryApiQuery,
    staleTime: 30_000,
  })
  const readiness = useQuery({
    queryKey: ['system-readiness'],
    queryFn: ({ signal }) =>
      apiRequest('/health/ready', healthSchema, { signal }),
    retry: retryApiQuery,
    staleTime: 30_000,
  })
  const database = useQuery({
    queryKey: ['system-database'],
    queryFn: ({ signal }) => apiRequest('/db/demo', databaseSchema, { signal }),
    retry: retryApiQuery,
    staleTime: 30_000,
  })
  return (
    <section
      aria-label="System diagnostics"
      className="grid gap-6 border-b pb-6 lg:grid-cols-3"
    >
      <section aria-label="API availability">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">API availability</h2>
          <QueryRefresh query={health} label="API availability" />
        </div>
        <QueryState query={health}>
          {(data) => (
            <div className="space-y-2 text-sm">
              <Badge variant="outline">{data.status}</Badge>
              <p>{data.service}</p>
            </div>
          )}
        </QueryState>
      </section>
      <section aria-label="Database readiness">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Database readiness</h2>
          <QueryRefresh query={readiness} label="database readiness" />
        </div>
        <QueryState query={readiness}>
          {(data) => (
            <div className="space-y-2 text-sm">
              <Badge variant="outline">{data.status}</Badge>
              <p>Schema contract verified by the API</p>
            </div>
          )}
        </QueryState>
      </section>
      <section aria-label="Database connection">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Database connection</h2>
          <QueryRefresh query={database} label="database connection" />
        </div>
        <QueryState query={database}>
          {(data) => (
            <div className="space-y-2 text-sm">
              <Badge variant="outline">
                {data.connected ? 'Connected' : 'Disconnected'}
              </Badge>
              <p className="break-all text-xs">
                Database time: {data.database_time}
              </p>
              <p className="text-xs text-muted-foreground">{data.message}</p>
            </div>
          )}
        </QueryState>
      </section>
    </section>
  )
}

function GridIntegration() {
  const scope = useOutletContext<WorkspaceScope>()
  const client = useQueryClient()
  const test = useCommand<z.infer<typeof providerSchema>>()
  const sync = useCommand<z.infer<typeof gridSyncSchema>>()
  const providerForm = useForm<z.infer<typeof providerFormSchema>>({
    resolver: zodResolver(providerFormSchema),
    defaultValues: { max_zones: '25' },
  })
  const gridForm = useForm<z.infer<typeof gridFormSchema>>({
    resolver: zodResolver(gridFormSchema),
    defaultValues: {
      zone: '',
      start: '',
      end: '',
      lookback_hours: '24',
      disable_estimations: false,
    },
  })
  const latest = useQuery({
    queryKey: ['system-grid', scope.company_id, scope.site_id],
    queryFn: ({ signal }) =>
      apiRequest(
        '/measurement/grid/latest',
        latestGridSchema.refine((data) => data.site_id === scope.site_id),
        {
          signal,
          params: { company_id: scope.company_id, site_id: scope.site_id },
        },
      ),
    retry: retryApiQuery,
    staleTime: 30_000,
  })
  return (
    <>
      <section aria-label="Grid cache" className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Latest cached grid point</h2>
          <QueryRefresh query={latest} label="cached grid" />
        </div>
        <QueryState query={latest} emptyCode="grid_intensity_not_found">
          {(data) => (
            <div className="space-y-3 text-sm">
              <div className="flex flex-wrap gap-2">
                <Badge variant="outline">
                  {data.provenance.synthetic
                    ? 'Synthetic grid source'
                    : 'Non-synthetic grid source'}
                </Badge>
                <Badge variant="outline">{data.provenance.provider_mode}</Badge>
                <Badge variant="outline">
                  {data.is_estimated ? 'Estimated' : 'Reported'}
                </Badge>
              </div>
              <p>
                <span className="font-mono">{data.value}</span> {data.unit} /{' '}
                {data.zone}
              </p>
              <p className="text-xs text-muted-foreground">
                Provider time: {data.provider_timestamp} / Cached:{' '}
                {data.provenance.retrieved_at}
              </p>
              <Link
                className="text-emerald-700 underline dark:text-emerald-400"
                to={`/data?document=${data.provenance.source_document_id}`}
              >
                Open provider source document
              </Link>
            </div>
          )}
        </QueryState>
      </section>
      <section
        aria-label="Electricity Maps connection"
        className={sectionClass}
      >
        <h2 className="text-sm font-semibold">Electricity Maps connection</h2>
        <form
          className="space-y-4"
          onSubmit={providerForm.handleSubmit((values) =>
            test.run(() =>
              apiRequest(
                '/integrations/electricity-maps/test',
                providerSchema,
                {
                  signal: new AbortController().signal,
                  method: 'POST',
                  timeoutMs: 60_000,
                  params: { max_zones: Number(values.max_zones) },
                },
              ),
            ),
          )}
        >
          <fieldset disabled={test.pending} className="space-y-4">
            <div className={formGrid}>
              <Field
                label="Maximum zones"
                error={providerForm.formState.errors.max_zones?.message}
              >
                <Input
                  {...providerForm.register('max_zones')}
                  type="number"
                  min={1}
                  max={100}
                  required
                />
              </Field>
            </div>
            <p className="text-xs text-muted-foreground">
              Tests the live provider using server-managed credentials.
            </p>
            <Button type="submit" variant="outline" disabled={test.pending}>
              <Plug />
              {test.pending ? 'Testing...' : 'Test provider connection'}
            </Button>
          </fieldset>
          <CommandError error={test.error} />
        </form>
        {test.data && (
          <div aria-label="Provider test result" className="space-y-3 text-sm">
            <p>
              {test.data.provider} / {test.data.api_version}:{' '}
              {test.data.authenticated ? 'Authenticated' : 'Not authenticated'}
            </p>
            <p>Accessible zones: {test.data.accessible_zone_count}</p>
            {test.data.zones.length ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Zone</TableHead>
                    <TableHead>Name</TableHead>
                    <TableHead>Available endpoints</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {test.data.zones.map((zone) => (
                    <TableRow key={zone.zone}>
                      <TableCell>{zone.zone}</TableCell>
                      <TableCell>{zone.zone_name}</TableCell>
                      <TableCell className="whitespace-normal">
                        {zone.accessible_endpoints.join(', ') ||
                          'None reported'}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <EmptyState
                title="No accessible zones returned"
                detail="The provider did not return zones for this account."
              />
            )}
            {test.data.zones_truncated && (
              <p className="text-xs text-muted-foreground">
                The provider response is truncated to the requested limit.
              </p>
            )}
          </div>
        )}
      </section>
      <section
        aria-label="Grid history synchronization"
        className={sectionClass}
      >
        <h2 className="text-sm font-semibold">
          Sync historical grid intensity
        </h2>
        <form
          className="space-y-4"
          onSubmit={gridForm.handleSubmit((values) =>
            sync.run(
              () =>
                apiRequest(
                  '/measurement/grid/history/sync',
                  gridSyncSchema.refine(
                    (data) => data.site_id === scope.site_id,
                  ),
                  {
                    signal: new AbortController().signal,
                    timeoutMs: 120_000,
                    params: {
                      company_id: scope.company_id,
                      site_id: scope.site_id,
                    },
                    body: {
                      mode: 'live',
                      zone: values.zone || null,
                      start: values.start || null,
                      end: values.end || null,
                      lookback_hours: Number(values.lookback_hours),
                      disable_estimations: values.disable_estimations,
                    },
                  },
                ),
              () => {
                void client.invalidateQueries({ queryKey: ['system-grid'] })
                void client.invalidateQueries({ queryKey: ['intake-factors'] })
              },
            ),
          )}
        >
          <fieldset disabled={sync.pending} className="space-y-4">
            <div className={formGrid}>
              <Field
                label="Grid zone (optional)"
                error={gridForm.formState.errors.zone?.message}
              >
                <Input {...gridForm.register('zone')} maxLength={100} />
              </Field>
              <Field
                label="Lookback hours"
                error={gridForm.formState.errors.lookback_hours?.message}
              >
                <Input
                  {...gridForm.register('lookback_hours')}
                  type="number"
                  min={1}
                  max={240}
                  required
                />
              </Field>
              <Field
                label="Range start (optional, ISO offset)"
                error={gridForm.formState.errors.start?.message}
              >
                <Input
                  {...gridForm.register('start')}
                  placeholder="YYYY-MM-DDTHH:mm:ssZ"
                />
              </Field>
              <Field
                label="Range end (ISO offset)"
                error={gridForm.formState.errors.end?.message}
              >
                <Input
                  {...gridForm.register('end')}
                  placeholder="YYYY-MM-DDTHH:mm:ssZ"
                />
              </Field>
            </div>
            <div className="flex items-start gap-2">
              <Controller
                name="disable_estimations"
                control={gridForm.control}
                render={({ field }) => (
                  <Checkbox
                    id="system-grid-disable-estimations"
                    name={field.name}
                    ref={field.ref}
                    checked={field.value}
                    onCheckedChange={(checked) =>
                      field.onChange(checked === true)
                    }
                    onBlur={field.onBlur}
                    disabled={sync.pending}
                    className="mt-0.5"
                  />
                )}
              />
              <Label
                htmlFor="system-grid-disable-estimations"
                className="leading-normal font-normal"
              >
                Exclude estimated provider values
              </Label>
            </div>
            <p className="text-xs text-muted-foreground">
              Live provider only. Maximum range: 240 hours. A range overrides
              lookback. An omitted zone is resolved by the server for the
              current site. Sync writes provider snapshots and grid points.
            </p>
            <Button type="submit" variant="outline" disabled={sync.pending}>
              <RefreshCw />
              {sync.pending ? 'Synchronizing...' : 'Sync grid history'}
            </Button>
          </fieldset>
          <CommandError error={sync.error} />
        </form>
        {sync.data && (
          <div aria-label="Grid sync result" className="space-y-3 text-sm">
            <p role="status">History synchronized for {sync.data.zone}</p>
            <p className="text-xs text-muted-foreground">
              {sync.data.requested_start} to {sync.data.requested_end}
            </p>
            <dl className="grid gap-3 sm:grid-cols-4">
              {[
                ['Received', sync.data.received_points],
                ['Inserted', sync.data.inserted_points],
                ['Existing', sync.data.existing_points],
                ['Estimated', sync.data.estimated_points],
              ].map(([label, value]) => (
                <div key={label}>
                  <dt className="text-xs text-muted-foreground">{label}</dt>
                  <dd className="font-mono">{value}</dd>
                </div>
              ))}
            </dl>
            <p className="break-all font-mono text-xs">
              Checksum: {sync.data.response_checksum}
            </p>
            <div className="flex flex-wrap gap-4">
              <Link
                className="text-emerald-700 underline dark:text-emerald-400"
                to={`/data?document=${sync.data.source_document_id}`}
              >
                Open synced source
              </Link>
              <Link
                className="text-emerald-700 underline dark:text-emerald-400"
                to="/measurement"
              >
                Calculate measurement
              </Link>
            </div>
          </div>
        )}
      </section>
    </>
  )
}

export default function DemoPage() {
  const scope = useOutletContext<WorkspaceScope>()
  const [resetVersion, setResetVersion] = useState(0)
  return (
    <div className="min-w-0 space-y-5 px-5 py-6 sm:px-8">
      <header>
        <h1 className="text-2xl font-semibold">System</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Readiness, grid integration and guarded demo operations
        </p>
      </header>
      <div key={resetVersion} className="contents">
        <WorkspaceProvenance />
        <Diagnostics />
        <GridIntegration key={`${scope.company_id}:${scope.site_id}`} />
      </div>
      <ResetPanel onReset={() => setResetVersion(value => value + 1)} />
    </div>
  )
}

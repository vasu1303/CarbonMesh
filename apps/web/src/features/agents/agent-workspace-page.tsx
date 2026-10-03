import { useMutation, useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useOutletContext } from 'react-router-dom'
import { QueryState } from '@/components/query-state'
import { Badge } from '@/components/ui/badge'
import type { WorkspaceScope } from '@/lib/workspace'
import { apiRequest } from '@/services/api'
import { OpenRunForm } from '@/features/runs/open-run-form'
import { AgentContextForm } from './context-form'
import { contextOptions } from './queries'
import {
  acceptedSchema,
  agentRequestSchema,
  type AgentRequest,
} from './schemas'

export default function AgentWorkspacePage() {
  const scope = useOutletContext<WorkspaceScope>()
  const context = useQuery(contextOptions(scope))
  const navigate = useNavigate()
  const command = useMutation({
    mutationFn: (request: AgentRequest) =>
      apiRequest('/agent/requests', acceptedSchema, {
        signal: new AbortController().signal,
        method: 'POST',
        body: agentRequestSchema.parse(request),
      }),
    retry: false,
    onSuccess: (result) => navigate(`/runs/${result.run_id}`),
  })
  return (
    <main className="min-w-0 space-y-6 px-5 py-6 sm:px-8">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="mb-2 text-xs font-medium text-muted-foreground">
            Agent workspace
          </p>
          <h1 className="text-2xl font-semibold">Ask CarbonMesh</h1>
        </div>
        <Link
          to="/runs"
          className="text-sm text-emerald-700 underline underline-offset-4 dark:text-emerald-400"
        >
          Open a run
        </Link>
      </header>
      <section aria-label="Request context" className="border-y py-3">
        <QueryState query={context}>
          {(data) => (
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <strong>{data.company.name}</strong>
              <span>{data.site.name}</span>
              <span>{data.reporting_period.name}</span>
              <Badge variant="outline">
                {data.company.is_synthetic
                  ? 'Synthetic data / API'
                  : 'Non-synthetic data / API'}
              </Badge>
            </div>
          )}
        </QueryState>
        {!context.data && (
          <p className="text-xs text-muted-foreground">
            Provenance unavailable until context resolves.
          </p>
        )}
      </section>
      <div className="grid min-w-0 gap-8 xl:grid-cols-[minmax(0,1fr)_17rem]">
        <section className="min-w-0" aria-label="New run">
          <AgentContextForm
            key={`${scope.company_id}-${scope.site_id}-${scope.reporting_period_id}`}
            scope={scope}
            pending={command.isPending}
            error={command.error}
            disabled={!context.data || context.isError}
            onSubmit={(context, query, previous_run_id) =>
              command.mutate({ context, query: query!, previous_run_id })
            }
          />
          {command.isError && (
            <p className="mt-3 text-xs text-muted-foreground">
              A failed response may still have created a run. Creation has no
              server idempotency key; submitting again starts a new request.
            </p>
          )}
        </section>
        <aside className="min-w-0 border-t pt-5 xl:border-t-0 xl:border-l xl:pl-6 xl:pt-0">
          <h2 className="mb-4 text-base font-semibold">Known run</h2>
          <OpenRunForm />
          <p className="mt-6 text-sm text-muted-foreground">
            Consequential outputs require human review in Approvals. Dispatch
            remains advisory.
          </p>
        </aside>
      </div>
    </main>
  )
}

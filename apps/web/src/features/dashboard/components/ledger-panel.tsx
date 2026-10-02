import { useQuery } from '@tanstack/react-query'
import { ArrowUpRight, GitCommitHorizontal } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { formatDate, humanize } from '../format'
import { dashboardQueries, type DashboardScope } from '../queries'
import { EmptyState, QueryRefresh, QueryState } from './query-state'

export function LedgerPanel({
  scope,
  onInspect,
}: {
  scope: DashboardScope
  onInspect: (id: string) => void
}) {
  const query = useQuery(dashboardQueries.ledger(scope))
  return (
    <section
      id="ledger"
      aria-labelledby="ledger-title"
      className="min-w-0 p-5 sm:p-6"
    >
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h2 id="ledger-title" className="text-sm font-semibold">
            Recent ledger activity
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Company-wide append-only history
          </p>
        </div>
        <QueryRefresh query={query} label="ledger activity" />
      </div>
      <QueryState query={query}>
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState
              title="No ledger events"
              detail="No events have been recorded for this company."
            />
          ) : (
            <>
              <ul className="divide-y">
                {page.items.map((event) => (
                  <li key={event.id} className="flex items-center gap-3 py-3">
                    <GitCommitHorizontal className="size-4 shrink-0 text-muted-foreground" />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium capitalize break-words">
                        {humanize(event.event_type)}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {formatDate(event.created_at)}
                      </p>
                    </div>
                    <Button
                      size="icon"
                      variant="ghost"
                      className="size-8 shrink-0"
                      aria-label={`Inspect event ${event.id}`}
                      title="Inspect event and evidence"
                      onClick={() => onInspect(event.id)}
                    >
                      <ArrowUpRight />
                    </Button>
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-xs text-muted-foreground">
                Latest {page.items.length} of {page.total} events
              </p>
            </>
          )
        }
      </QueryState>
    </section>
  )
}

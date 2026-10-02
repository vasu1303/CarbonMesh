import { useQuery } from '@tanstack/react-query'
import { ArrowUpRight } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { formatDate, formatDecimal, humanize } from '../format'
import { dashboardQueries, type DashboardScope } from '../queries'
import type { Approval } from '../schemas'
import {
  EmptyState,
  PageControls,
  QueryRefresh,
  QueryState,
} from './query-state'

function approvalStatus(item: Approval, now: number) {
  if (!item.preview_current) return 'Stale preview'
  if (item.expired || Date.parse(item.expires_at) <= now)
    return 'Expired preview'
  return 'Pending review'
}

export function ApprovalsPanel({
  scope,
  onInspect,
}: {
  scope: DashboardScope
  onInspect: (item: Approval) => void
}) {
  const [offset, setOffset] = useState(0)
  const [now, setNow] = useState(Date.now)
  const query = useQuery(dashboardQueries.approvals(scope, offset))
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  return (
    <section
      id="approvals"
      aria-labelledby="approvals-title"
      className="min-w-0 p-5 sm:p-6"
    >
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h2 id="approvals-title" className="text-sm font-semibold">
            Approval review queue
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Company-wide pending previews, including stale and expired items
          </p>
        </div>
        <QueryRefresh query={query} label="approval previews" />
      </div>
      <QueryState query={query}>
        {(page) => (
          <>
            {page.items.length === 0 ? (
              <EmptyState
                title="No pending approval previews"
                detail="No decision is waiting in this queue."
              />
            ) : (
              <ul className="divide-y">
                {page.items.map((item) => {
                  const state = approvalStatus(item, now)
                  return (
                    <li key={item.id} className="py-4 first:pt-0">
                      <div className="mb-2 flex flex-wrap items-center gap-2">
                        <Badge
                          variant="outline"
                          className={
                            state === 'Pending review'
                              ? 'border-amber-300 text-amber-700 dark:border-amber-800 dark:text-amber-400'
                              : 'text-muted-foreground'
                          }
                        >
                          {state}
                        </Badge>
                        <span className="text-xs text-muted-foreground">
                          {humanize(item.target_type)}
                        </span>
                      </div>
                      <h3 className="text-sm font-medium break-words">
                        {item.recommended_product_name ??
                          humanize(item.target_type)}
                      </h3>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {item.supplier_name ??
                          `Requested by ${item.requester_name}`}
                      </p>
                      <dl className="my-4 grid grid-cols-2 gap-4 text-xs">
                        {item.avoided_kgco2e != null && (
                          <div>
                            <dt className="text-muted-foreground">
                              Avoided kgCO2e
                            </dt>
                            <dd className="mt-1 break-all font-mono text-base tabular-nums">
                              {formatDecimal(item.avoided_kgco2e)}
                            </dd>
                          </div>
                        )}
                        {item.cost_delta_pct != null && (
                          <div>
                            <dt className="text-muted-foreground">
                              Cost change
                            </dt>
                            <dd className="mt-1 font-mono text-base tabular-nums">
                              {formatDecimal(item.cost_delta_pct)}%
                            </dd>
                          </div>
                        )}
                      </dl>
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="text-xs text-muted-foreground">
                          Expires {formatDate(item.expires_at)}
                        </p>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => onInspect(item)}
                        >
                          Inspect preview
                          <ArrowUpRight />
                        </Button>
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
            {page.total > 0 && (
              <PageControls
                {...page}
                count={page.items.length}
                onChange={setOffset}
              />
            )}
          </>
        )}
      </QueryState>
    </section>
  )
}

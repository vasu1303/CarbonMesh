import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { displayText, humanize } from '@/lib/presentation'
import { dashboardQueries, type DashboardScope } from '../queries'
import { EmptyState, QueryRefresh, QueryState } from './query-state'

export function QualityPanel({ scope }: { scope: DashboardScope }) {
  const [severity, setSeverity] = useState('all')
  const query = useQuery(
    dashboardQueries.issues(scope, severity === 'all' ? undefined : severity),
  )

  return (
    <section
      id="quality"
      aria-labelledby="quality-title"
      className="min-w-0 p-5 sm:p-6"
    >
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h2 id="quality-title" className="text-sm font-semibold">
            Data quality
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Open issues across the company
          </p>
        </div>
        <QueryRefresh query={query} label="quality issues" />
      </div>
      <Tabs value={severity} onValueChange={setSeverity}>
        <TabsList aria-label="Issue severity" className="mb-3 w-full">
          {['all', 'error', 'warning', 'info'].map((value) => (
            <TabsTrigger
              key={value}
              value={value}
              className="flex-1 text-xs capitalize"
            >
              {value === 'all' ? 'All' : value}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      <QueryState query={query}>
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState
              title="No open issues"
              detail={
                severity === 'all'
                  ? 'The company has no reported open issues.'
                  : 'No open issues match this severity.'
              }
            />
          ) : (
            <>
              <ul className="divide-y">
                {page.items.map((issue) => (
                  <li key={issue.id} className="space-y-2 py-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge
                        variant={
                          issue.severity === 'error'
                            ? 'destructive'
                            : 'secondary'
                        }
                      >
                        {issue.severity}
                      </Badge>
                      <span className="break-words text-xs text-muted-foreground">
                        {humanize(issue.code)}
                      </span>
                    </div>
                    <p className="text-sm break-words">
                      {displayText(issue.message)}
                    </p>
                    {issue.row_number !== null && (
                      <p className="text-xs text-muted-foreground">
                        Source row {issue.row_number}
                        {issue.field_name
                          ? ` / ${humanize(issue.field_name)}`
                          : ''}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-xs text-muted-foreground">
                Showing {page.items.length} of {page.total} open{' '}
                {severity === 'all' ? 'issues' : `${severity} issues`}
              </p>
            </>
          )
        }
      </QueryState>
    </section>
  )
}

import {
  useIsFetching,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from '@tanstack/react-query'
import {
  AlertTriangle,
  ArrowRight,
  ClipboardCheck,
  Database,
  Leaf,
  PackageSearch,
  RefreshCw,
  ShieldCheck,
  Zap,
  type LucideIcon,
} from 'lucide-react'
import { lazy, Suspense, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ApprovalsPanel } from './components/approvals-panel'
import { LedgerPanel } from './components/ledger-panel'
import { MeasurementsPanel } from './components/measurements-panel'
import { QualityPanel } from './components/quality-panel'
import { QueryState } from '@/components/query-state'
import type { Inspection } from './components/source-inspector'
import { formatDecimal } from '@/lib/format'
import { dashboardKey, dashboardQueries, type DashboardScope } from './queries'

const SourceInspector = lazy(() =>
  import('./components/source-inspector').then((module) => ({
    default: module.SourceInspector,
  })),
)

function SummaryCard<T>({
  title,
  icon: Icon,
  query,
  children,
}: {
  title: string
  icon: LucideIcon
  query: UseQueryResult<T, Error>
  children: (data: T) => ReactNode
}) {
  return (
    <Card className="min-w-0 rounded-lg">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
          <Icon className="size-4" />
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <QueryState query={query}>{children}</QueryState>
      </CardContent>
    </Card>
  )
}

const workflow = [
  { label: 'Upload data', path: '/data', icon: Database },
  { label: 'Check data', path: '/quality', icon: AlertTriangle },
  { label: 'Measure', path: '/measurement', icon: Leaf },
  { label: 'Plan', path: '/procurement/suppliers', icon: PackageSearch },
  { label: 'Review', path: '/approvals', icon: ClipboardCheck },
]

export function DashboardPage({ scope }: { scope: DashboardScope }) {
  const context = useQuery(dashboardQueries.context(scope))
  const measurements = useQuery(dashboardQueries.measurements(scope))
  const issues = useQuery(dashboardQueries.issues(scope))
  const approvals = useQuery(dashboardQueries.approvals(scope))
  const suppliers = useQuery(dashboardQueries.suppliers(scope))
  const fetching = useIsFetching({ queryKey: dashboardKey }) > 0
  const client = useQueryClient()
  const [selection, setSelection] = useState<Inspection | null>(null)
  const inspectMeasurement = (id: string) =>
    setSelection({ kind: 'measurement', id })

  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-4 px-5 py-6 sm:px-8">
        <div>
          <p className="mb-2 text-xs font-medium text-muted-foreground">
            Carbon operations
          </p>
          <h1 className="text-2xl font-semibold">Overview</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Your emissions, open checks and decisions.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button asChild size="sm">
            <Link to="/data">
              <Database />
              Upload data
            </Link>
          </Button>
          <Button
            variant="outline"
            size="icon"
            aria-label="Refresh dashboard"
            title="Refresh dashboard"
            disabled={fetching}
            onClick={() =>
              void client.invalidateQueries({ queryKey: dashboardKey })
            }
          >
            <RefreshCw className={fetching ? 'motion-safe:animate-spin' : ''} />
          </Button>
        </div>
      </div>

      <section
        aria-label="Reporting context"
        className="mx-5 mb-6 border-y py-3 sm:mx-8"
      >
        <QueryState query={context}>
          {(data) => (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
              <span className="font-medium">{data.company.name}</span>
              <span className="text-muted-foreground">{data.site.name}</span>
              <span className="text-muted-foreground">
                {data.reporting_period.name}
              </span>
              <Badge variant="outline" className="sm:ml-auto">
                {data.company.is_synthetic
                  ? 'Synthetic data'
                  : 'Non-synthetic data'}
              </Badge>
            </div>
          )}
        </QueryState>
        {!context.data && (
          <p className="mt-2 text-xs text-muted-foreground">
            Workspace details are unavailable. Reporting data will load
            independently.
          </p>
        )}
      </section>

      <nav
        aria-label="Carbon workflow"
        className="mx-5 mb-6 grid grid-cols-2 gap-2 border-b pb-5 sm:mx-8 sm:grid-cols-5"
      >
        {workflow.map(({ label, path, icon: Icon }, index) => (
          <Link
            key={path}
            to={path}
            className="group flex min-w-0 items-center gap-2 rounded-md px-2 py-3 text-sm font-medium transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
          >
            <span className="flex size-7 shrink-0 items-center justify-center rounded-full border text-xs text-muted-foreground">
              {index + 1}
            </span>
            <span className="min-w-0 flex-1">{label}</span>
            <Icon className="size-4 shrink-0 text-muted-foreground" />
          </Link>
        ))}
      </nav>

      <div className="grid grid-cols-1 gap-3 px-5 pb-6 sm:grid-cols-2 sm:px-8 xl:grid-cols-4">
        <SummaryCard
          title="Latest verified result"
          icon={Leaf}
          query={measurements}
        >
          {(data) =>
            data.items[0] ? (
              <>
                <Button
                  variant="link"
                  className="h-auto max-w-full justify-start p-0 text-left font-mono text-2xl font-semibold whitespace-normal break-all tabular-nums"
                  onClick={() => inspectMeasurement(data.items[0].id)}
                >
                  {formatDecimal(data.items[0].value_kgco2e)}
                </Button>
                <p className="mt-2 text-xs text-muted-foreground">
                  kgCO2e / latest verified measurement
                </p>
              </>
            ) : (
              <>
                <p className="text-lg font-medium">No verified result</p>
                <p className="mt-2 text-xs text-muted-foreground">
                  Selected site and period
                </p>
              </>
            )
          }
        </SummaryCard>
        <SummaryCard
          title="Open data issues"
          icon={AlertTriangle}
          query={issues}
        >
          {(data) => (
            <>
              <p className="font-mono text-2xl font-semibold tabular-nums">
                {data.total}
              </p>
              <p className="mt-2 text-xs text-muted-foreground">
                Awaiting review across your company
              </p>
            </>
          )}
        </SummaryCard>
        <SummaryCard
          title="Pending approvals"
          icon={ClipboardCheck}
          query={approvals}
        >
          {(data) => (
            <>
              <p className="font-mono text-2xl font-semibold tabular-nums">
                {data.total}
              </p>
              <p className="mt-2 text-xs text-muted-foreground">
                Pending review, including expired previews
              </p>
            </>
          )}
        </SummaryCard>
        <SummaryCard
          title="Active suppliers"
          icon={PackageSearch}
          query={suppliers}
        >
          {(data) => (
            <>
              <p className="font-mono text-2xl font-semibold tabular-nums">
                {data.total}
              </p>
              <p className="mt-2 text-xs text-muted-foreground">
                Company-wide supplier catalog
              </p>
            </>
          )}
        </SummaryCard>
      </div>

      <div className="border-y bg-card lg:grid lg:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)] lg:divide-x">
        <MeasurementsPanel scope={scope} onInspect={inspectMeasurement} />
        <section
          aria-labelledby="workflows-title"
          className="min-w-0 border-t p-5 sm:p-6 lg:border-t-0"
        >
          <h2 id="workflows-title" className="mb-2 text-sm font-semibold">
            Continue your work
          </h2>
          <div className="divide-y">
            {[
              {
                title: 'Review data checks',
                path: '/quality',
                icon: AlertTriangle,
              },
              { title: 'Measure emissions', path: '/measurement', icon: Leaf },
              {
                title: 'Compare suppliers',
                path: '/procurement/suppliers',
                icon: PackageSearch,
              },
              {
                title: 'Prepare a disclosure',
                path: '/assurance',
                icon: ShieldCheck,
              },
              { title: 'Plan energy use', path: '/dispatch', icon: Zap },
              {
                title: 'Review decisions',
                path: '/approvals',
                icon: ClipboardCheck,
              },
            ].map(({ title, path, icon: Icon }) => (
              <Link
                key={path}
                to={path}
                className="flex items-center gap-3 py-4 text-sm transition-colors hover:text-emerald-700 dark:hover:text-emerald-400"
              >
                <Icon className="size-4 text-muted-foreground" />
                <span className="flex-1">{title}</span>
                <ArrowRight className="size-4" />
              </Link>
            ))}
          </div>
        </section>
      </div>
      <div className="border-b bg-card lg:grid lg:grid-cols-2 lg:divide-x">
        <ApprovalsPanel
          scope={scope}
          onInspect={(item) => setSelection({ kind: 'approval', item })}
        />
        <div className="border-t lg:border-t-0">
          <QualityPanel scope={scope} />
        </div>
      </div>
      <details className="border-b bg-card">
        <summary className="cursor-pointer px-5 py-4 text-sm font-medium sm:px-6">
          Recent activity
        </summary>
        <LedgerPanel
          scope={scope}
          onInspect={(id) => setSelection({ kind: 'event', id })}
        />
      </details>
      <p className="px-5 py-4 text-xs text-muted-foreground sm:px-8">
        Dispatch is advisory only. Procurement projections are not realized
        reductions.
      </p>
      {selection && (
        <Suspense
          fallback={
            <p
              role="status"
              className="fixed right-5 bottom-5 rounded-md border bg-background px-4 py-3 text-sm"
            >
              Loading source inspector...
            </p>
          }
        >
          <SourceInspector
            scope={scope}
            selection={selection}
            onSelect={setSelection}
          />
        </Suspense>
      )}
    </>
  )
}

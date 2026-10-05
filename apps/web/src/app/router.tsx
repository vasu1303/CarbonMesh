import { lazy, Suspense, type ReactNode } from 'react'
import {
  createBrowserRouter,
  Link,
  Navigate,
  RouterProvider,
  useOutletContext,
} from 'react-router-dom'
import App from '@/App'
import { Button } from '@/components/ui/button'
import type { WorkspaceScope } from '@/lib/workspace'

const DashboardPage = lazy(() =>
  import('@/features/dashboard/dashboard-page').then((module) => ({
    default: module.DashboardPage,
  })),
)
const MeasurementsPage = lazy(
  () => import('@/features/measurements/measurements-page'),
)
const MeasurementDetailPage = lazy(
  () => import('@/features/measurements/measurement-detail-page'),
)
const ProcurementPage = lazy(
  () => import('@/features/procurement/procurement-page'),
)
const ScenarioPage = lazy(() => import('@/features/procurement/scenario-page'))
const ScenarioCreatePage = lazy(
  () => import('@/features/procurement/scenario-create-page'),
)
const AgentWorkspacePage = lazy(
  () => import('@/features/agents/agent-workspace-page'),
)
const DataPage = lazy(() => import('@/features/data/data-page'))
const QualityPage = lazy(() => import('@/features/quality/quality-page'))
const AssurancePage = lazy(() => import('@/features/assurance/assurance-page'))
const AssuranceDraftPage = lazy(
  () => import('@/features/assurance/assurance-draft-page'),
)
const DispatchPage = lazy(() => import('@/features/dispatch/dispatch-page'))
const DispatchScenarioPage = lazy(
  () => import('@/features/dispatch/dispatch-scenario-page'),
)
const ApprovalsPage = lazy(() => import('@/features/approvals/approvals-page'))
const RunPage = lazy(() => import('@/features/runs/run-page'))
const LedgerPage = lazy(() => import('@/features/ledger/ledger-page'))

function Screen({ children }: { children: ReactNode }) {
  return (
    <Suspense
      fallback={
        <p role="status" className="p-8 text-sm text-muted-foreground">
          Loading workspace...
        </p>
      }
    >
      {children}
    </Suspense>
  )
}

function DashboardRoute() {
  const scope = useOutletContext<WorkspaceScope>()
  return <DashboardPage scope={scope} />
}

function MissingPage() {
  return (
    <section className="space-y-4 p-8">
      <h1 className="text-xl font-semibold">Page not found</h1>
      <Button asChild variant="outline">
        <Link to="/dashboard">Back to overview</Link>
      </Button>
    </section>
  )
}

const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    errorElement: (
      <section role="alert" className="space-y-4 p-8">
        <h1 className="text-xl font-semibold">This page could not be opened</h1>
        <Button variant="outline" onClick={() => window.location.reload()}>
          Reload workspace
        </Button>
      </section>
    ),
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },
      {
        path: 'dashboard',
        element: (
          <Screen>
            <DashboardRoute />
          </Screen>
        ),
      },
      {
        path: 'ask',
        element: (
          <Screen>
            <AgentWorkspacePage />
          </Screen>
        ),
      },
      {
        path: 'data',
        element: (
          <Screen>
            <DataPage />
          </Screen>
        ),
      },
      {
        path: 'quality',
        element: (
          <Screen>
            <QualityPage />
          </Screen>
        ),
      },
      {
        path: 'measurement',
        element: (
          <Screen>
            <MeasurementsPage />
          </Screen>
        ),
      },
      {
        path: 'measurement/:id',
        element: (
          <Screen>
            <MeasurementDetailPage />
          </Screen>
        ),
      },
      {
        path: 'assurance',
        element: (
          <Screen>
            <AssurancePage />
          </Screen>
        ),
      },
      {
        path: 'assurance/:draftId',
        element: (
          <Screen>
            <AssuranceDraftPage />
          </Screen>
        ),
      },
      {
        path: 'procurement',
        element: <Navigate to="/procurement/suppliers" replace />,
      },
      {
        path: 'procurement/suppliers',
        element: (
          <Screen>
            <ProcurementPage />
          </Screen>
        ),
      },
      {
        path: 'procurement/scenarios/new',
        element: (
          <Screen>
            <ScenarioCreatePage />
          </Screen>
        ),
      },
      {
        path: 'procurement/scenarios/:id',
        element: (
          <Screen>
            <ScenarioPage />
          </Screen>
        ),
      },
      {
        path: 'dispatch',
        element: (
          <Screen>
            <DispatchPage />
          </Screen>
        ),
      },
      {
        path: 'dispatch/:scenarioId',
        element: (
          <Screen>
            <DispatchScenarioPage />
          </Screen>
        ),
      },
      {
        path: 'approvals',
        element: (
          <Screen>
            <ApprovalsPage />
          </Screen>
        ),
      },
      {
        path: 'runs',
        element: (
          <Screen>
            <RunPage />
          </Screen>
        ),
      },
      {
        path: 'runs/:runId',
        element: (
          <Screen>
            <RunPage />
          </Screen>
        ),
      },
      {
        path: 'ledger',
        element: (
          <Screen>
            <LedgerPage />
          </Screen>
        ),
      },
      {
        path: 'demo',
        element: <Navigate to="/dashboard" replace />,
      },
      { path: '*', element: <MissingPage /> },
    ],
  },
])

export function AppRouter() {
  return <RouterProvider router={router} />
}

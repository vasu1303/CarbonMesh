import { lazy, Suspense } from 'react'
import {
  createBrowserRouter,
  Navigate,
  RouterProvider,
  useOutletContext,
} from 'react-router-dom'

import App from '@/App'
import type { WorkspaceScope } from '@/lib/workspace'

const DashboardPage = lazy(() =>
  import('@/features/dashboard/dashboard-page').then((module) => ({
    default: module.DashboardPage,
  })),
)
const MeasurementsPage = lazy(
  () => import('@/features/measurements/measurements-page'),
)

function DashboardRoute() {
  const scope = useOutletContext<WorkspaceScope>()
  return (
    <Suspense
      fallback={
        <p role="status" className="p-8 text-sm text-muted-foreground">
          Loading dashboard...
        </p>
      }
    >
      <DashboardPage scope={scope} />
    </Suspense>
  )
}

const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },
      { path: 'dashboard', element: <DashboardRoute /> },
      {
        path: 'measurement',
        element: (
          <Suspense
            fallback={
              <p role="status" className="p-8 text-sm text-muted-foreground">
                Loading measurements...
              </p>
            }
          >
            <MeasurementsPage />
          </Suspense>
        ),
      },
    ],
  },
])

export function AppRouter() {
  return <RouterProvider router={router} />
}

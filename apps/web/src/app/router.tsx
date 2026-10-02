import { createBrowserRouter, Navigate, RouterProvider } from 'react-router-dom'

import App from '@/App'

const router = createBrowserRouter([
  {
    path: '/',
    element: <Navigate to="/dashboard" replace />,
  },
  {
    path: '/dashboard',
    element: <App />,
  },
])

export function AppRouter() {
  return <RouterProvider router={router} />
}

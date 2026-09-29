import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { QueryProvider } from '@/app/providers/query-provider'
import { AppRouter } from '@/app/router'

import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryProvider>
      <AppRouter />
    </QueryProvider>
  </StrictMode>,
)

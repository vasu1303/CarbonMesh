import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  ClipboardCheck,
  Database,
  LayoutDashboard,
  Leaf,
  MessageSquareText,
  Moon,
  Ruler,
  ScrollText,
  ShieldCheck,
  ShoppingCart,
  Sun,
  Zap,
} from 'lucide-react'
import { useEffect, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { DashboardPage } from '@/features/dashboard/dashboard-page'
import { dashboardScope } from '@/features/dashboard/queries'
import { retryApiQuery } from '@/services/api'
import { getHealth } from '@/services/health'

const navigation = [
  { label: 'Overview', icon: LayoutDashboard },
  { label: 'Agent workspace', icon: MessageSquareText },
  { label: 'Data', icon: Database },
  { label: 'Measurements', icon: Ruler },
  { label: 'Assurance', icon: ShieldCheck },
  { label: 'Procurement', icon: ShoppingCart },
  { label: 'Dispatch', icon: Zap },
  { label: 'Approvals', icon: ClipboardCheck },
  { label: 'Run trace', icon: Activity },
  { label: 'Ledger', icon: ScrollText },
]

function initialTheme() {
  try {
    const saved = localStorage.getItem('carbonmesh-theme')
    if (saved) return saved === 'dark'
  } catch {
    /* Storage may be disabled by the browser. */
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

export default function App() {
  const [dark, setDark] = useState(initialTheme)
  const health = useQuery({
    queryKey: ['health'],
    queryFn: getHealth,
    retry: retryApiQuery,
    refetchInterval: 60_000,
  })
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    try {
      localStorage.setItem('carbonmesh-theme', dark ? 'dark' : 'light')
    } catch {
      /* Theme still works for this session. */
    }
  }, [dark])

  return (
    <TooltipProvider delayDuration={200}>
      <a
        href="#main-content"
        className="sr-only z-50 rounded-md bg-background p-3 focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        Skip to dashboard
      </a>
      <div className="min-h-svh bg-neutral-50 text-foreground dark:bg-neutral-950 lg:grid lg:grid-cols-[14rem_minmax(0,1fr)]">
        <aside className="sticky top-0 hidden h-svh flex-col border-r bg-background lg:flex">
          <div className="flex h-16 shrink-0 items-center gap-3 border-b px-5">
            <div className="flex size-8 items-center justify-center rounded-md bg-emerald-700 text-white">
              <Leaf className="size-4" />
            </div>
            <div>
              <p className="text-sm font-semibold">CarbonMesh</p>
              <p className="text-xs text-muted-foreground">Carbon operations</p>
            </div>
          </div>
          <nav
            className="flex-1 space-y-1 overflow-y-auto p-3"
            aria-label="Primary navigation"
          >
            {navigation.map(({ label, icon: Icon }, index) => (
              <Tooltip key={label}>
                <TooltipTrigger asChild>
                  <span
                    className="block"
                    tabIndex={index ? 0 : undefined}
                    aria-label={
                      index ? `${label}, not connected yet` : undefined
                    }
                  >
                    <Button
                      variant="ghost"
                      disabled={index !== 0}
                      aria-current={index === 0 ? 'page' : undefined}
                      onClick={() => window.scrollTo({ top: 0 })}
                      className={
                        index === 0
                          ? 'w-full justify-start bg-muted'
                          : 'w-full justify-start text-muted-foreground disabled:opacity-60'
                      }
                    >
                      <Icon className="size-4" />
                      {label}
                    </Button>
                  </span>
                </TooltipTrigger>
                {index !== 0 && (
                  <TooltipContent side="right">
                    Not connected yet
                  </TooltipContent>
                )}
              </Tooltip>
            ))}
          </nav>
          <div className="border-t p-5 text-xs text-muted-foreground">
            <p className="font-medium text-foreground">
              Human-reviewed decisions
            </p>
            <p className="mt-1">Traceable facts. Advisory dispatch.</p>
          </div>
        </aside>
        <div className="min-w-0">
          <header className="flex min-h-16 flex-wrap items-center justify-between gap-3 border-b bg-background px-5 py-3 sm:px-8">
            <div className="flex items-center gap-2 text-sm">
              <Leaf className="size-4 text-emerald-600 lg:hidden" />
              <span className="font-semibold lg:hidden">CarbonMesh</span>
              <span className="hidden text-muted-foreground lg:inline">
                Workspace
              </span>
              <span className="text-muted-foreground">/</span>
              <span>Dashboard</span>
            </div>
            <div className="flex items-center gap-3">
              <Badge variant="outline" className="text-xs">
                <span
                  className={`size-1.5 rounded-full ${health.isError ? 'bg-amber-500' : health.data ? 'bg-emerald-500' : 'bg-neutral-400'}`}
                />
                {health.isError
                  ? 'API unavailable'
                  : health.data
                    ? 'API online'
                    : 'Connecting'}
              </Badge>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    size="icon"
                    variant="ghost"
                    className="size-8"
                    aria-label={
                      dark ? 'Switch to light mode' : 'Switch to dark mode'
                    }
                    onClick={() => setDark(!dark)}
                  >
                    {dark ? <Sun /> : <Moon />}
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  {dark ? 'Light mode' : 'Dark mode'}
                </TooltipContent>
              </Tooltip>
            </div>
          </header>
          <main id="main-content" className="mx-auto max-w-[1600px]">
            {dashboardScope.success ? (
              <DashboardPage scope={dashboardScope.data} />
            ) : (
              <section role="alert" className="p-8">
                <h1 className="text-lg font-semibold">
                  Dashboard context is not configured
                </h1>
                <p className="mt-2 text-sm text-muted-foreground">
                  Set valid company, site, reporting period and metric UUIDs in
                  the frontend environment.
                </p>
              </section>
            )}
          </main>
        </div>
      </div>
    </TooltipProvider>
  )
}

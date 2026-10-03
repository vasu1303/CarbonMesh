import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  ClipboardCheck,
  Database,
  FlaskConical,
  LayoutDashboard,
  Leaf,
  ListChecks,
  Menu,
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
import { Link, Outlet, useLocation } from 'react-router-dom'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { workspaceScope } from '@/lib/workspace'
import { SessionBoundary } from '@/features/auth/session-boundary'
import { retryApiQuery } from '@/services/api'
import { getHealth } from '@/services/health'

const navigation = [
  {
    label: 'Overview',
    icon: LayoutDashboard,
    path: '/dashboard',
    root: '/dashboard',
  },
  {
    label: 'Agent workspace',
    icon: MessageSquareText,
    path: '/ask',
    root: '/ask',
  },
  { label: 'Data intake', icon: Database, path: '/data', root: '/data' },
  {
    label: 'Data quality',
    icon: ListChecks,
    path: '/quality',
    root: '/quality',
  },
  {
    label: 'Measurements',
    icon: Ruler,
    path: '/measurement',
    root: '/measurement',
  },
  {
    label: 'Assurance',
    icon: ShieldCheck,
    path: '/assurance',
    root: '/assurance',
  },
  {
    label: 'Procurement',
    icon: ShoppingCart,
    path: '/procurement/suppliers',
    root: '/procurement',
  },
  { label: 'Dispatch', icon: Zap, path: '/dispatch', root: '/dispatch' },
  {
    label: 'Approvals',
    icon: ClipboardCheck,
    path: '/approvals',
    root: '/approvals',
  },
  { label: 'Run trace', icon: Activity, path: '/runs', root: '/runs' },
  { label: 'Ledger', icon: ScrollText, path: '/ledger', root: '/ledger' },
  { label: 'System', icon: FlaskConical, path: '/demo', root: '/demo' },
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
  const [menuOpen, setMenuOpen] = useState(false)
  const location = useLocation()
  const activeItem = navigation.find(
    (item) =>
      location.pathname === item.root ||
      location.pathname.startsWith(item.root + '/'),
  )
  useEffect(() => {
    window.scrollTo({ top: 0 })
  }, [location.pathname])
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

  function navigationLinks(closeOnSelect = false) {
    return navigation.map(({ label, icon: Icon, path, root }) => (
      <Button
        key={path}
        asChild
        variant="ghost"
        className={
          activeItem?.root === root
            ? 'w-full justify-start bg-muted'
            : 'w-full justify-start text-muted-foreground'
        }
      >
        <Link
          to={path}
          aria-current={activeItem?.root === root ? 'page' : undefined}
          onClick={() => {
            if (closeOnSelect) setMenuOpen(false)
          }}
        >
          <Icon className="size-4" />
          {label}
        </Link>
      </Button>
    ))
  }

  return (
    <TooltipProvider delayDuration={200}>
      <a
        href="#main-content"
        className="sr-only z-50 rounded-md bg-background p-3 focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        Skip to content
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
            {navigationLinks()}
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
            <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
              <Leaf className="size-4 text-emerald-600 lg:hidden" />
              <span className="font-semibold lg:hidden">CarbonMesh</span>
              <span className="hidden text-muted-foreground lg:inline">
                Workspace
              </span>
              <span className="text-muted-foreground">/</span>
              <span>{activeItem?.label ?? 'Workspace'}</span>
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
          <nav
            aria-label="Mobile navigation"
            className="flex items-center justify-between gap-2 border-b bg-background px-3 py-2 lg:hidden"
          >
            {activeItem && (
              <Button asChild variant="ghost" size="sm" className="bg-muted">
                <Link to={activeItem.path} aria-current="page">
                  <activeItem.icon className="size-4" />
                  {activeItem.label}
                </Link>
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon"
              aria-label="Open navigation"
              title="Open navigation"
              onClick={() => setMenuOpen(true)}
            >
              <Menu />
            </Button>
          </nav>
          <Dialog open={menuOpen} onOpenChange={setMenuOpen}>
            <DialogContent className="max-h-[90svh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle>CarbonMesh</DialogTitle>
                <DialogDescription>Carbon operations</DialogDescription>
              </DialogHeader>
              <nav aria-label="Workspace navigation" className="space-y-1">
                {navigationLinks(true)}
              </nav>
            </DialogContent>
          </Dialog>
          <main id="main-content" className="mx-auto max-w-[1600px]">
            {workspaceScope.success ? (
              <SessionBoundary>
                <Outlet context={workspaceScope.data} />
              </SessionBoundary>
            ) : (
              <section role="alert" className="p-8">
                <h1 className="text-lg font-semibold">
                  Workspace context is not configured
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

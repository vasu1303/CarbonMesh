import { lazy, Suspense } from 'react'
import { Skeleton } from '@/components/ui/skeleton'

export type LineageDiagramProps = {
  nodes: Array<{ id: string; label: string; type: string; status?: string }>
  edges: Array<{ id: string; source: string; target: string; label?: string }>
  onSelect?: (id: string) => void
  selectedId?: string
}

const LineageCanvas = lazy(() => import('./lineage-canvas'))

export function LineageDiagram(props: LineageDiagramProps) {
  if (!props.nodes.length) {
    return (
      <p className="py-8 text-sm text-muted-foreground">
        No evidence links have been recorded yet.
      </p>
    )
  }
  return (
    <Suspense
      fallback={
        <Skeleton
          aria-label="Loading evidence diagram"
          className="h-96 w-full motion-reduce:animate-none"
        />
      }
    >
      <LineageCanvas {...props} />
    </Suspense>
  )
}

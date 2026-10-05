import dagre from '@dagrejs/dagre'
import {
  MarkerType,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Node,
  type Edge,
} from '@xyflow/react'
import {
  Calculator,
  ClipboardCheck,
  FileText,
  GitBranch,
  Leaf,
  Minus,
  Plus,
  Scan,
} from 'lucide-react'
import { useMemo } from 'react'
import '@xyflow/react/dist/style.css'
import { Button } from '@/components/ui/button'
import { displayText, humanize } from '@/lib/presentation'
import type { LineageDiagramProps } from './lineage-diagram'

function GraphTools() {
  const { zoomIn, zoomOut, fitView } = useReactFlow()
  return (
    <Panel
      position="bottom-right"
      className="flex gap-1 rounded-md border bg-background p-1"
    >
      <Button
        size="icon"
        variant="ghost"
        className="size-8"
        title="Zoom in"
        aria-label="Zoom in"
        onClick={() => void zoomIn()}
      >
        <Plus />
      </Button>
      <Button
        size="icon"
        variant="ghost"
        className="size-8"
        title="Zoom out"
        aria-label="Zoom out"
        onClick={() => void zoomOut()}
      >
        <Minus />
      </Button>
      <Button
        size="icon"
        variant="ghost"
        className="size-8"
        title="Fit evidence diagram"
        aria-label="Fit evidence diagram"
        onClick={() => void fitView({ padding: 0.15 })}
      >
        <Scan />
      </Button>
    </Panel>
  )
}

export default function LineageCanvas({
  nodes: records,
  edges: links,
  onSelect,
  selectedId,
}: LineageDiagramProps) {
  const { nodes, edges } = useMemo(() => {
    const graph = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}))
    graph.setGraph({
      rankdir: 'LR',
      nodesep: 28,
      ranksep: 76,
      marginx: 20,
      marginy: 20,
    })
    const labels = new Map(records.map((record) => [record.id, record.label]))
    const connected = links.filter(
      (edge) => labels.has(edge.source) && labels.has(edge.target),
    )
    records.forEach((record) =>
      graph.setNode(record.id, { width: 240, height: 112 }),
    )
    connected.forEach((edge) => graph.setEdge(edge.source, edge.target))
    dagre.layout(graph)
    const nodes: Node[] = records.map((record) => {
      const position = graph.node(record.id)
      const Icon = /measurement/i.test(record.type)
        ? Leaf
        : /calculation/i.test(record.type)
          ? Calculator
          : /approval|decision/i.test(record.type)
            ? ClipboardCheck
            : /ledger/i.test(record.type)
              ? GitBranch
              : FileText
      return {
        id: record.id,
        position: { x: position.x - 120, y: position.y - 56 },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        ariaLabel: `${humanize(record.type)}: ${displayText(record.label)}`,
        selected: selectedId === record.id,
        style: {
          width: 240,
          height: 112,
          padding: 0,
          borderRadius: 8,
          background: 'var(--background)',
          color: 'var(--foreground)',
          borderColor: selectedId === record.id ? '#13836c' : 'var(--border)',
          boxShadow: 'none',
        },
        data: {
          label: (
            <Button
              variant="ghost"
              className="h-full w-full items-start justify-start gap-3 rounded-lg p-4 text-left whitespace-normal"
              title={displayText(record.label)}
            >
              <Icon className="mt-0.5 size-4 shrink-0 text-emerald-700 dark:text-emerald-400" />
              <span className="min-w-0">
                <span className="block text-xs font-normal text-muted-foreground">
                  {humanize(record.type)}
                </span>
                <span className="mt-1 line-clamp-2 text-sm leading-5 break-words">
                  {displayText(record.label)}
                </span>
                {record.status && (
                  <span className="mt-1 block text-xs font-normal text-muted-foreground">
                    {humanize(record.status)}
                  </span>
                )}
              </span>
            </Button>
          ),
        },
      }
    })
    const edges: Edge[] = connected.map((link) => ({
      id: link.id,
      source: link.source,
      target: link.target,
      ariaLabel: `${humanize(link.label ?? 'Evidence relationship')}: ${displayText(labels.get(link.source))} to ${displayText(labels.get(link.target))}`,
      label: link.label ? humanize(link.label) : undefined,
      type: 'smoothstep',
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 18,
        height: 18,
        color: '#73857d',
      },
      style: { stroke: '#73857d', strokeWidth: 1.5 },
      labelStyle: { fontSize: 11, fill: 'var(--muted-foreground)' },
      labelBgStyle: { fill: 'var(--background)' },
    }))
    return { nodes, edges }
  }, [records, links, selectedId])

  return (
    <figure aria-label="Evidence lineage diagram" className="min-w-0">
      <div className="h-96 w-full overflow-hidden rounded-lg border bg-background sm:h-[28rem]">
        <ReactFlowProvider>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            fitView
            fitViewOptions={{ padding: 0.15 }}
            minZoom={0.15}
            maxZoom={1.8}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={false}
            onNodeClick={(_, node) => onSelect?.(node.id)}
            zoomOnScroll={false}
            preventScrolling={false}
            style={{ background: 'var(--background)' }}
            ariaLabelConfig={{
              'node.a11yDescription.default': 'Evidence record',
              'edge.a11yDescription.default': 'Evidence relationship',
            }}
          >
            <GraphTools />
          </ReactFlow>
        </ReactFlowProvider>
      </div>
    </figure>
  )
}

import { ArrowUpRight, GitBranch } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { formatDate, formatDecimal } from '@/lib/format'
import { displayText, humanize } from '@/lib/presentation'
import type { MeasurementSummary } from '../schemas'
import { StatusBadge } from './status-badge'

export function RecordsTable({
  items,
  onInspect,
}: {
  items: MeasurementSummary[]
  onInspect: (id: string, tab?: string) => void
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Record</TableHead>
          <TableHead>Status</TableHead>
          <TableHead className="text-right">Emissions / kgCO2e</TableHead>
          <TableHead className="text-right">Confidence / 0-1</TableHead>
          <TableHead className="text-right">Inspect</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((item) => (
          <TableRow key={item.id}>
            <TableCell className="py-4">
              <Button
                asChild
                variant="link"
                className="h-auto max-w-72 p-0 text-left text-sm whitespace-normal"
              >
                <Link
                  to={`/measurement/${item.id}`}
                  aria-label={`Open ${humanize(item.metric_key)} recorded ${formatDate(item.created_at)}`}
                >
                  {humanize(item.metric_key)}
                </Link>
              </Button>
              <p className="mt-1 text-xs text-muted-foreground">
                {formatDate(item.created_at)}
              </p>
              <p className="mt-1 max-w-64 text-xs text-muted-foreground whitespace-normal wrap-anywhere">
                {displayText(item.site_name)} /{' '}
                {displayText(item.reporting_period_name)}
              </p>
            </TableCell>
            <TableCell>
              <StatusBadge status={item.status} />
            </TableCell>
            <TableCell className="text-right font-mono text-sm tabular-nums">
              {item.status === 'unsupported'
                ? 'Not supported'
                : formatDecimal(item.value_kgco2e)}
            </TableCell>
            <TableCell className="text-right font-mono text-sm tabular-nums">
              {item.status === 'unsupported'
                ? 'Not assessed'
                : formatDecimal(item.confidence)}
            </TableCell>
            <TableCell>
              <div className="flex justify-end gap-1">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-8"
                      aria-label={`View lineage for ${humanize(item.metric_key)} recorded ${formatDate(item.created_at)}`}
                      onClick={() => onInspect(item.id, 'lineage')}
                    >
                      <GitBranch className="size-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Source lineage</TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-8"
                      aria-label={`View facts for ${humanize(item.metric_key)} recorded ${formatDate(item.created_at)}`}
                      onClick={() => onInspect(item.id)}
                    >
                      <ArrowUpRight />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Measurement facts</TooltipContent>
                </Tooltip>
              </div>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

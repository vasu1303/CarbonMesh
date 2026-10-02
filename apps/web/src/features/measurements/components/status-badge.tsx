import { Badge } from '@/components/ui/badge'
import type { MeasurementStatus } from '../schemas'

const colors: Record<MeasurementStatus, string> = {
  verified:
    'border-emerald-200 text-emerald-700 dark:border-emerald-900 dark:text-emerald-400',
  draft: 'border-sky-200 text-sky-700 dark:border-sky-900 dark:text-sky-400',
  superseded:
    'border-amber-200 text-amber-700 dark:border-amber-900 dark:text-amber-400',
  unsupported:
    'border-rose-200 text-rose-700 dark:border-rose-900 dark:text-rose-400',
}

export function StatusBadge({ status }: { status: MeasurementStatus }) {
  return (
    <Badge variant="outline" className={`capitalize ${colors[status]}`}>
      {status}
    </Badge>
  )
}

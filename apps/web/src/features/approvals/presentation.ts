import { formatDate } from '@/lib/format'
import { displayText, humanize } from '@/lib/presentation'

export function reviewValue(value: unknown): string {
  if (typeof value === 'string') {
    if (/^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value)))
      return formatDate(value)
    if (/^[a-z][a-z0-9]*(?:[_.][a-z0-9]+)+$/.test(value)) return humanize(value)
  }
  return displayText(value)
}

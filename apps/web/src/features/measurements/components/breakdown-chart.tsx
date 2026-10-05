import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from 'recharts'
import { useNavigate } from 'react-router-dom'

import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from '@/components/ui/chart'
import { formatDate, formatDecimal } from '@/lib/format'
import { humanize } from '@/lib/presentation'
import type { MeasurementBreakdown } from '../schemas'

export default function BreakdownChart({
  items,
}: {
  items: MeasurementBreakdown['items']
}) {
  const navigate = useNavigate()
  const data = [...items]
    .sort((a, b) =>
      (a.interval_start ?? a.activity_date ?? '').localeCompare(
        b.interval_start ?? b.activity_date ?? '',
      ),
    )
    .map((item) => ({
      label: item.interval_start
        ? formatDate(item.interval_start)
        : (item.activity_date ?? humanize(item.material_code)),
      material: humanize(item.material_code),
      documentId: item.source_document_id,
      value: Number(item.emissions_kgco2e),
      exact: item.emissions_kgco2e,
    }))
  return (
    <ChartContainer
      config={{ value: { label: 'kgCO2e', color: '#059669' } }}
      className="aspect-auto h-56 w-full"
    >
      <BarChart data={data} accessibilityLayer margin={{ top: 12, right: 12 }}>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="label"
          tickLine={false}
          axisLine={false}
          minTickGap={48}
          tick={{ fontSize: 11 }}
        />
        <YAxis
          width={64}
          tickLine={false}
          axisLine={false}
          tickFormatter={(value: number) =>
            new Intl.NumberFormat('en', { notation: 'compact' }).format(value)
          }
        />
        <ChartTooltip
          content={
            <ChartTooltipContent
              formatter={(_value, _name, item) => (
                <div className="max-w-64 space-y-1 wrap-anywhere">
                  <p className="text-xs text-muted-foreground">
                    {item.payload.label}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {item.payload.material}
                  </p>
                  <p className="font-mono">
                    {formatDecimal(item.payload.exact)} kgCO2e
                  </p>
                </div>
              )}
            />
          }
        />
        <Bar
          dataKey="value"
          fill="var(--color-value)"
          maxBarSize={52}
          cursor="pointer"
          onClick={(_item, index) => {
            const selected = data[index]
            if (selected) navigate(`/data?document=${selected.documentId}`)
          }}
          isAnimationActive={false}
        />
      </BarChart>
    </ChartContainer>
  )
}

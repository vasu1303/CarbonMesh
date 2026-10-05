import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from 'recharts'

import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from '@/components/ui/chart'
import { formatDecimal } from '@/lib/format'
import type { Measurement } from '../schemas'

export default function MeasurementChart({
  measurements,
}: {
  measurements: Measurement[]
}) {
  const data = measurements.map((measurement, index) => ({
    label: `M${index + 1}`,
    emissions: Number(measurement.value_kgco2e),
    exact: measurement.value_kgco2e,
  }))
  if (data.some((point) => !Number.isFinite(point.emissions))) return null

  return (
    <ChartContainer
      config={{ emissions: { label: 'kgCO2e', color: '#059669' } }}
      className="aspect-auto h-48 w-full"
      aria-label="Individual verified measurements in kilograms CO2 equivalent"
    >
      <BarChart
        data={data}
        accessibilityLayer
        margin={{ left: 0, right: 4, top: 10 }}
      >
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="label"
          axisLine={false}
          tickLine={false}
          tickMargin={8}
        />
        <YAxis
          axisLine={false}
          tickLine={false}
          width={58}
          tickFormatter={(value: number) =>
            new Intl.NumberFormat('en', { notation: 'compact' }).format(value)
          }
        />
        <ChartTooltip
          cursor={{ fill: 'var(--muted)' }}
          content={
            <ChartTooltipContent
              formatter={(_value, _name, item) => (
                <span className="font-mono tabular-nums">
                  {formatDecimal(item.payload.exact)} kgCO2e
                </span>
              )}
            />
          }
        />
        <Bar
          dataKey="emissions"
          fill="var(--color-emissions)"
          radius={[3, 3, 0, 0]}
          maxBarSize={48}
          isAnimationActive={false}
        />
      </BarChart>
    </ChartContainer>
  )
}

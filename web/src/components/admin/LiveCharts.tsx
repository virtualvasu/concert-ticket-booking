import { useEffect, useRef, useState } from 'react'
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Line, LineChart, XAxis, YAxis } from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent, type ChartConfig } from '@/components/ui/chart'
import type { Stats } from '@/lib/api'

const MAX_POINTS = 180
const seatConfig = {
  LOCKED: { label: 'Locked', color: '#94a3b8' },
  AVAILABLE: { label: 'Available', color: '#3b82f6' },
  HELD: { label: 'Held', color: '#f59e0b' },
  SOLD: { label: 'Sold', color: '#16a34a' },
} satisfies ChartConfig
const rateConfig = {
  rps: { label: 'Requests/s', color: '#3b82f6' },
  payment_attempts: { label: 'Payment attempts/s', color: '#f59e0b' },
} satisfies ChartConfig
const flowConfig = {
  offers: { label: 'Offers/s', color: '#3b82f6' },
  bookings: { label: 'Bookings/s', color: '#16a34a' },
} satisfies ChartConfig

const clock = (t: number) => new Date(t * 1000).toLocaleTimeString([], { minute: '2-digit', second: '2-digit' })

export function LiveCharts({ stats }: { stats: Stats | undefined }) {
  // Seat states are a snapshot per poll, so we accumulate the history client-side.
  const [history, setHistory] = useState<Record<string, number>[]>([])
  const lastSale = useRef<number | undefined>(undefined)
  useEffect(() => {
    if (!stats) return
    const { seats, sale_id } = stats.sale
    const point = { t: Math.floor(Date.now() / 1000), LOCKED: seats.LOCKED, AVAILABLE: seats.AVAILABLE, HELD: seats.HELD, SOLD: seats.SOLD }
    setHistory((h) => {
      const base = lastSale.current === sale_id ? h : []
      lastSale.current = sale_id
      return [...base, point].slice(-MAX_POINTS)
    })
  }, [stats])

  const series = stats?.series ?? []
  const axis = <XAxis dataKey="t" tickFormatter={clock} minTickGap={40} />

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card>
        <CardHeader>
          <CardTitle>Seat states</CardTitle>
        </CardHeader>
        <CardContent>
          <ChartContainer config={seatConfig} className="h-56 w-full">
            <AreaChart data={history}>
              <CartesianGrid vertical={false} />
              {axis}
              <YAxis width={36} />
              <ChartTooltip content={<ChartTooltipContent />} />
              <ChartLegend content={<ChartLegendContent />} />
              {(Object.keys(seatConfig) as (keyof typeof seatConfig)[]).map((k) => (
                <Area key={k} dataKey={k} stackId="seats" type="monotone" stroke={`var(--color-${k})`} fill={`var(--color-${k})`} isAnimationActive={false} />
              ))}
            </AreaChart>
          </ChartContainer>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Traffic</CardTitle>
        </CardHeader>
        <CardContent>
          <ChartContainer config={rateConfig} className="h-56 w-full">
            <LineChart data={series}>
              <CartesianGrid vertical={false} />
              {axis}
              <YAxis width={36} />
              <ChartTooltip content={<ChartTooltipContent />} />
              <ChartLegend content={<ChartLegendContent />} />
              <Line dataKey="rps" stroke="var(--color-rps)" dot={false} isAnimationActive={false} />
              <Line dataKey="payment_attempts" stroke="var(--color-payment_attempts)" dot={false} isAnimationActive={false} />
            </LineChart>
          </ChartContainer>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Offers and bookings</CardTitle>
        </CardHeader>
        <CardContent>
          <ChartContainer config={flowConfig} className="h-56 w-full">
            <BarChart data={series}>
              <CartesianGrid vertical={false} />
              {axis}
              <YAxis width={36} allowDecimals={false} />
              <ChartTooltip content={<ChartTooltipContent />} />
              <ChartLegend content={<ChartLegendContent />} />
              <Bar dataKey="offers" fill="var(--color-offers)" isAnimationActive={false} />
              <Bar dataKey="bookings" fill="var(--color-bookings)" isAnimationActive={false} />
            </BarChart>
          </ChartContainer>
        </CardContent>
      </Card>
    </div>
  )
}

import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { secondsUntil, useTicker } from '@/hooks/useServerClock'
import type { Stats } from '@/lib/api'

const fmt = (n: number | null) => (n === null ? '–' : `${n.toFixed(1)} s`)

export function StatsCards({ stats, now }: { stats: Stats | undefined; now: () => number }) {
  useTicker()
  if (!stats) return null
  const { sale, counts, holds, payments, wait_seconds: w } = stats
  const next = sale.next_tick_at ? secondsUntil(sale.next_tick_at, now()).toFixed(1) + ' s' : '–'
  const tiles: [string, string | number][] = [
    ['Locked', sale.seats.LOCKED],
    ['Available', sale.seats.AVAILABLE],
    ['Held', sale.seats.HELD],
    ['Sold', sale.seats.SOLD],
    ['Queue length', sale.queue_length],
    ['Booked', counts.booked],
    ['Expired holds', holds.expired],
    ['Refunds', payments.refunded],
    ['Wait p50', fmt(w.p50)],
    ['Wait p95', fmt(w.p95)],
    ['Next release', next],
  ]
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
      {tiles.map(([label, value]) => (
        <Card key={label} size="sm">
          <CardHeader>
            <CardDescription>{label}</CardDescription>
            <CardTitle className="text-2xl tabular-nums">{value}</CardTitle>
          </CardHeader>
        </Card>
      ))}
    </div>
  )
}


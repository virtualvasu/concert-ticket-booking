import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Separator } from '@/components/ui/separator'
import { secondsUntil, useTicker } from '@/hooks/useServerClock'
import type { Me, Sale } from '@/lib/api'

interface Props {
  me: Me
  sale: Sale | undefined
  now: () => number
}

export function WaitingCard({ me, sale, now }: Props) {
  useTicker()
  const next = secondsUntil(sale?.next_tick_at, now())
  const total = sale?.seats.total ?? 0
  const sold = sale?.seats.SOLD ?? 0
  const soldPct = total ? Math.round((sold / total) * 100) : 0
  const ahead = me.ahead ?? 0

  return (
    <Card>
      <CardHeader className="text-center">
        <CardDescription>You are in the queue, {me.name}</CardDescription>
        <CardTitle className="text-7xl font-bold tabular-nums">#{me.position ?? '–'}</CardTitle>
        <CardDescription>
          {ahead === 0 ? 'You are next in line' : `${ahead} ${ahead === 1 ? 'person' : 'people'} ahead of you`}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Separator />
        <div className="flex items-baseline justify-between">
          <span className="text-sm text-muted-foreground">Next release in</span>
          <span className="text-2xl font-semibold tabular-nums">{next.toFixed(1)} s</span>
        </div>
        {sale && (
          <p className="text-sm text-muted-foreground">
            {sale.seats_per_tick} seats are released every {sale.tick_seconds} s.
          </p>
        )}
        <div className="space-y-1">
          <div className="flex justify-between text-sm text-muted-foreground">
            <span>Seats sold</span>
            <span className="tabular-nums">
              {sold} / {total}
            </span>
          </div>
          <Progress value={soldPct} />
        </div>
        <p className="text-xs text-muted-foreground">Keep this page open. When it is your turn, you will have a short time to pay.</p>
      </CardContent>
    </Card>
  )
}

import { useRef, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { Switch } from '@/components/ui/switch'
import { secondsUntil, useTicker } from '@/hooks/useServerClock'
import { rupees } from '@/lib/concert'
import type { Offer } from '@/lib/api'

interface Props {
  offer: Offer
  holdSeconds: number
  now: () => number
  paying: boolean
  /** `key` is stable per click and is reused if the request fails at network level. */
  onPay: (holdId: number, key: string, force: 'fail' | null) => Promise<void>
}

export function OfferCard({ offer, holdSeconds, now, paying, onPay }: Props) {
  useTicker()
  const [forceFail, setForceFail] = useState(false)
  // Idempotency key for the click in flight; cleared once the server answered.
  const pending = useRef<string | null>(null)

  const left = secondsUntil(offer.expires_at, now())
  const pct = holdSeconds > 0 ? Math.min(100, (left / holdSeconds) * 100) : 0
  const s = offer.seat

  const click = async () => {
    const key = pending.current ?? crypto.randomUUID()
    pending.current = key
    try {
      await onPay(offer.hold_id, key, forceFail ? 'fail' : null)
      pending.current = null // got an HTTP answer: next click is a new attempt
    } catch {
      /* network-level error: keep the key so a retry cannot double-charge */
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardDescription>Your seat is reserved</CardDescription>
        <CardTitle className="text-3xl">
          {s.section} · Row {s.row} · Seat {s.number}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1">
          <div className="flex justify-between text-sm">
            <span className="text-muted-foreground">Time left to pay</span>
            <span className="font-semibold tabular-nums">{Math.ceil(left)} s</span>
          </div>
          <Progress value={pct} />
        </div>
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">Attempts left</span>
          <Badge variant="secondary">{offer.attempts_left}</Badge>
        </div>
        <div className="flex items-center justify-between rounded-md border p-3">
          <Label htmlFor="force-fail" className="text-xs text-muted-foreground">
            Demo: make this payment fail
          </Label>
          <Switch id="force-fail" checked={forceFail} onCheckedChange={setForceFail} />
        </div>
      </CardContent>
      <CardFooter>
        <Button className="w-full" size="lg" disabled={paying || left <= 0} onClick={click}>
          {paying ? 'Paying…' : `Pay ${rupees(s.price)}`}
        </Button>
      </CardFooter>
    </Card>
  )
}

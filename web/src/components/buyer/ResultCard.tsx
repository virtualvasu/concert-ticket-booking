import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { CONCERT, rupees } from '@/lib/concert'
import type { Me, Reason } from '@/lib/api'

const REASONS: Record<Reason, string> = {
  hold_expired: 'Your 60 s to pay ran out, so the seat went to the next person.',
  payment_failed: 'Your payment failed and you have no attempts left.',
  sold_out: 'All seats are gone. This concert is sold out.',
  sale_stopped: 'The sale was stopped by the organiser.',
}

interface Props {
  me: Me
  holdSeconds?: number
  onRejoin: () => void
}

export function ResultCard({ me, holdSeconds, onRejoin }: Props) {
  if (me.state === 'booked' && me.booking) {
    const s = me.booking.seat
    return (
      <Card className="border-dashed border-2">
        <CardHeader className="text-center">
          <CardDescription>Booking confirmed · #{me.booking.booking_id}</CardDescription>
          <CardTitle className="text-2xl">{CONCERT.name}</CardTitle>
          <CardDescription>{CONCERT.date}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Separator />
          <div className="grid grid-cols-3 text-center">
            <div>
              <div className="text-xs text-muted-foreground">Section</div>
              <div className="text-lg font-semibold">{s.section}</div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">Row</div>
              <div className="text-lg font-semibold">{s.row}</div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">Seat</div>
              <div className="text-lg font-semibold">{s.number}</div>
            </div>
          </div>
          <Separator />
          <p className="text-center text-sm text-muted-foreground">
            {me.name} · paid {rupees(s.price)}
          </p>
        </CardContent>
      </Card>
    )
  }

  let reason = me.reason ? REASONS[me.reason] : 'You are no longer in the sale.'
  if (me.reason === 'hold_expired' && holdSeconds) {
    reason = `Your ${Math.round(holdSeconds)} s to pay ran out, so the seat went to the next person.`
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>No ticket this time</CardTitle>
        <CardDescription>{reason}</CardDescription>
      </CardHeader>
      <CardFooter>
        <Button className="w-full" size="lg" onClick={onRejoin}>
          Rejoin at the back
        </Button>
      </CardFooter>
    </Card>
  )
}

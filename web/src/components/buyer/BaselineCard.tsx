import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { CONCERT } from '@/lib/concert'
import type { Sale } from '@/lib/api'

interface Props {
  sale: Sale | undefined
  busy: boolean
  onGrab: () => void
}

export function BaselineCard({ sale, busy, onGrab }: Props) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-2xl">{CONCERT.name}</CardTitle>
        <CardDescription>{CONCERT.date}</CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">
          <span className="text-2xl font-semibold text-foreground">{sale?.seats.AVAILABLE ?? '–'}</span> seats free right now. First come, first served.
        </p>
      </CardContent>
      <CardFooter>
        <Button className="w-full" size="lg" disabled={busy} onClick={onGrab}>
          Grab a seat
        </Button>
      </CardFooter>
    </Card>
  )
}

import { useState } from 'react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { CONCERT } from '@/lib/concert'
import type { Sale } from '@/lib/api'

interface Props {
  sale: Sale | undefined
  noSale: boolean
  busy: boolean
  onJoin: (name: string) => void
}

export function JoinCard({ sale, noSale, busy, onJoin }: Props) {
  const [name, setName] = useState('')
  const baseline = sale?.mode === 'baseline'
  const running = sale?.status === 'running'
  const left = sale ? sale.seats.AVAILABLE + sale.seats.LOCKED + sale.seats.HELD : 0

  let blocked: string | null = null
  if (noSale) blocked = 'No sale has been created yet.'
  else if (sale && sale.status === 'idle') blocked = 'The sale has not started yet. Check back soon.'
  else if (sale && sale.status === 'ended') {
    blocked = sale.end_reason === 'sold_out' ? 'This concert is sold out.' : 'This sale has ended.'
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-2xl">{CONCERT.name}</CardTitle>
        <CardDescription>{CONCERT.date}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {sale ? (
          <p className="text-sm text-muted-foreground">
            <span className="text-2xl font-semibold text-foreground">{left}</span> of {sale.seats.total} seats left
          </p>
        ) : noSale ? null : (
          <Skeleton className="h-8 w-40" />
        )}
        <div className="space-y-2">
          <Label htmlFor="name">Your name</Label>
          <Input
            id="name"
            value={name}
            maxLength={200}
            placeholder="e.g. Asha"
            disabled={!running}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && name.trim() && running && !busy && onJoin(name.trim())}
          />
        </div>
        {blocked && (
          <Alert>
            <AlertDescription>{blocked}</AlertDescription>
          </Alert>
        )}
      </CardContent>
      <CardFooter>
        <Button
          className="w-full"
          size="lg"
          disabled={!running || !name.trim() || busy}
          onClick={() => onJoin(name.trim())}
        >
          {baseline ? 'Enter sale' : 'Join the queue'}
        </Button>
      </CardFooter>
    </Card>
  )
}

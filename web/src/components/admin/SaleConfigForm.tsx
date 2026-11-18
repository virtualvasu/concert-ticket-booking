import { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { adminCreateSale, adminStartSale, adminStopSale, type Mode, type Sale, type SaleConfig } from '@/lib/api'

interface Props {
  token: string
  sale: Sale | undefined
  onError: (e: unknown) => void
  onChanged: () => void
}

const DEFAULT_SECTIONS: SaleConfig['sections'] = [
  { name: 'VIP', rows: 2, seats_per_row: 25, price: 5000 },
  { name: 'Gold', rows: 6, seats_per_row: 25, price: 3000 },
  { name: 'Silver', rows: 12, seats_per_row: 25, price: 1500 },
]

export function SaleConfigForm({ token, sale, onError, onChanged }: Props) {
  const [mode, setMode] = useState<Mode>('bucket')
  const [label, setLabel] = useState('')
  const [perTick, setPerTick] = useState('10')
  const [tickSeconds, setTickSeconds] = useState('10')
  const [holdSeconds, setHoldSeconds] = useState('60')
  const [attempts, setAttempts] = useState('3')
  const [busy, setBusy] = useState(false)

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true)
    try {
      await fn()
      toast.success(ok)
      onChanged()
    } catch (e) {
      onError(e)
    } finally {
      setBusy(false)
    }
  }

  const createAndStart = () =>
    run(async () => {
      const created = await adminCreateSale(token, {
        mode,
        label,
        sections: DEFAULT_SECTIONS,
        seats_per_tick: Number(perTick),
        tick_seconds: Number(tickSeconds),
        hold_seconds: Number(holdSeconds),
        max_payment_attempts: Number(attempts),
        tickets_per_user: 1,
      })
      await adminStartSale(token, created.sale_id)
    }, 'Sale started')

  const running = sale?.status === 'running'
  const field = (id: string, text: string, value: string, set: (v: string) => void, type = 'number') => (
    <div className="space-y-1">
      <Label htmlFor={id}>{text}</Label>
      <Input id={id} type={type} value={value} onChange={(e) => set(e.target.value)} />
    </div>
  )

  return (
    <Card>
      <CardHeader>
        <CardTitle>Sale</CardTitle>
        <CardDescription>
          {sale ? `Sale #${sale.sale_id} · ${sale.mode} · ${sale.status}` : 'No sale yet'} — new sales use 500 seats (VIP, Gold, Silver)
        </CardDescription>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <Label>Mode</Label>
          <Select value={mode} onValueChange={(v) => setMode(v as Mode)}>
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="bucket">bucket</SelectItem>
              <SelectItem value="baseline">baseline</SelectItem>
            </SelectContent>
          </Select>
        </div>
        {field('label', 'Label', label, setLabel, 'text')}
        {field('per-tick', 'Seats per tick', perTick, setPerTick)}
        {field('tick-s', 'Tick seconds', tickSeconds, setTickSeconds)}
        {field('hold-s', 'Hold seconds', holdSeconds, setHoldSeconds)}
        {field('attempts', 'Max payment attempts', attempts, setAttempts)}
      </CardContent>
      <CardFooter className="gap-2">
        <Button disabled={busy || running} onClick={createAndStart}>
          Create &amp; start
        </Button>
        <Button
          variant="destructive"
          disabled={busy || !running || !sale}
          onClick={() => sale && run(() => adminStopSale(token, sale.sale_id), 'Sale stopped')}
        >
          Stop sale
        </Button>
      </CardFooter>
    </Card>
  )
}

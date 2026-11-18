import { useCallback, useState } from 'react'
import { toast } from 'sonner'
import { ConsistencyPanel } from '@/components/admin/ConsistencyPanel'
import { GatewayForm } from '@/components/admin/GatewayForm'
import { LiveCharts } from '@/components/admin/LiveCharts'
import { SaleConfigForm } from '@/components/admin/SaleConfigForm'
import { StatsCards } from '@/components/admin/StatsCards'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { useServerClock } from '@/hooks/useServerClock'
import { usePoll } from '@/hooks/usePoll'
import { adminStats, ApiError, downloadTrace } from '@/lib/api'
import { loadAdminToken, saveAdminToken } from '@/lib/storage'
import { useEffect } from 'react'

function TokenPrompt({ onSubmit, rejected }: { onSubmit: (t: string) => void; rejected: boolean }) {
  const [value, setValue] = useState('')
  return (
    <div className="mx-auto max-w-sm px-4 py-12">
      <Card>
        <CardHeader>
          <CardTitle>Admin token</CardTitle>
          <CardDescription>{rejected ? 'That token was rejected. Try again.' : 'Enter the admin token to continue.'}</CardDescription>
        </CardHeader>
        <CardContent className="flex gap-2">
          <Input
            type="password"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && value && onSubmit(value)}
          />
          <Button disabled={!value} onClick={() => onSubmit(value)}>
            Enter
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}

export function AdminPage() {
  const [token, setToken] = useState<string | null>(loadAdminToken)
  const [rejected, setRejected] = useState(false)
  const clock = useServerClock()

  const onError = useCallback((e: unknown) => {
    if (e instanceof ApiError && e.status === 401) {
      saveAdminToken(null)
      setToken(null)
      setRejected(true)
    } else {
      toast.error(e instanceof Error ? e.message : 'Request failed')
    }
  }, [])

  const stats = usePoll(
    () => (token ? adminStats(token) : Promise.resolve(undefined)),
    1000,
    [token],
  )
  useEffect(() => {
    if (stats.error) {
      if (!(stats.error instanceof ApiError && stats.error.status === 404)) onError(stats.error)
    }
  }, [stats.error, onError])
  useEffect(() => clock.observe(stats.data?.sale.server_time), [stats.data, clock])

  if (!token) {
    return (
      <TokenPrompt
        rejected={rejected}
        onSubmit={(t) => {
          saveAdminToken(t)
          setRejected(false)
          setToken(t)
        }}
      />
    )
  }

  const sale = stats.data?.sale
  return (
    <div className="mx-auto max-w-6xl space-y-4 px-4 py-6">
      <StatsCards stats={stats.data} now={clock.now} />
      <LiveCharts stats={stats.data} />
      <div className="grid gap-4 lg:grid-cols-3">
        <SaleConfigForm token={token} sale={sale} onError={onError} onChanged={stats.refresh} />
        <GatewayForm token={token} onError={onError} />
        <div className="space-y-4">
          <ConsistencyPanel token={token} saleId={sale?.sale_id} />
          {sale && (
            <Button variant="outline" className="w-full" onClick={() => downloadTrace(token, sale.sale_id).catch(onError)}>
              Download trace
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}

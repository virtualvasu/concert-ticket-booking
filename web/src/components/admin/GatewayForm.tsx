import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Slider } from '@/components/ui/slider'
import { adminGetGateway, adminSetGateway } from '@/lib/api'

interface Props {
  token: string
  onError: (e: unknown) => void
}

export function GatewayForm({ token, onError }: Props) {
  const [failure, setFailure] = useState(10)
  const [mean, setMean] = useState('300')
  const [jitter, setJitter] = useState('200')

  useEffect(() => {
    adminGetGateway(token)
      .then((g) => {
        setFailure(Math.round(g.failure_rate * 100))
        setMean(String(g.latency_ms_mean))
        setJitter(String(g.latency_ms_jitter))
      })
      .catch(onError)
  }, [token, onError])

  const save = async () => {
    try {
      await adminSetGateway(token, {
        failure_rate: failure / 100,
        latency_ms_mean: Number(mean),
        latency_ms_jitter: Number(jitter),
        seed: null,
      })
      toast.success('Gateway updated')
    } catch (e) {
      onError(e)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Payment gateway</CardTitle>
        <CardDescription>Mock gateway behaviour</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label>Failure rate: {failure}%</Label>
          <Slider min={0} max={100} step={1} value={[failure]} onValueChange={(v) => setFailure(Array.isArray(v) ? v[0] : v)} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <Label htmlFor="mean">Latency mean (ms)</Label>
            <Input id="mean" type="number" value={mean} onChange={(e) => setMean(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="jitter">Latency jitter (ms)</Label>
            <Input id="jitter" type="number" value={jitter} onChange={(e) => setJitter(e.target.value)} />
          </div>
        </div>
      </CardContent>
      <CardFooter>
        <Button onClick={save}>Save</Button>
      </CardFooter>
    </Card>
  )
}

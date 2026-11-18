import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { usePoll } from '@/hooks/usePoll'
import { adminConsistency } from '@/lib/api'

export function ConsistencyPanel({ token, saleId }: { token: string; saleId: number | undefined }) {
  const { data } = usePoll(
    () => (saleId ? adminConsistency(token, saleId) : Promise.resolve(undefined)),
    5000,
    [token, saleId],
  )
  return (
    <Card>
      <CardHeader>
        <CardTitle>Consistency</CardTitle>
        <CardDescription>Every count must be 0</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {data &&
          Object.entries(data).map(([k, v]) => (
            <div key={k} className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">{k.replaceAll('_', ' ')}</span>
              <Badge variant={v === 0 ? 'secondary' : 'destructive'} className={v === 0 ? 'bg-green-600 text-white' : ''}>
                {v}
              </Badge>
            </div>
          ))}
      </CardContent>
    </Card>
  )
}

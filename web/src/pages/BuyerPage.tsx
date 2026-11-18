import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { BaselineCard } from '@/components/buyer/BaselineCard'
import { JoinCard } from '@/components/buyer/JoinCard'
import { OfferCard } from '@/components/buyer/OfferCard'
import { ResultCard } from '@/components/buyer/ResultCard'
import { WaitingCard } from '@/components/buyer/WaitingCard'
import { Skeleton } from '@/components/ui/skeleton'
import { useServerClock } from '@/hooks/useServerClock'
import { usePoll } from '@/hooks/usePoll'
import { ApiError, getMe, getSale, grab, join, pay, type Me } from '@/lib/api'
import { loadUserId, saveUserId } from '@/lib/storage'

export function BuyerPage() {
  const [userId, setUserId] = useState<string | null>(loadUserId)
  const [busy, setBusy] = useState(false)
  const clock = useServerClock()

  const sale = usePoll(getSale, 2000)
  const me = usePoll<Me | null>(() => (userId ? getMe(userId) : Promise.resolve(null)), 1000, [userId])

  useEffect(() => clock.observe(sale.data?.server_time), [sale.data, clock])
  useEffect(() => clock.observe(me.data?.server_time), [me.data, clock])

  // A stored id the server no longer knows (e.g. new database): forget it.
  useEffect(() => {
    if (me.error instanceof ApiError && me.error.status === 401) {
      saveUserId(null)
      setUserId(null)
    }
  }, [me.error])

  const noSale = sale.error instanceof ApiError && sale.error.status === 404
  const mine = userId ? me.data : null

  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    try {
      await fn()
    } finally {
      setBusy(false)
    }
  }

  const onJoin = (name: string) =>
    run(async () => {
      try {
        // Only send the stored id while that user is still active; otherwise this is a fresh join.
        const active = mine && ['waiting', 'offered', 'booked', 'browsing'].includes(mine.state)
        const r = await join(name, active && userId ? userId : undefined)
        saveUserId(r.user_id)
        setUserId(r.user_id)
      } catch (e) {
        toast.error(e instanceof ApiError && e.status === 409 ? `Cannot join: ${e.message}` : 'Could not join, try again')
      }
    })

  const onRejoin = () => {
    saveUserId(null)
    setUserId(null)
  }

  const onPay = async (holdId: number, key: string, force: 'fail' | null) => {
    if (!userId) return
    setBusy(true)
    try {
      const r = await pay(userId, holdId, key, force)
      if (r.status === 'failed_retry') toast.error(`Payment failed. ${r.attempts_left} attempt(s) left.`)
      else if (r.status === 'failed_final') toast.error('Payment failed. No attempts left.')
      else if (r.status === 'confirmed') toast.success('Payment received. Seat booked!')
      else toast.error('Your hold expired before payment completed.')
      await me.refresh()
    } catch (e) {
      if (e instanceof ApiError) {
        toast.error(`Payment rejected: ${e.message}`)
        await me.refresh()
      } else {
        toast.error('Network problem. Press Pay again; you will not be charged twice.')
        throw e // OfferCard keeps the idempotency key
      }
    } finally {
      setBusy(false)
    }
  }

  const onGrab = () =>
    run(async () => {
      if (!userId) return
      try {
        await grab(userId)
        await me.refresh()
      } catch (e) {
        if (e instanceof ApiError && e.status === 409 && e.message === 'no_seats') toast('No seats free right now')
        else toast.error('Could not grab a seat')
        await me.refresh()
      }
    })

  let card
  if (!userId) {
    card = <JoinCard sale={sale.data} noSale={noSale} busy={busy} onJoin={onJoin} />
  } else if (!mine) {
    card = <Skeleton className="h-72 w-full" />
  } else if (mine.state === 'waiting') {
    card = <WaitingCard me={mine} sale={sale.data} now={clock.now} />
  } else if (mine.state === 'offered') {
    card = mine.offer ? (
      <OfferCard offer={mine.offer} holdSeconds={sale.data?.hold_seconds ?? 60} now={clock.now} paying={busy} onPay={onPay} />
    ) : (
      <Skeleton className="h-72 w-full" />
    )
  } else if (mine.state === 'browsing') {
    card = <BaselineCard sale={sale.data} busy={busy} onGrab={onGrab} />
  } else {
    card = <ResultCard me={mine} holdSeconds={sale.data?.hold_seconds} onRejoin={onRejoin} />
  }

  return <div className="mx-auto w-full max-w-md px-4 py-8">{card}</div>
}

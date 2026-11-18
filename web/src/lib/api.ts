export type Mode = 'bucket' | 'baseline'
export type SaleStatus = 'idle' | 'running' | 'ended'
export type UserState = 'waiting' | 'offered' | 'booked' | 'left' | 'rejected' | 'browsing'
export type Reason = 'hold_expired' | 'payment_failed' | 'sold_out' | 'sale_stopped'

export interface SeatCounts {
  [status: string]: number
}

export interface Sale {
  sale_id: number
  mode: Mode
  status: SaleStatus
  end_reason: string | null
  seats: SeatCounts
  seats_per_tick: number
  tick_seconds: number
  hold_seconds: number
  next_tick_at: string | null
  queue_length: number
  server_time: string
}

export interface Seat {
  section: string
  row: number
  number: number
  price: number
}

export interface Offer {
  hold_id: number
  seat: Seat
  expires_at: string
  attempts_left: number
}

export interface Me {
  user_id: string
  name: string
  state: UserState
  reason: Reason | null
  position: number | null
  ahead: number | null
  offer: Offer | null
  booking: { booking_id: number; seat: Seat; created_at: string } | null
  server_time: string
}

export interface PayResult {
  status: 'confirmed' | 'failed_retry' | 'failed_final' | 'expired_refunded' | 'expired'
  booking_id: number | null
  attempts_left: number
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(
  method: string,
  path: string,
  opts: { body?: unknown; headers?: Record<string, string> } = {},
): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: {
      ...(opts.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...opts.headers,
    },
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const j = await res.json()
      if (typeof j.detail === 'string') detail = j.detail
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail)
  }
  return (await res.json()) as T
}

const user = (id: string) => ({ 'X-User-Id': id })
const admin = (token: string) => ({ 'X-Admin-Token': token })

// ---- buyer -------------------------------------------------------------

export const getSale = () => request<Sale>('GET', '/api/sale')

export const join = (name: string, userId?: string) =>
  request<{ user_id: string; position: number | null }>('POST', '/api/queue/join', {
    body: { name },
    headers: userId ? user(userId) : undefined,
  })

export const getMe = (userId: string) => request<Me>('GET', '/api/me', { headers: user(userId) })

export const pay = (
  userId: string,
  holdId: number,
  idempotencyKey: string,
  force: 'success' | 'fail' | null = null,
) =>
  request<PayResult>('POST', '/api/pay', {
    body: { hold_id: holdId, idempotency_key: idempotencyKey, force },
    headers: user(userId),
  })

export const grab = (userId: string) =>
  request<Offer>('POST', '/api/baseline/grab', { headers: user(userId) })

// ---- admin -------------------------------------------------------------

export interface SectionSpec {
  name: string
  rows: number
  seats_per_row: number
  price: number
}

export interface SaleConfig {
  mode: Mode
  label: string
  sections: SectionSpec[]
  seats_per_tick: number
  tick_seconds: number
  hold_seconds: number
  max_payment_attempts: number
  tickets_per_user: number
}

export interface GatewayConfig {
  failure_rate: number
  latency_ms_mean: number
  latency_ms_jitter: number
  seed: number | null
}

export type Consistency = Record<string, number>

export const adminCreateSale = (token: string, cfg: SaleConfig) =>
  request<Sale>('POST', '/api/admin/sales', { body: cfg, headers: admin(token) })

export const adminStartSale = (token: string, saleId: number) =>
  request<Sale>('POST', `/api/admin/sales/${saleId}/start`, { headers: admin(token) })

export const adminStopSale = (token: string, saleId: number) =>
  request<Sale>('POST', `/api/admin/sales/${saleId}/stop`, { headers: admin(token) })

export const adminStats = (token: string, window = 120) =>
  request<Record<string, unknown>>('GET', `/api/admin/stats?window=${window}`, {
    headers: admin(token),
  })

export const adminConsistency = (token: string, saleId: number) =>
  request<Consistency>('GET', `/api/admin/sales/${saleId}/consistency`, { headers: admin(token) })

export const adminGetGateway = (token: string) =>
  request<GatewayConfig>('GET', '/api/admin/gateway', { headers: admin(token) })

export const adminSetGateway = (token: string, cfg: GatewayConfig) =>
  request<GatewayConfig>('PUT', '/api/admin/gateway', { body: cfg, headers: admin(token) })

export const traceUrl = (saleId: number) => `/api/admin/sales/${saleId}/trace`

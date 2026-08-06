// Hand-written TS types mirroring the backend's Pydantic response shapes
// (backend/api/routers/*.py). No codegen — the surface is small enough
// that keeping these in sync by hand is simpler than adding a generator.

export interface Wallet {
  id: number
  name: string
  strategy_id: number | null
  initial_cash: number
  cash: number
  start_date: string // ISO date
  status: 'active' | 'retired'
  is_benchmark: boolean
  created_at: string
  retired_at: string | null
}

export interface Strategy {
  id: number
  name: string
  spec_version: number
  spec: Record<string, unknown>
  created_at: string
}

export interface OrderRow {
  id: number
  wallet_id: number
  created_date: string
  ticker: string
  action: 'BUY' | 'SELL'
  cash_amount: number | null
  reason: string
  status: 'pending' | 'executed' | 'cancelled'
  user_decision: 'approve' | 'skip' | null
}

export interface Position {
  id: number
  ticker: string
  shares: number
  avg_entry_price: number
  entry_date: string
  entry_reason: string | null
}

export interface Fill {
  id: number
  timestamp: string
  ticker: string
  action: 'BUY' | 'SELL'
  shares: number
  fill_price: number
  cost_bps_applied: number
  reason: string
}

export interface EquitySnapshot {
  date: string
  cash: number
  positions_value: number
  total_equity: number
  benchmark_equity: number | null
}

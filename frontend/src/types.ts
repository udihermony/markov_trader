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
  spec: StrategySpec
  trust_label: TrustLabel
  created_at: string
}

export type TrustLabel = 'point_in_time' | 'reconstructable' | 'live_only'

export type NodeKind = 'universe' | 'trigger' | 'confirm' | 'veto' | 'exit' | 'size'

export interface NodeSpec {
  id: string
  kind: NodeKind
  type: string
  params: Record<string, unknown>
  on_missing?: 'fail_open' | 'fail_closed' | null
}

export interface SourceRef {
  id: string
  type: string
  params?: Record<string, unknown>
}

export interface StrategySpec {
  spec_version: 2
  name: string
  sources: SourceRef[]
  nodes: NodeSpec[]
  edges: [string, string][]
  costs?: Record<string, unknown>
}

// GET /node-types
export interface ParamField {
  name: string
  type: 'string' | 'number' | 'enum' | 'ticker_list' | 'expression'
  label: string
  options: string[] | null
  default: unknown
}

export interface NodeTypeInfo {
  type: string
  allowed_kinds: NodeKind[]
  maturity: 'standard' | 'experimental' | 'AI'
  params_schema: ParamField[]
}

// POST /strategies/preview
export interface FunnelStage {
  node_id: string
  kind: NodeKind
  type: string
  description: string
  candidates_before: number
  candidates_after: number
  missing_data_count: number
}

export interface PreviewResponse {
  stages: FunnelStage[]
  trust_label: TrustLabel
  descriptions: Record<string, string>
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

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
  parent_id: number | null
  created_by: 'user' | 'ai'
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

// M7 — Lab
export interface BacktestMetrics {
  total_return_pct: number
  benchmark_return_pct: number | null
  max_drawdown_pct: number
  n_trades: number
  n_closed_trades: number
  hit_rate: number | null
  avg_holding_days_calendar: number | null
}

export interface EquityCurvePoint {
  date: string
  total_equity: number
  benchmark_equity: number | null
}

export interface BacktestResultJson {
  metrics: BacktestMetrics
  equity_curve: EquityCurvePoint[]
  fills: { date: string; ticker: string; action: 'BUY' | 'SELL'; shares: number; fill_price: number; reason: string }[]
  scan_param?: string
  scan_value?: number | string
}

export interface Experiment {
  id: number
  strategy_id: number
  hypothesis: string
  expected_outcome: string
  actual_outcome: string | null
  prediction_correct: boolean | null
  period_start: string
  period_end: string
  initiated_by: string
  is_holdout: boolean
  diff_summary: string | null
  result_json: BacktestResultJson | null
  created_at: string
}

export interface Holdout {
  id: number
  start_date: string
  end_date: string
  unseals_total: number
  unseals_used: number
  created_at: string
}

export interface NeighbourhoodScanPoint {
  value: number | string
  total_return_pct: number
  experiment_id: number
}

export interface LuckTestResult {
  real_return_pct: number
  shuffled_returns: number[]
  percentile: number
}

export interface SearchCounter {
  count: number
  best_return_pct: number | null
}

export interface QuestionAnswer {
  answer: string
  detail: string
}

export interface ReportCard {
  has_evidence: boolean
  evidence_source: 'holdout' | 'lab' | null
  beat_doing_nothing: QuestionAnswer | null
  real_or_luck: QuestionAnswer | null
  how_often_right: QuestionAnswer | null
  could_stomach_it: QuestionAnswer | null
}

// M8 — Copilot
export interface ApiKeyInfo {
  provider: string
  created_at: string
}

export interface Conversation {
  id: number
  created_at: string
}

export interface StrategyProposal {
  proposal: true
  kind: 'create' | 'update'
  strategy_id?: number
  name: string
  spec: StrategySpec
  before_spec?: StrategySpec
  trust_label: TrustLabel
  complexity: { score: number; label: 'Low' | 'Medium' | 'High' }
  diff_summary: string
}

export interface ChatMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  proposal_json: StrategyProposal | null
  created_at: string
}

export interface CopilotContext {
  surface: string
  entity_id?: number
}

// M9 — Unattended experiments
export interface UnattendedSessionResult {
  digest: string
  experiment_ids: number[]
  strategies_created: number[]
  tokens: { input: number; output: number }
  calibration: { predicted: number; correct: number }
}

export interface Job {
  id: number
  type: string
  payload_json: Record<string, unknown>
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress: Record<string, unknown> | null
  result_json: UnattendedSessionResult | { error: string } | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface Calibration {
  predicted: number
  correct: number
}

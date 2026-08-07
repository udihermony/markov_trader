import { useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiError } from '../lib/api'
import type { PreviewResponse, Strategy, Wallet } from '../types'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { Input } from '../components/ui/Input'

const AI_NODE_TYPES = new Set(['ai_news_check', 'ai_regime_check'])
// A rough, documented approximation (Claude Sonnet's published per-token
// pricing at a typical judgment call's token size) — shown as an estimate,
// never a bill. See backend/sources/ai_judgment.py's own pricing note.
const ESTIMATED_COST_PER_AI_CALL_USD = 0.003

export function CreateWalletPage() {
  const navigate = useNavigate()
  const { data: strategies } = useQuery({
    queryKey: ['strategies'],
    queryFn: () => api.get<Strategy[]>('/strategies'),
  })

  const [name, setName] = useState('')
  const [strategyId, setStrategyId] = useState<string>('')
  const [initialCash, setInitialCash] = useState('100000')
  const [aiDailyBudget, setAiDailyBudget] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const selectedStrategy = strategies?.find((s) => String(s.id) === strategyId) ?? null
  const hasAiNode = selectedStrategy?.trust_label === 'live_only'
    && selectedStrategy.spec.nodes.some((n) => AI_NODE_TYPES.has(n.type))

  const { data: preview } = useQuery({
    queryKey: ['strategy-preview', selectedStrategy?.id],
    queryFn: () => api.post<PreviewResponse>('/strategies/preview', { spec: selectedStrategy!.spec }),
    enabled: hasAiNode,
  })
  const estimatedCallsPerDay = preview?.stages
    .filter((s) => AI_NODE_TYPES.has(s.type))
    .reduce((sum, s) => sum + s.candidates_before, 0) ?? 0
  const estimatedCostPerDay = estimatedCallsPerDay * ESTIMATED_COST_PER_AI_CALL_USD

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const wallet = await api.post<Wallet>('/wallets', {
        name,
        strategy_id: Number(strategyId),
        initial_cash: Number(initialCash),
        ai_daily_budget_usd: hasAiNode && aiDailyBudget ? Number(aiDailyBudget) : null,
      })
      navigate(`/wallets/${wallet.id}`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create wallet.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="max-w-md">
      <h1 className="text-xl font-semibold text-slate-800 mb-1">New wallet</h1>
      <p className="text-sm text-slate-500 mb-4">
        A wallet is a commitment: one strategy, a starting balance, and today as the start date — it can't be
        backdated.
      </p>
      <Card>
        <form onSubmit={onSubmit} className="space-y-3">
          <div>
            <label className="text-xs font-medium text-slate-500 mb-1 block">Name</label>
            <Input value={name} onChange={(e) => setName(e.target.value)} required />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-500 mb-1 block">Strategy</label>
            <select
              className="w-full px-3 py-1.5 rounded-md border border-slate-300 text-sm focus:outline-none focus:ring-2 focus:ring-slate-400"
              value={strategyId}
              onChange={(e) => setStrategyId(e.target.value)}
              required
            >
              <option value="" disabled>
                Select a strategy…
              </option>
              {strategies?.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
            <p className="text-xs text-slate-400 mt-1">
              Don't see the one you want?{' '}
              <Link to="/strategies/new" className="underline font-medium text-slate-600">
                Build a new strategy
              </Link>
              .
            </p>
          </div>
          {hasAiNode && (
            <div className="rounded-md border border-amber-200 bg-amber-50 p-3 space-y-2">
              <div className="flex items-center gap-2">
                <Badge tone="amber">AI</Badge>
                <p className="text-xs text-amber-800">
                  This strategy has an AI check that runs for real once the wallet is live — estimated{' '}
                  ~${estimatedCostPerDay.toFixed(2)}/day ({estimatedCallsPerDay} check{estimatedCallsPerDay === 1 ? '' : 's'}).
                </p>
              </div>
              <div>
                <label className="text-xs font-medium text-slate-600 mb-1 block">
                  Daily AI budget ($, optional — blank means uncapped)
                </label>
                <Input
                  type="number" min={0} step="0.01" value={aiDailyBudget}
                  onChange={(e) => setAiDailyBudget(e.target.value)}
                />
              </div>
            </div>
          )}
          <div>
            <label className="text-xs font-medium text-slate-500 mb-1 block">Starting balance</label>
            <Input
              type="number"
              min={1}
              step="0.01"
              value={initialCash}
              onChange={(e) => setInitialCash(e.target.value)}
              required
            />
          </div>
          {error && <p className="text-sm text-red-600">{error}</p>}
          <Button type="submit" disabled={submitting || !strategyId} className="w-full">
            {submitting ? 'Creating…' : 'Create wallet'}
          </Button>
        </form>
      </Card>
    </div>
  )
}

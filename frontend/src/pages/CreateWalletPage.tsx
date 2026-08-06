import { useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api, ApiError } from '../lib/api'
import type { Strategy, Wallet } from '../types'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { Input } from '../components/ui/Input'

export function CreateWalletPage() {
  const navigate = useNavigate()
  const { data: strategies } = useQuery({
    queryKey: ['strategies'],
    queryFn: () => api.get<Strategy[]>('/strategies'),
  })

  const [name, setName] = useState('')
  const [strategyId, setStrategyId] = useState<string>('')
  const [initialCash, setInitialCash] = useState('100000')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const wallet = await api.post<Wallet>('/wallets', {
        name,
        strategy_id: Number(strategyId),
        initial_cash: Number(initialCash),
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
            {strategies && strategies.length === 1 && (
              <p className="text-xs text-slate-400 mt-1">
                Only your default benchmark strategy exists so far — the strategy builder is coming in a later
                milestone.
              </p>
            )}
          </div>
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

import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  CartesianGrid, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis,
} from 'recharts'
import { api } from '../../lib/api'
import type { LuckTestResult } from '../../types'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { Input } from '../ui/Input'

function isoDaysAgo(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}

export function LuckTest({
  strategyId, onResult,
}: { strategyId: number; onResult: (nullSamples: number[]) => void }) {
  const [periodStart, setPeriodStart] = useState(isoDaysAgo(365))
  const [periodEnd, setPeriodEnd] = useState(isoDaysAgo(1))
  const [nShuffles, setNShuffles] = useState(30)

  const luckMutation = useMutation({
    mutationFn: () =>
      api.post<LuckTestResult>('/experiments/luck-test', {
        strategy_id: strategyId, period_start: periodStart, period_end: periodEnd, n_shuffles: nShuffles,
      }),
    onSuccess: (result) => onResult(result.shuffled_returns),
  })

  const result = luckMutation.data
  const scatterData = result?.shuffled_returns.map((r, i) => ({ x: i, y: r })) ?? []

  return (
    <Card>
      <h2 className="text-sm font-semibold text-slate-700 mb-1">Luck test</h2>
      <p className="text-xs text-slate-400 mb-3">
        Re-runs this strategy with randomly timed entries, keeping the same trade frequency, universe, exits, and
        sizing. Shows where your real result falls among the random ones.
      </p>
      <div className="space-y-3">
        <div className="flex gap-3">
          <div>
            <label className="text-xs font-medium text-slate-500 mb-1 block">From</label>
            <Input type="date" value={periodStart} onChange={(e) => setPeriodStart(e.target.value)} />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-500 mb-1 block">To</label>
            <Input type="date" value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)} />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-500 mb-1 block">Shuffles</label>
            <Input
              type="number" min={1} max={200} className="w-20" value={nShuffles}
              onChange={(e) => setNShuffles(Number(e.target.value))}
            />
          </div>
        </div>
        <Button onClick={() => luckMutation.mutate()} disabled={luckMutation.isPending}>
          {luckMutation.isPending ? 'Running…' : 'Run luck test'}
        </Button>
        {luckMutation.isError && <p className="text-sm text-red-600">Could not run the luck test.</p>}
      </div>

      {result && (
        <div className="mt-4">
          <p className="text-sm text-slate-700 mb-2">
            Your real result ({result.real_return_pct >= 0 ? '+' : ''}{result.real_return_pct.toFixed(1)}%) did better
            than <span className="font-semibold">{result.percentile.toFixed(0)}%</span> of{' '}
            {result.shuffled_returns.length} random-timing runs with the same trade frequency.
          </p>
          <div className="h-40 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis type="number" dataKey="x" hide />
                <YAxis
                  type="number" dataKey="y" tick={{ fontSize: 11, fill: '#64748b' }} width={48}
                  tickFormatter={(v) => `${v}%`}
                />
                <ZAxis range={[40, 40]} />
                <Tooltip formatter={(v) => `${Number(v).toFixed(1)}%`} />
                <Scatter data={scatterData} fill="#94a3b8" />
                <ReferenceLine y={result.real_return_pct} stroke="#0f172a" strokeWidth={2} />
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </Card>
  )
}

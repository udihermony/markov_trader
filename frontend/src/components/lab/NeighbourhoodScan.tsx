import { useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../../lib/api'
import { parseExpression, composeExpression } from '../../lib/expressions'
import { useNodeTypes } from '../../lib/nodeTypes'
import type { NeighbourhoodScanPoint, StrategySpec } from '../../types'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { Input } from '../ui/Input'

interface ScannableParam {
  key: string
  nodeId: string
  paramName: string
  label: string
  isExpression: boolean
  current: number
}

function isoDaysAgo(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}

export function NeighbourhoodScan({ strategyId, spec }: { strategyId: number; spec: StrategySpec }) {
  const queryClient = useQueryClient()
  const { data: nodeTypes } = useNodeTypes()

  const scannable = useMemo<ScannableParam[]>(() => {
    if (!nodeTypes) return []
    const out: ScannableParam[] = []
    for (const node of spec.nodes) {
      const info = nodeTypes.find((t) => t.type === node.type)
      if (!info) continue
      for (const field of info.params_schema) {
        if (field.type === 'number') {
          const current = Number(node.params[field.name] ?? field.default ?? 0)
          out.push({
            key: `${node.id}.${field.name}`, nodeId: node.id, paramName: field.name,
            label: `${node.type}: ${field.label}`, isExpression: false, current,
          })
        } else if (field.type === 'expression') {
          const { fn, window } = parseExpression(node.params[field.name] as string | undefined)
          if (fn === 'price') continue // no window to scan
          out.push({
            key: `${node.id}.${field.name}`, nodeId: node.id, paramName: field.name,
            label: `${node.type}: ${field.label} (${fn})`, isExpression: true, current: window,
          })
        }
      }
    }
    return out
  }, [nodeTypes, spec.nodes])

  const [selectedKey, setSelectedKey] = useState<string>('')
  const [periodStart, setPeriodStart] = useState(isoDaysAgo(365))
  const [periodEnd, setPeriodEnd] = useState(isoDaysAgo(1))
  const [hypothesis, setHypothesis] = useState('')
  const [expectedOutcome, setExpectedOutcome] = useState('')

  const selected = scannable.find((s) => s.key === selectedKey) ?? null

  const scanMutation = useMutation({
    mutationFn: () => {
      if (!selected) throw new Error('no param selected')
      const node = spec.nodes.find((n) => n.id === selected.nodeId)!
      const spreads = selected.isExpression ? [-4, -2, 0, 2, 4] : [-0.4, -0.2, 0, 0.2, 0.4]
      const values = selected.isExpression
        ? spreads.map((d) => Math.max(1, Math.round(selected.current + d)))
        : spreads.map((f) => Number((selected.current * (1 + f)).toFixed(4)))
      const finalValues = selected.isExpression
        ? values.map((w) => composeExpression(parseExpression(node.params[selected.paramName] as string).fn, w))
        : values
      return api.post<NeighbourhoodScanPoint[]>('/experiments/neighbourhood-scan', {
        strategy_id: strategyId, node_id: selected.nodeId, param_name: selected.paramName,
        values: finalValues, period_start: periodStart, period_end: periodEnd,
        hypothesis, expected_outcome: expectedOutcome,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['search-counter', strategyId] })
      queryClient.invalidateQueries({ queryKey: ['experiments', strategyId] })
      queryClient.invalidateQueries({ queryKey: ['report-card', strategyId] })
    },
  })

  return (
    <Card>
      <h2 className="text-sm font-semibold text-slate-700 mb-1">Neighbourhood scan</h2>
      <p className="text-xs text-slate-400 mb-3">
        Tries nearby values of one setting. A lone bright bar surrounded by weak ones is a warning sign, not a good
        sign — it usually means you found noise, not a real pattern. Every point here counts toward the search
        counter above.
      </p>
      <div className="space-y-3">
        <select
          className="w-full px-3 py-1.5 rounded-md border border-slate-300 text-sm"
          value={selectedKey}
          onChange={(e) => setSelectedKey(e.target.value)}
        >
          <option value="">Select a setting to scan…</option>
          {scannable.map((s) => (
            <option key={s.key} value={s.key}>
              {s.label} (currently {s.current})
            </option>
          ))}
        </select>
        <Input placeholder="Hypothesis for this scan" value={hypothesis} onChange={(e) => setHypothesis(e.target.value)} />
        <Input
          placeholder="What you expect to happen"
          value={expectedOutcome}
          onChange={(e) => setExpectedOutcome(e.target.value)}
        />
        <div className="flex gap-3">
          <div>
            <label className="text-xs font-medium text-slate-500 mb-1 block">From</label>
            <Input type="date" value={periodStart} onChange={(e) => setPeriodStart(e.target.value)} />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-500 mb-1 block">To</label>
            <Input type="date" value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)} />
          </div>
        </div>
        <Button
          onClick={() => scanMutation.mutate()}
          disabled={scanMutation.isPending || !selected || !hypothesis.trim() || !expectedOutcome.trim()}
        >
          {scanMutation.isPending ? 'Scanning…' : 'Run scan'}
        </Button>
        {scanMutation.isError && <p className="text-sm text-red-600">Could not run the scan.</p>}
      </div>

      {scanMutation.data && (
        <div className="h-56 w-full mt-4">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={scanMutation.data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="value" tick={{ fontSize: 11, fill: '#64748b' }} />
              <YAxis tick={{ fontSize: 11, fill: '#64748b' }} width={48} tickFormatter={(v) => `${v}%`} />
              <Tooltip formatter={(v) => `${Number(v).toFixed(1)}%`} />
              <Bar dataKey="total_return_pct" name="Return">
                {scanMutation.data.map((p) => (
                  <Cell key={p.experiment_id} fill={p.value === selected?.current ? '#0f172a' : '#94a3b8'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </Card>
  )
}

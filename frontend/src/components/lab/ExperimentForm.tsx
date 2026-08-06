import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../lib/api'
import type { Experiment } from '../../types'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { Input } from '../ui/Input'

function isoDaysAgo(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}

export function ExperimentForm({ strategyId }: { strategyId: number }) {
  const queryClient = useQueryClient()
  const [hypothesis, setHypothesis] = useState('')
  const [expectedOutcome, setExpectedOutcome] = useState('')
  const [periodStart, setPeriodStart] = useState(isoDaysAgo(365))
  const [periodEnd, setPeriodEnd] = useState(isoDaysAgo(1))
  const [lastResult, setLastResult] = useState<Experiment | null>(null)

  const runMutation = useMutation({
    mutationFn: () =>
      api.post<Experiment>('/experiments', {
        strategy_id: strategyId, hypothesis, expected_outcome: expectedOutcome,
        period_start: periodStart, period_end: periodEnd,
      }),
    onSuccess: (experiment) => {
      setLastResult(experiment)
      queryClient.invalidateQueries({ queryKey: ['experiments', strategyId] })
      queryClient.invalidateQueries({ queryKey: ['search-counter', strategyId] })
      queryClient.invalidateQueries({ queryKey: ['report-card', strategyId] })
    },
  })

  const predictionMutation = useMutation({
    mutationFn: (correct: boolean) =>
      api.post<Experiment>(`/experiments/${lastResult!.id}/prediction-correct`, { correct }),
    onSuccess: (experiment) => {
      setLastResult(experiment)
      queryClient.invalidateQueries({ queryKey: ['experiments', strategyId] })
    },
  })

  return (
    <Card>
      <h2 className="text-sm font-semibold text-slate-700 mb-1">Run an experiment</h2>
      <p className="text-xs text-slate-400 mb-3">
        A hypothesis is a question with an expected answer, not a title — this is the main brake on blind search.
      </p>
      <div className="space-y-3">
        <div>
          <label className="text-xs font-medium text-slate-500 mb-1 block">Your hypothesis</label>
          <Input
            placeholder="e.g. a faster average will catch trends earlier"
            value={hypothesis}
            onChange={(e) => setHypothesis(e.target.value)}
          />
        </div>
        <div>
          <label className="text-xs font-medium text-slate-500 mb-1 block">What you expect to happen</label>
          <Input
            placeholder="e.g. more trades, similar hit rate"
            value={expectedOutcome}
            onChange={(e) => setExpectedOutcome(e.target.value)}
          />
        </div>
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
          onClick={() => runMutation.mutate()}
          disabled={runMutation.isPending || !hypothesis.trim() || !expectedOutcome.trim()}
        >
          {runMutation.isPending ? 'Running…' : 'Run experiment'}
        </Button>
        {runMutation.isError && <p className="text-sm text-red-600">Could not run this experiment.</p>}
      </div>

      {lastResult && (
        <div className="mt-4 pt-4 border-t border-slate-200">
          <p className="text-sm text-slate-700">{lastResult.actual_outcome}</p>
          {lastResult.prediction_correct === null ? (
            <div className="mt-2 flex items-center gap-2">
              <span className="text-xs text-slate-500">Were you right?</span>
              <Button variant="secondary" onClick={() => predictionMutation.mutate(true)}>
                Yes
              </Button>
              <Button variant="secondary" onClick={() => predictionMutation.mutate(false)}>
                No
              </Button>
            </div>
          ) : (
            <p className="text-xs text-slate-400 mt-1">
              You said you'd be {lastResult.prediction_correct ? 'right' : 'wrong'}.
            </p>
          )}
        </div>
      )}
    </Card>
  )
}

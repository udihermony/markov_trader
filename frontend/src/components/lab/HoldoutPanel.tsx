import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../lib/api'
import type { Experiment, Holdout } from '../../types'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { Input } from '../ui/Input'

function isoDaysAgo(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}

export function HoldoutPanel({ strategyId }: { strategyId: number }) {
  const queryClient = useQueryClient()
  const { data: holdout } = useQuery({
    queryKey: ['holdout'],
    queryFn: () => api.get<Holdout | null>('/holdouts'),
  })

  const [startDate, setStartDate] = useState(isoDaysAgo(180))
  const [endDate, setEndDate] = useState(isoDaysAgo(30))
  const [hypothesis, setHypothesis] = useState('')
  const [expectedOutcome, setExpectedOutcome] = useState('')
  const [lastUnseal, setLastUnseal] = useState<Experiment | null>(null)
  const [confirmingSeal, setConfirmingSeal] = useState(false)
  const [confirmingUnseal, setConfirmingUnseal] = useState(false)

  const sealMutation = useMutation({
    mutationFn: () => api.post<Holdout>('/holdouts', { start_date: startDate, end_date: endDate }),
    onSuccess: () => {
      setConfirmingSeal(false)
      queryClient.invalidateQueries({ queryKey: ['holdout'] })
    },
  })

  const unsealMutation = useMutation({
    mutationFn: () =>
      api.post<{ holdout: Holdout; experiment: Experiment }>(`/holdouts/${holdout!.id}/unseal`, {
        strategy_id: strategyId, hypothesis, expected_outcome: expectedOutcome,
      }),
    onSuccess: ({ experiment }) => {
      setLastUnseal(experiment)
      setConfirmingUnseal(false)
      queryClient.invalidateQueries({ queryKey: ['holdout'] })
      queryClient.invalidateQueries({ queryKey: ['experiments', strategyId] })
      queryClient.invalidateQueries({ queryKey: ['report-card', strategyId] })
    },
  })

  return (
    <Card>
      <h2 className="text-sm font-semibold text-slate-700 mb-1">Sealed holdout</h2>
      <p className="text-xs text-slate-400 mb-3">
        A period neither you nor the AI can freely test against — the only honest historical number in the app.
      </p>

      {!holdout ? (
        <div className="space-y-3">
          <div className="flex gap-3">
            <div>
              <label className="text-xs font-medium text-slate-500 mb-1 block">From</label>
              <Input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500 mb-1 block">To</label>
              <Input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
            </div>
          </div>
          {!confirmingSeal ? (
            <Button onClick={() => setConfirmingSeal(true)}>Seal my holdout</Button>
          ) : (
            <div className="space-y-2">
              <p className="text-sm text-amber-700 bg-amber-50 rounded px-2 py-1">
                This can never be changed or undone once sealed.
              </p>
              <div className="flex gap-2">
                <Button variant="danger" onClick={() => sealMutation.mutate()} disabled={sealMutation.isPending}>
                  {sealMutation.isPending ? 'Sealing…' : 'Yes, seal it'}
                </Button>
                <Button variant="secondary" onClick={() => setConfirmingSeal(false)}>
                  Cancel
                </Button>
              </div>
            </div>
          )}
          {sealMutation.isError && <p className="text-sm text-red-600">Could not seal a holdout.</p>}
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-sm text-slate-700">
            {holdout.start_date} → {holdout.end_date} — {holdout.unseals_total - holdout.unseals_used} of{' '}
            {holdout.unseals_total} unseals remaining
          </p>
          {holdout.unseals_used < holdout.unseals_total ? (
            <>
              <Input
                placeholder="Hypothesis for this holdout test"
                value={hypothesis}
                onChange={(e) => setHypothesis(e.target.value)}
              />
              <Input
                placeholder="What you expect to happen"
                value={expectedOutcome}
                onChange={(e) => setExpectedOutcome(e.target.value)}
              />
              {!confirmingUnseal ? (
                <Button
                  variant="danger"
                  onClick={() => setConfirmingUnseal(true)}
                  disabled={!hypothesis.trim() || !expectedOutcome.trim()}
                >
                  Test against holdout
                </Button>
              ) : (
                <div className="space-y-2">
                  <p className="text-sm text-amber-700 bg-amber-50 rounded px-2 py-1">
                    This will permanently spend 1 of your {holdout.unseals_total - holdout.unseals_used} remaining
                    holdout tests.
                  </p>
                  <div className="flex gap-2">
                    <Button variant="danger" onClick={() => unsealMutation.mutate()} disabled={unsealMutation.isPending}>
                      {unsealMutation.isPending ? 'Testing…' : 'Yes, spend it'}
                    </Button>
                    <Button variant="secondary" onClick={() => setConfirmingUnseal(false)}>
                      Cancel
                    </Button>
                  </div>
                </div>
              )}
              {unsealMutation.isError && <p className="text-sm text-red-600">Could not run the holdout test.</p>}
            </>
          ) : (
            <p className="text-sm text-slate-400">No unseals remaining.</p>
          )}
          {lastUnseal && <p className="text-sm text-slate-700 pt-2 border-t border-slate-200">{lastUnseal.actual_outcome}</p>}
        </div>
      )}
    </Card>
  )
}

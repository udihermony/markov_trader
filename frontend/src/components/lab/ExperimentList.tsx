import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../lib/api'
import type { Experiment } from '../../types'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'

export function ExperimentList({ strategyId }: { strategyId: number }) {
  const queryClient = useQueryClient()
  const { data: experiments, isLoading } = useQuery({
    queryKey: ['experiments', strategyId],
    queryFn: () => api.get<Experiment[]>(`/experiments?strategy_id=${strategyId}`),
  })

  const predictionMutation = useMutation({
    mutationFn: ({ id, correct }: { id: number; correct: boolean }) =>
      api.post<Experiment>(`/experiments/${id}/prediction-correct`, { correct }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['experiments', strategyId] }),
  })

  return (
    <Card>
      <h2 className="text-sm font-semibold text-slate-700 mb-3">Experiment history</h2>
      {isLoading && <p className="text-sm text-slate-400">Loading…</p>}
      {experiments && experiments.length === 0 && (
        <p className="text-sm text-slate-400">No experiments run yet.</p>
      )}
      {experiments && experiments.length > 0 && (
        <div className="space-y-3">
          {[...experiments].reverse().map((e) => (
            <div key={e.id} className="border border-slate-200 rounded-md p-3">
              <div className="flex items-center gap-2 flex-wrap mb-1">
                {e.is_holdout && <Badge tone="amber">Holdout</Badge>}
                <span className="text-xs text-slate-400">
                  {e.period_start} → {e.period_end}
                </span>
                <span className="text-xs text-slate-400">· {e.diff_summary}</span>
              </div>
              <p className="text-sm text-slate-700">{e.hypothesis}</p>
              <p className="text-sm text-slate-600 mt-1">{e.actual_outcome}</p>
              {e.prediction_correct === null ? (
                <div className="mt-2 flex items-center gap-2">
                  <span className="text-xs text-slate-500">Were you right?</span>
                  <Button variant="secondary" onClick={() => predictionMutation.mutate({ id: e.id, correct: true })}>
                    Yes
                  </Button>
                  <Button variant="secondary" onClick={() => predictionMutation.mutate({ id: e.id, correct: false })}>
                    No
                  </Button>
                </div>
              ) : (
                <p className="text-xs text-slate-400 mt-1">
                  {e.prediction_correct ? 'Prediction was right' : 'Prediction was wrong'}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}

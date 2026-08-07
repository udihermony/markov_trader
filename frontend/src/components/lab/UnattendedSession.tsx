import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../lib/api'
import type { Job } from '../../types'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { Input } from '../ui/Input'

const POLL_INTERVAL_MS = 3000

export function UnattendedSession({ strategyId }: { strategyId: number }) {
  const queryClient = useQueryClient()
  const [goal, setGoal] = useState('')
  const [budget, setBudget] = useState(5)
  const [activeJobId, setActiveJobId] = useState<number | null>(null)

  const startMutation = useMutation({
    mutationFn: () =>
      api.post<Job>('/jobs/unattended-sessions', { strategy_id: strategyId, goal, budget }),
    onSuccess: (job) => {
      setActiveJobId(job.id)
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
    },
  })

  const { data: activeJob } = useQuery({
    queryKey: ['job', activeJobId],
    queryFn: () => api.get<Job>(`/jobs/${activeJobId}`),
    enabled: activeJobId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'pending' || status === 'running' ? POLL_INTERVAL_MS : false
    },
  })

  useEffect(() => {
    if (activeJob && (activeJob.status === 'completed' || activeJob.status === 'failed')) {
      queryClient.invalidateQueries({ queryKey: ['strategies'] })
      queryClient.invalidateQueries({ queryKey: ['experiments', strategyId] })
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeJob?.status])

  const { data: recentJobs } = useQuery({
    queryKey: ['jobs'],
    queryFn: () => api.get<Job[]>('/jobs'),
  })

  const running = activeJob?.status === 'pending' || activeJob?.status === 'running'
  const result = activeJob?.status === 'completed' && activeJob.result_json && 'digest' in activeJob.result_json
    ? activeJob.result_json
    : null
  const error = activeJob?.status === 'failed' && activeJob.result_json && 'error' in activeJob.result_json
    ? activeJob.result_json.error
    : null

  return (
    <Card>
      <h2 className="text-sm font-semibold text-slate-700 mb-1">Unattended session</h2>
      <p className="text-xs text-slate-400 mb-3">
        Give the AI a goal and a budget, then walk away — come back to a digest, not a dump.
      </p>

      <div className="space-y-2">
        <Input
          placeholder="Goal, e.g. find a confirm signal that improves the hit rate"
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          disabled={running}
        />
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-500">Budget (experiments)</label>
          <Input
            type="number" min={1} max={50} className="w-20" value={budget}
            onChange={(e) => setBudget(Number(e.target.value))} disabled={running}
          />
        </div>
        <Button onClick={() => startMutation.mutate()} disabled={running || !goal.trim()}>
          {running ? 'Running…' : 'Start session'}
        </Button>
        {startMutation.isError && <p className="text-sm text-red-600">Could not start the session.</p>}
      </div>

      {activeJob && (
        <div className="mt-4 pt-4 border-t border-slate-200">
          {running && <p className="text-sm text-slate-500">Session {activeJob.status}…</p>}
          {result && (
            <div className="space-y-2">
              <p className="text-sm text-slate-700 whitespace-pre-wrap">{result.digest}</p>
              <div className="flex items-center gap-2 flex-wrap text-xs text-slate-500">
                <Badge tone="neutral">
                  {result.experiment_ids.length} experiment{result.experiment_ids.length === 1 ? '' : 's'}
                </Badge>
                <Badge tone="neutral">{result.strategies_created.length} variant(s) created</Badge>
                <Badge tone="neutral">
                  Calibration: {result.calibration.correct}/{result.calibration.predicted}
                </Badge>
                <span>{result.tokens.input + result.tokens.output} tokens used</span>
              </div>
            </div>
          )}
          {error && <p className="text-sm text-red-600">Session failed: {error}</p>}
        </div>
      )}

      {recentJobs && recentJobs.length > 0 && (
        <div className="mt-4 pt-4 border-t border-slate-200">
          <p className="text-xs font-medium text-slate-500 mb-2">Recent sessions</p>
          <div className="space-y-1">
            {recentJobs.slice(0, 5).map((j) => (
              <button
                key={j.id}
                className="block text-xs text-slate-500 hover:text-slate-700 underline"
                onClick={() => setActiveJobId(j.id)}
              >
                {new Date(j.created_at).toLocaleString()} — {j.status}
              </button>
            ))}
          </div>
        </div>
      )}
    </Card>
  )
}

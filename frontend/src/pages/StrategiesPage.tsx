import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { computeComplexity } from '../lib/complexity'
import type { Strategy, TrustLabel } from '../types'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'

const TRUST_LABELS: Record<TrustLabel, { text: string; tone: 'green' | 'amber' | 'red' }> = {
  point_in_time: { text: 'Fully backtestable', tone: 'green' },
  reconstructable: { text: 'Backtestable with caveats', tone: 'amber' },
  live_only: { text: 'Forward-only', tone: 'red' },
}

export function StrategiesPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { data: strategies, isLoading } = useQuery({
    queryKey: ['strategies'],
    queryFn: () => api.get<Strategy[]>('/strategies'),
  })

  const duplicateMutation = useMutation({
    mutationFn: (strategy: Strategy) =>
      api.post<Strategy>('/strategies', {
        name: `${strategy.name} (copy)`, spec: strategy.spec, parent_id: strategy.id,
      }),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ['strategies'] })
      navigate(`/strategies/${created.id}/edit`)
    },
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-800">Strategies</h1>
          <p className="text-sm text-slate-500">Build a strategy from plain-language building blocks — no JSON.</p>
        </div>
        <Link to="/strategies/new">
          <Button>New strategy</Button>
        </Link>
      </div>

      {isLoading ? (
        <p className="text-sm text-slate-400">Loading…</p>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {strategies?.map((strategy) => {
            const complexity = computeComplexity(strategy.spec)
            const trust = TRUST_LABELS[strategy.trust_label]
            return (
              <Card key={strategy.id} className="hover:border-slate-400 transition-colors">
                <Link to={`/strategies/${strategy.id}/edit`}>
                  <p className="font-semibold text-slate-800 mb-2">{strategy.name}</p>
                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge tone={trust.tone}>{trust.text}</Badge>
                    <Badge tone="neutral">{complexity.label} complexity</Badge>
                    {strategy.created_by === 'ai' && <Badge tone="neutral">Built by AI</Badge>}
                  </div>
                  <p className="text-xs text-slate-400 mt-2">
                    Created {new Date(strategy.created_at).toLocaleDateString()}
                  </p>
                </Link>
                <div className="flex items-center gap-2 mt-3 pt-3 border-t border-slate-100">
                  <Link to={`/lab?strategy=${strategy.id}`}>
                    <Button variant="secondary">Test in Lab</Button>
                  </Link>
                  <Button variant="ghost" onClick={() => duplicateMutation.mutate(strategy)}>
                    Duplicate
                  </Button>
                </div>
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}

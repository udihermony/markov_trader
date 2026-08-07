import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../../lib/api'
import type { Strategy, StrategyProposal, TrustLabel } from '../../types'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'

const TRUST_LABELS: Record<TrustLabel, { text: string; tone: 'green' | 'amber' | 'red' }> = {
  point_in_time: { text: 'Fully backtestable', tone: 'green' },
  reconstructable: { text: 'Backtestable with caveats', tone: 'amber' },
  live_only: { text: 'Forward-only', tone: 'red' },
}

export function StrategyProposalCard({ proposal }: { proposal: StrategyProposal }) {
  const queryClient = useQueryClient()
  const [applied, setApplied] = useState<Strategy | null>(null)
  const [undone, setUndone] = useState(false)
  const [discarded, setDiscarded] = useState(false)

  const applyMutation = useMutation({
    mutationFn: () =>
      proposal.kind === 'create'
        ? api.post<Strategy>('/strategies', { name: proposal.name, spec: proposal.spec })
        : api.put<Strategy>(`/strategies/${proposal.strategy_id}`, { spec: proposal.spec }),
    onSuccess: (strategy) => {
      setApplied(strategy)
      queryClient.invalidateQueries({ queryKey: ['strategies'] })
    },
  })

  // Undo only applies to an update — a fresh create has no "before" to
  // revert to, and there's no delete endpoint for strategies (matching
  // this app's general "retire, don't delete" posture toward records).
  const undoMutation = useMutation({
    mutationFn: () => api.put<Strategy>(`/strategies/${proposal.strategy_id}`, { spec: proposal.before_spec }),
    onSuccess: () => {
      setUndone(true)
      queryClient.invalidateQueries({ queryKey: ['strategies'] })
    },
  })

  if (discarded) {
    return <p className="text-xs text-slate-400 italic">Proposal discarded.</p>
  }

  const trust = TRUST_LABELS[proposal.trust_label]
  const reviewLink =
    proposal.kind === 'create'
      ? { to: '/strategies/new/build', state: { spec: proposal.spec } }
      : { to: `/strategies/${proposal.strategy_id}/edit`, state: { spec: { ...proposal.spec, name: proposal.name } } }

  return (
    <Card className="bg-slate-50">
      <p className="font-semibold text-slate-800 mb-1">{proposal.name}</p>
      <div className="flex items-center gap-2 flex-wrap mb-2">
        <Badge tone={trust.tone}>{trust.text}</Badge>
        <Badge tone="neutral">{proposal.complexity.label} complexity</Badge>
      </div>
      <p className="text-xs text-slate-500 mb-3">{proposal.diff_summary}</p>

      {undone ? (
        <p className="text-xs text-slate-400 italic">Reverted.</p>
      ) : applied ? (
        <div className="flex items-center gap-2">
          <span className="text-xs text-emerald-700">Applied.</span>
          <Link to={`/strategies/${applied.id}/edit`} className="text-xs underline text-slate-600">
            Open
          </Link>
          {proposal.kind === 'update' && (
            <Button variant="secondary" onClick={() => undoMutation.mutate()} disabled={undoMutation.isPending}>
              Undo
            </Button>
          )}
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <Button onClick={() => applyMutation.mutate()} disabled={applyMutation.isPending}>
            {applyMutation.isPending ? 'Applying…' : 'Apply'}
          </Button>
          <Button variant="secondary" onClick={() => setDiscarded(true)}>
            Discard
          </Button>
          <Link to={reviewLink.to} state={reviewLink.state} className="text-xs underline text-slate-600">
            Review in builder
          </Link>
        </div>
      )}
      {applyMutation.isError && <p className="text-sm text-red-600 mt-2">Could not apply this proposal.</p>}
    </Card>
  )
}

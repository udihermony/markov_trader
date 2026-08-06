import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { luckBaseline } from '../../lib/luckBaseline'
import type { SearchCounter } from '../../types'
import { Card } from '../ui/Card'

export function SearchCounterBanner({
  strategyId, luckNullSamples,
}: { strategyId: number; luckNullSamples: number[] | null }) {
  const { data: counter } = useQuery({
    queryKey: ['search-counter', strategyId],
    queryFn: () => api.get<SearchCounter>(`/strategies/${strategyId}/search-counter`),
  })

  if (!counter) return null

  const baseline = luckNullSamples ? luckBaseline(luckNullSamples, counter.count) : null

  return (
    <Card className="bg-slate-50">
      <p className="text-sm text-slate-700">
        <span className="font-semibold">{counter.count}</span> experiment{counter.count === 1 ? '' : 's'} run on this
        idea so far
        {counter.best_return_pct !== null && (
          <>
            ; your best is <span className="font-semibold">{counter.best_return_pct >= 0 ? '+' : ''}{counter.best_return_pct.toFixed(1)}%</span>
          </>
        )}
        .
      </p>
      {baseline !== null ? (
        <p className="text-xs text-slate-500 mt-1">
          At {counter.count} tries, pure chance alone tends to produce something around{' '}
          <span className="font-medium">{baseline >= 0 ? '+' : ''}{baseline.toFixed(1)}%</span> — based on your last
          luck test.
        </p>
      ) : (
        <p className="text-xs text-slate-400 mt-1">
          Run a luck test below to see how your best result compares to pure chance at this search count.
        </p>
      )}
    </Card>
  )
}

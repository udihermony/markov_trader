import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { api } from '../lib/api'
import type { Strategy } from '../types'
import { Card } from '../components/ui/Card'
import { ExperimentForm } from '../components/lab/ExperimentForm'
import { ExperimentList } from '../components/lab/ExperimentList'
import { HoldoutPanel } from '../components/lab/HoldoutPanel'
import { LuckTest } from '../components/lab/LuckTest'
import { NeighbourhoodScan } from '../components/lab/NeighbourhoodScan'
import { ReportCard } from '../components/lab/ReportCard'
import { SearchCounterBanner } from '../components/lab/SearchCounterBanner'
import { UnattendedSession } from '../components/lab/UnattendedSession'

export function LabPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const { data: strategies } = useQuery({
    queryKey: ['strategies'],
    queryFn: () => api.get<Strategy[]>('/strategies'),
  })

  const strategyIdParam = searchParams.get('strategy')
  const strategyId = strategyIdParam ? Number(strategyIdParam) : strategies?.[0]?.id

  const [luckNullSamples, setLuckNullSamples] = useState<number[] | null>(null)
  const selected = strategies?.find((s) => s.id === strategyId) ?? null

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-800">Lab</h1>
        <p className="text-sm text-slate-500">
          Ideas are tested here, and mostly die. Every result is labelled with how much to trust it.
        </p>
      </div>

      <select
        className="w-full max-w-sm px-3 py-1.5 rounded-md border border-slate-300 text-sm"
        value={strategyId ?? ''}
        onChange={(e) => {
          setSearchParams({ strategy: e.target.value })
          setLuckNullSamples(null)
        }}
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

      {selected && (
        <>
          {selected.trust_label === 'live_only' && (
            <Card className="border-amber-200 bg-amber-50">
              <p className="text-sm text-amber-800">
                This strategy has an AI check in it. AI checks are switched off for Lab experiments — every
                result below reflects the strategy without its AI judgment (an LLM asked about a past date
                could just remember what happened, so it can never be honestly backtested).
              </p>
            </Card>
          )}
          <SearchCounterBanner strategyId={selected.id} luckNullSamples={luckNullSamples} />
          <ExperimentForm strategyId={selected.id} />
          <UnattendedSession strategyId={selected.id} />
          <NeighbourhoodScan strategyId={selected.id} spec={selected.spec} />
          <LuckTest strategyId={selected.id} onResult={setLuckNullSamples} />
          <HoldoutPanel strategyId={selected.id} />
          <ReportCard strategyId={selected.id} />
          <ExperimentList strategyId={selected.id} />
        </>
      )}
    </div>
  )
}

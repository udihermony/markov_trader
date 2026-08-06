import type { FunnelStage, TrustLabel } from '../../types'
import { COMPLEXITY_CAVEAT, type Complexity } from '../../lib/complexity'
import { Badge } from '../ui/Badge'
import { Card } from '../ui/Card'

const TRUST_LABELS: Record<TrustLabel, { text: string; tone: 'green' | 'amber' | 'red' }> = {
  point_in_time: { text: 'Fully backtestable', tone: 'green' },
  reconstructable: { text: 'Backtestable with caveats', tone: 'amber' },
  live_only: { text: 'Forward-only, not backtestable', tone: 'red' },
}

const COMPLEXITY_TONES: Record<Complexity['label'], 'green' | 'amber' | 'red'> = {
  Low: 'green', Medium: 'amber', High: 'red',
}

interface FunnelPreviewProps {
  stages: FunnelStage[]
  trustLabel: TrustLabel | null
  complexity: Complexity
  loading: boolean
}

export function FunnelPreview({ stages, trustLabel, complexity, loading }: FunnelPreviewProps) {
  const counts = stages.length > 0 ? [stages[0].candidates_before, ...stages.map((s) => s.candidates_after)] : []
  const totalMissing = stages.reduce((sum, s) => sum + s.missing_data_count, 0)

  return (
    <Card>
      <div className="flex items-center gap-2 flex-wrap mb-3">
        {trustLabel && <Badge tone={TRUST_LABELS[trustLabel].tone}>{TRUST_LABELS[trustLabel].text}</Badge>}
        <Badge tone={COMPLEXITY_TONES[complexity.label]}>{complexity.label} complexity</Badge>
        {totalMissing > 0 && <Badge tone="amber">{totalMissing} missing-data hits</Badge>}
      </div>

      {loading ? (
        <p className="text-sm text-slate-400">Updating…</p>
      ) : counts.length > 0 ? (
        <div className="flex items-center gap-2 flex-wrap font-mono text-lg text-slate-800">
          {counts.map((c, i) => (
            <span key={i} className="flex items-center gap-2">
              {i > 0 && <span className="text-slate-300">→</span>}
              {c}
            </span>
          ))}
        </div>
      ) : (
        <p className="text-sm text-slate-400">Add a universe node to see how many candidates survive.</p>
      )}

      <p className="text-xs text-slate-400 mt-3">{COMPLEXITY_CAVEAT}</p>
    </Card>
  )
}

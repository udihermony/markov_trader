import type { NodeSpec } from '../../types'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'

interface NodeCardProps {
  node: NodeSpec
  description?: string
  candidatesBefore?: number
  candidatesAfter?: number
  missingDataCount?: number
  canMoveUp: boolean
  canMoveDown: boolean
  onMoveUp: () => void
  onMoveDown: () => void
  onRemove: () => void
}

export function NodeCard({
  node, description, candidatesBefore, candidatesAfter, missingDataCount,
  canMoveUp, canMoveDown, onMoveUp, onMoveDown, onRemove,
}: NodeCardProps) {
  const hasCounts = candidatesBefore !== undefined && candidatesAfter !== undefined

  return (
    <Card className="flex items-center justify-between gap-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <Badge tone="neutral">{node.type}</Badge>
          {hasCounts && (
            <span className="text-xs font-mono text-slate-400">
              {candidatesBefore} → {candidatesAfter}
            </span>
          )}
          {!!missingDataCount && (
            <Badge tone="amber">{missingDataCount} missing data</Badge>
          )}
        </div>
        <p className="text-sm text-slate-600 truncate">{description ?? '…'}</p>
      </div>
      <div className="flex items-center gap-1 shrink-0">
        <Button variant="ghost" disabled={!canMoveUp} onClick={onMoveUp} aria-label="Move up">
          ↑
        </Button>
        <Button variant="ghost" disabled={!canMoveDown} onClick={onMoveDown} aria-label="Move down">
          ↓
        </Button>
        <Button variant="danger" onClick={onRemove}>
          Remove
        </Button>
      </div>
    </Card>
  )
}

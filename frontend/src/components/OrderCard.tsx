import type { OrderRow } from '../types'
import { plainReason } from '../lib/reasons'
import { Badge } from './ui/Badge'
import { Button } from './ui/Button'
import { Card } from './ui/Card'

interface OrderCardProps {
  order: OrderRow
  walletName: string
  onDecide: (decision: 'approve' | 'skip') => void
  deciding: boolean
}

export function OrderCard({ order, walletName, onDecide, deciding }: OrderCardProps) {
  const decided = order.user_decision !== null

  return (
    <Card className="flex items-start justify-between gap-4">
      <div>
        <div className="flex items-center gap-2 mb-1">
          <Badge tone={order.action === 'BUY' ? 'green' : 'amber'}>{order.action}</Badge>
          <span className="font-semibold text-slate-800">{order.ticker}</span>
          <span className="text-xs text-slate-400">· {walletName}</span>
        </div>
        <p className="text-sm text-slate-600">{plainReason(order.reason)}</p>
        {order.cash_amount != null && (
          <p className="text-xs text-slate-400 mt-1">≈ ${order.cash_amount.toLocaleString()}</p>
        )}
      </div>
      <div className="flex gap-2 shrink-0">
        {decided ? (
          <Badge tone={order.user_decision === 'skip' ? 'red' : 'neutral'}>
            {order.user_decision === 'skip' ? 'Skipped' : 'Approved'}
          </Badge>
        ) : (
          <>
            <Button variant="secondary" disabled={deciding} onClick={() => onDecide('skip')}>
              Skip
            </Button>
            <Button disabled={deciding} onClick={() => onDecide('approve')}>
              Approve
            </Button>
          </>
        )}
      </div>
    </Card>
  )
}

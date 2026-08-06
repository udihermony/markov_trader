import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { api } from '../lib/api'
import type { EquitySnapshot, Fill, Position, Wallet } from '../types'
import { plainReason } from '../lib/reasons'
import { Badge } from '../components/ui/Badge'
import { Card } from '../components/ui/Card'
import { EquityChart } from '../components/EquityChart'

// The engine enforces a max-concurrent-positions cap (currently a fixed
// backend default, not yet a per-wallet API field — see
// backend/worker/wallet_runner.py DEFAULT_MAX_CONCURRENT_POSITIONS). Shown
// here only to render DESIGN.md's "physical slots" metaphor; not fetched.
const MAX_POSITIONS_ASSUMED = 8

export function WalletDetailPage() {
  const { id } = useParams<{ id: string }>()
  const walletId = Number(id)

  const { data: wallet } = useQuery({
    queryKey: ['wallet', walletId],
    queryFn: () => api.get<Wallet>(`/wallets/${walletId}`),
  })
  const { data: snapshots } = useQuery({
    queryKey: ['wallet', walletId, 'equity-snapshots'],
    queryFn: () => api.get<EquitySnapshot[]>(`/wallets/${walletId}/equity-snapshots`),
  })
  const { data: positions } = useQuery({
    queryKey: ['wallet', walletId, 'positions'],
    queryFn: () => api.get<Position[]>(`/wallets/${walletId}/positions`),
  })
  const { data: fills } = useQuery({
    queryKey: ['wallet', walletId, 'fills'],
    queryFn: () => api.get<Fill[]>(`/wallets/${walletId}/fills`),
  })

  if (!wallet) return <p className="text-sm text-slate-400">Loading…</p>

  const latest = snapshots?.[snapshots.length - 1]
  const idleCashPct = latest ? (latest.cash / latest.total_equity) * 100 : null
  const pnl = latest ? latest.total_equity - wallet.initial_cash : 0
  const totalSlippage =
    fills?.reduce((sum, f) => sum + (f.shares * f.fill_price * f.cost_bps_applied) / 10000, 0) ?? 0

  const emptySlots = Math.max(0, MAX_POSITIONS_ASSUMED - (positions?.length ?? 0))

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <h1 className="text-xl font-semibold text-slate-800">{wallet.name}</h1>
        {wallet.is_benchmark && <Badge tone="neutral">Benchmark</Badge>}
        {wallet.status === 'retired' && <Badge tone="amber">Retired</Badge>}
      </div>

      <Card>
        <h2 className="text-sm font-medium text-slate-500 mb-2">Equity vs SPY</h2>
        <EquityChart snapshots={snapshots ?? []} />
      </Card>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Card>
          <p className="text-xs text-slate-400">Total equity</p>
          <p className="text-lg font-medium">${(latest?.total_equity ?? wallet.cash).toLocaleString()}</p>
        </Card>
        <Card>
          <p className="text-xs text-slate-400">P&amp;L</p>
          <p className={`text-lg font-medium ${pnl >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
            {pnl >= 0 ? '+' : ''}
            ${pnl.toFixed(2)}
          </p>
        </Card>
        <Card>
          <p className="text-xs text-slate-400">Idle cash</p>
          <p className="text-lg font-medium">{idleCashPct !== null ? `${idleCashPct.toFixed(0)}%` : '—'}</p>
        </Card>
        <Card>
          <p className="text-xs text-slate-400">Friction paid</p>
          <p className="text-lg font-medium">${totalSlippage.toFixed(2)}</p>
        </Card>
      </div>

      <Card>
        <h2 className="text-sm font-medium text-slate-500 mb-3">Open positions</h2>
        <div className="grid grid-cols-4 sm:grid-cols-8 gap-2">
          {positions?.map((p) => (
            <div key={p.id} className="border border-slate-300 rounded-md p-2 text-center bg-slate-50">
              <p className="text-xs font-semibold text-slate-700">{p.ticker}</p>
              <p className="text-xs text-slate-400">{p.shares} sh</p>
            </div>
          ))}
          {Array.from({ length: emptySlots }).map((_, i) => (
            <div
              key={`empty-${i}`}
              className="border border-dashed border-slate-200 rounded-md p-2 text-center h-[46px]"
            />
          ))}
        </div>
      </Card>

      <Card>
        <h2 className="text-sm font-medium text-slate-500 mb-3">Trade history</h2>
        {fills && fills.length > 0 ? (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-slate-400 border-b border-slate-200">
                <th className="py-1 font-medium">Date</th>
                <th className="font-medium">Ticker</th>
                <th className="font-medium">Action</th>
                <th className="font-medium">Shares</th>
                <th className="font-medium">Fill</th>
                <th className="font-medium">Reason</th>
              </tr>
            </thead>
            <tbody>
              {fills.map((f) => (
                <tr key={f.id} className="border-b border-slate-100 last:border-0">
                  <td className="py-1.5">{f.timestamp.slice(0, 10)}</td>
                  <td>{f.ticker}</td>
                  <td>
                    <Badge tone={f.action === 'BUY' ? 'green' : 'amber'}>{f.action}</Badge>
                  </td>
                  <td>{f.shares}</td>
                  <td>${f.fill_price.toFixed(2)}</td>
                  <td className="text-slate-500">{plainReason(f.reason)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-slate-400">No trades yet.</p>
        )}
      </Card>
    </div>
  )
}

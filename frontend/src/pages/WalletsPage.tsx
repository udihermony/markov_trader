import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import type { Wallet } from '../types'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'

export function WalletsPage() {
  const { data: wallets, isLoading } = useQuery({
    queryKey: ['wallets'],
    queryFn: () => api.get<Wallet[]>('/wallets'),
  })

  const roi = (w: Wallet) => ((w.cash - w.initial_cash) / w.initial_cash) * 100

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-800">Wallets</h1>
          <p className="text-sm text-slate-500">Parallel forward paper-trading accounts.</p>
        </div>
        <Link to="/wallets/new">
          <Button>New wallet</Button>
        </Link>
      </div>

      {isLoading ? (
        <p className="text-sm text-slate-400">Loading…</p>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {wallets?.map((wallet) => (
            <Link key={wallet.id} to={`/wallets/${wallet.id}`}>
              <Card className="hover:border-slate-400 transition-colors">
                <div className="flex items-center gap-2 mb-2">
                  <span className="font-semibold text-slate-800">{wallet.name}</span>
                  {wallet.is_benchmark && <Badge tone="neutral">Benchmark</Badge>}
                  {wallet.status === 'retired' && <Badge tone="amber">Retired</Badge>}
                </div>
                <p className="text-lg font-medium text-slate-900">${wallet.cash.toLocaleString()}</p>
                <p className={`text-sm ${roi(wallet) >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                  {roi(wallet) >= 0 ? '+' : ''}
                  {roi(wallet).toFixed(2)}% since {wallet.start_date}
                </p>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { EquitySnapshot, OrderRow, Wallet } from '../types'
import { OrderCard } from '../components/OrderCard'
import { Card } from '../components/ui/Card'

function startOfMonthIso(): string {
  const now = new Date()
  return new Date(now.getFullYear(), now.getMonth(), 1).toISOString().slice(0, 10)
}

export function TodayPage() {
  const queryClient = useQueryClient()

  const { data: wallets } = useQuery({
    queryKey: ['wallets'],
    queryFn: () => api.get<Wallet[]>('/wallets'),
  })

  const { data: pendingOrders, isLoading: loadingOrders } = useQuery({
    queryKey: ['orders', 'pending'],
    queryFn: () => api.get<OrderRow[]>('/orders?status=pending'),
  })

  const { data: allOrders } = useQuery({
    queryKey: ['orders', 'all'],
    queryFn: () => api.get<OrderRow[]>('/orders'),
  })

  const { data: equitySummaries } = useQuery({
    queryKey: ['equity-summary', wallets?.map((w) => w.id)],
    queryFn: async () => {
      if (!wallets) return []
      return Promise.all(
        wallets.map(async (wallet) => {
          const snapshots = await api.get<EquitySnapshot[]>(`/wallets/${wallet.id}/equity-snapshots`)
          const today = snapshots[snapshots.length - 1]
          const yesterday = snapshots[snapshots.length - 2]
          return { wallet, today, yesterday }
        }),
      )
    },
    enabled: !!wallets && wallets.length > 0,
  })

  const decide = useMutation({
    mutationFn: ({ orderId, decision }: { orderId: number; decision: 'approve' | 'skip' }) =>
      api.post(`/orders/${orderId}/decision`, { decision }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['orders'] })
    },
  })

  const skipCountThisMonth =
    allOrders?.filter((o) => o.user_decision === 'skip' && o.created_date >= startOfMonthIso()).length ?? 0

  const walletName = (walletId: number) => wallets?.find((w) => w.id === walletId)?.name ?? `Wallet ${walletId}`

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-800">Today</h1>
        <p className="text-sm text-slate-500">What happens next, across every wallet.</p>
      </div>

      {equitySummaries && equitySummaries.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          {equitySummaries.map(({ wallet, today, yesterday }) => {
            const delta = today && yesterday ? today.total_equity - yesterday.total_equity : null
            return (
              <Card key={wallet.id} className="text-sm">
                <p className="font-medium text-slate-700">{wallet.name}</p>
                {today ? (
                  <>
                    <p className="text-slate-500">${today.total_equity.toLocaleString()}</p>
                    {delta !== null && (
                      <p className={delta >= 0 ? 'text-emerald-600' : 'text-red-600'}>
                        {delta >= 0 ? '+' : ''}
                        {delta.toFixed(2)} since yesterday
                      </p>
                    )}
                  </>
                ) : (
                  <p className="text-slate-400">No activity yet</p>
                )}
              </Card>
            )
          })}
        </div>
      )}

      <div className="space-y-3">
        {loadingOrders ? (
          <p className="text-slate-400 text-sm">Loading…</p>
        ) : pendingOrders && pendingOrders.length > 0 ? (
          pendingOrders.map((order) => (
            <OrderCard
              key={order.id}
              order={order}
              walletName={walletName(order.wallet_id)}
              deciding={decide.isPending}
              onDecide={(decision) => decide.mutate({ orderId: order.id, decision })}
            />
          ))
        ) : (
          <Card className="text-center py-8">
            <p className="text-slate-500">Nothing to do today.</p>
            <p className="text-sm text-slate-400 mt-1">Most days, that's the correct answer.</p>
          </Card>
        )}
      </div>

      {skipCountThisMonth > 0 && (
        <p className="text-xs text-slate-400">
          You've skipped {skipCountThisMonth} signal{skipCountThisMonth === 1 ? '' : 's'} this month.
        </p>
      )}
    </div>
  )
}

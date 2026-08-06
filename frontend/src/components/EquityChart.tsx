import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { EquitySnapshot } from '../types'

export function EquityChart({ snapshots }: { snapshots: EquitySnapshot[] }) {
  if (snapshots.length === 0) {
    return <p className="text-sm text-slate-400 py-8 text-center">No equity history yet.</p>
  }

  return (
    <div className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={snapshots} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis dataKey="date" tick={{ fontSize: 11, fill: '#64748b' }} />
          <YAxis
            tick={{ fontSize: 11, fill: '#64748b' }}
            width={64}
            tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}k`}
          />
          <Tooltip formatter={(v) => `$${Number(v).toLocaleString()}`} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line type="monotone" dataKey="total_equity" name="This wallet" stroke="#0f172a" strokeWidth={2} dot={false} />
          <Line
            type="monotone"
            dataKey="benchmark_equity"
            name="SPY (same rules)"
            stroke="#94a3b8"
            strokeWidth={2}
            strokeDasharray="4 3"
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

import type { ParamField } from '../../types'
import { Input } from '../ui/Input'

// The generic, schema-driven form: works for every node type today and any
// future one (M8's copilot, M10's AI nodes, M11's new sources) without a
// new form component, as long as its fields fit these five field types.

const EXPRESSION_FUNCTIONS = [
  { value: 'price', label: 'the price itself' },
  { value: 'sma', label: 'day average' },
  { value: 'ema', label: 'day exponential average' },
  { value: 'rsi', label: 'day RSI' },
  { value: 'pct_change', label: 'day price change' },
  { value: 'zscore', label: 'day z-score' },
]

function parseExpression(expr: string | undefined): { fn: string; window: number } {
  if (!expr || expr.trim() === 'px.close') return { fn: 'price', window: 10 }
  const match = /^(\w+)\(px\.close,\s*(\d+)\)$/.exec(expr.trim())
  if (match) return { fn: match[1], window: Number(match[2]) }
  return { fn: 'price', window: 10 }
}

function composeExpression(fn: string, window: number): string {
  return fn === 'price' ? 'px.close' : `${fn}(px.close, ${window})`
}

interface ParamFormProps {
  schema: ParamField[]
  value: Record<string, unknown>
  onChange: (params: Record<string, unknown>) => void
}

export function ParamForm({ schema, value, onChange }: ParamFormProps) {
  function setField(name: string, fieldValue: unknown) {
    onChange({ ...value, [name]: fieldValue })
  }

  if (schema.length === 0) {
    return <p className="text-sm text-slate-400">No settings needed.</p>
  }

  return (
    <div className="space-y-3">
      {schema.map((field) => (
        <div key={field.name}>
          <label className="text-xs font-medium text-slate-500 mb-1 block">{field.label}</label>
          {field.type === 'expression' && (
            <ExpressionField
              value={value[field.name] as string | undefined}
              onChange={(expr) => setField(field.name, expr)}
            />
          )}
          {field.type === 'enum' && (
            <select
              className="w-full px-3 py-1.5 rounded-md border border-slate-300 text-sm focus:outline-none focus:ring-2 focus:ring-slate-400"
              value={(value[field.name] as string | undefined) ?? field.options?.[0] ?? ''}
              onChange={(e) => setField(field.name, e.target.value)}
            >
              {field.options?.map((opt) => (
                <option key={opt} value={opt}>
                  {opt}
                </option>
              ))}
            </select>
          )}
          {field.type === 'number' && (
            <Input
              type="number"
              step="any"
              value={(value[field.name] as number | undefined) ?? (field.default as number) ?? 0}
              onChange={(e) => setField(field.name, Number(e.target.value))}
            />
          )}
          {field.type === 'string' && (
            <Input
              value={(value[field.name] as string | undefined) ?? ''}
              onChange={(e) => setField(field.name, e.target.value)}
            />
          )}
          {field.type === 'ticker_list' && (
            <Input
              placeholder="AAPL, MSFT, GOOGL"
              value={((value[field.name] as string[] | undefined) ?? []).join(', ')}
              onChange={(e) =>
                setField(
                  field.name,
                  e.target.value.split(',').map((t) => t.trim().toUpperCase()).filter(Boolean),
                )
              }
            />
          )}
        </div>
      ))}
    </div>
  )
}

function ExpressionField({ value, onChange }: { value: string | undefined; onChange: (expr: string) => void }) {
  const { fn, window } = parseExpression(value)

  return (
    <div className="flex gap-2 items-center">
      <select
        className="px-3 py-1.5 rounded-md border border-slate-300 text-sm focus:outline-none focus:ring-2 focus:ring-slate-400"
        value={fn}
        onChange={(e) => onChange(composeExpression(e.target.value, window))}
      >
        {EXPRESSION_FUNCTIONS.map((f) => (
          <option key={f.value} value={f.value}>
            {f.label}
          </option>
        ))}
      </select>
      {fn !== 'price' && (
        <>
          <Input
            type="number"
            min={1}
            className="w-20"
            value={window}
            onChange={(e) => onChange(composeExpression(fn, Number(e.target.value)))}
          />
          <span className="text-sm text-slate-400">days</span>
        </>
      )}
    </div>
  )
}

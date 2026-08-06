// Shared window-extraction logic for the "expression" ParamField type
// (M6's ParamForm.tsx) — extracted so the M7 neighbourhood scan can enumerate
// and vary the same numeric windows the builder's function/window picker
// already composes, without a second parser.

export const EXPRESSION_FUNCTIONS = [
  { value: 'price', label: 'the price itself' },
  { value: 'sma', label: 'day average' },
  { value: 'ema', label: 'day exponential average' },
  { value: 'rsi', label: 'day RSI' },
  { value: 'pct_change', label: 'day price change' },
  { value: 'zscore', label: 'day z-score' },
]

export function parseExpression(expr: string | undefined): { fn: string; window: number } {
  if (!expr || expr.trim() === 'px.close') return { fn: 'price', window: 10 }
  const match = /^(\w+)\(px\.close,\s*(\d+)\)$/.exec(expr.trim())
  if (match) return { fn: match[1], window: Number(match[2]) }
  return { fn: 'price', window: 10 }
}

export function composeExpression(fn: string, window: number): string {
  return fn === 'price' ? 'px.close' : `${fn}(px.close, ${window})`
}

import type { StrategySpec } from '../types'

export type ComplexityLabel = 'Low' | 'Medium' | 'High'

export interface Complexity {
  score: number
  label: ComplexityLabel
}

// DESIGN.md §3: "more nodes and more parameters means more overfitting
// risk." A simple, honest count — not a real holdout gate (Lab/M7 doesn't
// exist yet), just a nudge.
export function computeComplexity(spec: StrategySpec): Complexity {
  const score = spec.nodes.reduce((total, node) => total + 1 + Object.keys(node.params ?? {}).length, 0)
  const label: ComplexityLabel = score < 8 ? 'Low' : score < 15 ? 'Medium' : 'High'
  return { score, label }
}

export const COMPLEXITY_CAVEAT =
  'More nodes and parameters mean more ways to accidentally fit noise instead of a real pattern. ' +
  'A backtest alone can\'t tell the difference — that\'s what honest testing (coming soon) is for.'

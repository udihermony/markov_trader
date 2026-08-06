// Mirrors backend/engine/backtest_runner.py's luck_baseline() exactly — kept
// client-side (not a new API endpoint) because luck-test results are
// session-only (design decision #4 in the M7 plan): no fabricated baseline
// is ever shown before the user has actually run a luck test this session.
export function luckBaseline(nullSamples: number[], k: number): number | null {
  if (nullSamples.length === 0 || k <= 0) return null
  const sorted = [...nullSamples].sort((a, b) => a - b)
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.ceil((1 - 1 / k) * sorted.length) - 1))
  return sorted[idx]
}

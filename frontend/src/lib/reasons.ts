// Orders persist only the node's machine `reason` key (e.g. "cross_up"),
// not its plain-language `explanation` — the engine computes both
// (backend/engine/graph/types.py NodeResult) but only the key is written to
// the orders table. Translating at the display layer here is the M5-scoped
// way to satisfy "every reason is shown in plain language somewhere"
// without reopening the orchestrator (untouched since M3).
const REASON_LABELS: Record<string, string> = {
  cross_up: 'The fast trend crossed above the slow trend, first time in a while.',
  cross_down: 'The fast trend crossed below the slow trend.',
  time_stop_exit: 'This position reached its maximum holding period.',
  holding: 'Still within the holding period — no exit yet.',
  always: 'This wallet always holds its target position.',
  never: 'This wallet never sells on its own.',
  threshold_met: 'A price condition was met.',
  threshold_not_met: 'The price condition was not met.',
  insufficient_history: 'Not enough price history yet to decide.',
  no_position: 'No open position to act on.',
  no_signal: 'No trading signal today.',
}

export function plainReason(reason: string): string {
  return REASON_LABELS[reason] ?? reason.replace(/_/g, ' ')
}

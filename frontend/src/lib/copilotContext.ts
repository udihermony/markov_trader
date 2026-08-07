import type { CopilotContext } from '../types'

// DESIGN.md §5.5: "the panel knows which wallet, strategy, or run is on
// screen, so 'why did this lose money in March' needs no setup." A small,
// route-aware mapping rather than lifting page state into a context
// provider — the backend resolves the id into a name via the same
// get_strategy/get_wallet tools it already has (backend/ai/copilot.py).
export function copilotContextForPath(pathname: string): CopilotContext | undefined {
  let match = /^\/strategies\/(\d+)\/edit/.exec(pathname)
  if (match) return { surface: 'strategy', entity_id: Number(match[1]) }

  match = /^\/wallets\/(\d+)/.exec(pathname)
  if (match) return { surface: 'wallet', entity_id: Number(match[1]) }

  if (pathname.startsWith('/lab')) return { surface: 'lab' }
  if (pathname.startsWith('/strategies')) return { surface: 'strategies' }
  if (pathname.startsWith('/wallets')) return { surface: 'wallets' }
  if (pathname === '/') return { surface: 'today' }

  return undefined
}

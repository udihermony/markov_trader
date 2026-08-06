import type { StrategySpec } from '../types'

export interface Preset {
  name: string
  behavior: string
  failureMode: string
  spec: StrategySpec
}

// All three presets use `manual_list` for their starting universe rather
// than `finviz_screen` — the live builder preview re-evaluates on every
// edit, and finviz_screen makes a real network call in paper mode
// (backend/sources/finviz_screen.py's documented scraper-fragility risk).
// A small fixed ticker list keeps the builder fast and deterministic;
// finviz_screen is still available as a node type users can add by hand.

const TREND_FOLLOWER_TICKERS = ['AAPL', 'MSFT', 'GOOGL']

export const PRESETS: Preset[] = [
  {
    name: 'Trend Follower',
    behavior:
      'Buys when short-term momentum turns up (the 10-day average crosses above the 20-day), sells when it ' +
      'turns back down or after 5 days, whichever comes first.',
    failureMode:
      'Works in trending markets. Whipsaws — frequent small losses — in choppy, sideways ones, since every ' +
      'minor wiggle can trigger a buy and a quick sell.',
    spec: {
      spec_version: 2,
      name: 'Trend Follower',
      sources: [{ id: 'px', type: 'price_bars' }],
      nodes: [
        { id: 'u1', kind: 'universe', type: 'manual_list', params: { tickers: TREND_FOLLOWER_TICKERS } },
        {
          id: 't1', kind: 'trigger', type: 'cross',
          params: { a: 'sma(px.close, 10)', b: 'sma(px.close, 20)', direction: 'up' },
        },
        {
          id: 'x1', kind: 'exit', type: 'cross',
          params: { a: 'sma(px.close, 10)', b: 'sma(px.close, 20)', direction: 'down' },
        },
        { id: 'x2', kind: 'exit', type: 'time_stop', params: { max_hold_days: 5, calendar_feature: 'px.close' } },
        { id: 's1', kind: 'size', type: 'fixed_fraction', params: { fraction: 0.1 } },
      ],
      edges: [['u1', 't1']],
    },
  },
  {
    name: 'Buy and Hold',
    behavior: 'Buys once, right away, and holds — no selling, ever, unless you retire the wallet.',
    failureMode:
      'No timing risk (you can\'t buy at a worse moment than "immediately"), but also no downside protection ' +
      '— every drop is ridden out in full.',
    spec: {
      spec_version: 2,
      name: 'Buy and Hold',
      sources: [{ id: 'px', type: 'price_bars' }],
      nodes: [
        { id: 'u1', kind: 'universe', type: 'manual_list', params: { tickers: ['SPY'] } },
        { id: 't1', kind: 'trigger', type: 'always', params: {} },
        { id: 'x1', kind: 'exit', type: 'never', params: {} },
        { id: 's1', kind: 'size', type: 'fixed_fraction', params: { fraction: 1.0 } },
      ],
      edges: [['u1', 't1']],
    },
  },
  {
    name: 'Confirmed Trend Follower',
    behavior:
      'Only buys on the same moving-average cross as Trend Follower when 14-day momentum (RSI) also agrees ' +
      '— a second, independent signal has to say yes.',
    failureMode:
      'Fewer, more selective trades than Trend Follower. Can sit out real trends entirely if momentum lags ' +
      'behind the price move.',
    spec: {
      spec_version: 2,
      name: 'Confirmed Trend Follower',
      sources: [{ id: 'px', type: 'price_bars' }],
      nodes: [
        { id: 'u1', kind: 'universe', type: 'manual_list', params: { tickers: TREND_FOLLOWER_TICKERS } },
        {
          id: 't1', kind: 'trigger', type: 'cross',
          params: { a: 'sma(px.close, 10)', b: 'sma(px.close, 20)', direction: 'up' },
        },
        {
          id: 'c1', kind: 'confirm', type: 'threshold',
          params: { feature: 'rsi(px.close, 14)', op: '>', value: 50 },
        },
        {
          id: 'x1', kind: 'exit', type: 'cross',
          params: { a: 'sma(px.close, 10)', b: 'sma(px.close, 20)', direction: 'down' },
        },
        { id: 'x2', kind: 'exit', type: 'time_stop', params: { max_hold_days: 5, calendar_feature: 'px.close' } },
        { id: 's1', kind: 'size', type: 'fixed_fraction', params: { fraction: 0.1 } },
      ],
      edges: [['u1', 't1'], ['t1', 'c1']],
    },
  },
]

import type { NodeKind, NodeSpec } from '../types'

const ENTRY_KIND_ORDER: NodeKind[] = ['universe', 'trigger', 'confirm', 'veto', 'size']

// "The funnel editor generates the linear chain" (DESIGN.md §4.6) — the
// builder never lets a user draw edges directly. This is purely a frontend
// concern: chain every entry-kind node (exit nodes are unwired) in fixed
// kind order, preserving each kind's own node order as given.
export function buildEdges(nodes: NodeSpec[]): [string, string][] {
  const byKind = new Map<NodeKind, NodeSpec[]>(ENTRY_KIND_ORDER.map((k) => [k, []]))
  for (const node of nodes) {
    if (node.kind === 'exit') continue
    byKind.get(node.kind)?.push(node)
  }

  const chain = ENTRY_KIND_ORDER.flatMap((kind) => byKind.get(kind) ?? [])
  const edges: [string, string][] = []
  for (let i = 0; i < chain.length - 1; i++) {
    edges.push([chain[i].id, chain[i + 1].id])
  }
  return edges
}

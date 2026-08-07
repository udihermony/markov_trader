import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import { buildEdges } from '../lib/buildEdges'
import { computeComplexity } from '../lib/complexity'
import type { FunnelStage, NodeKind, NodeSpec, PreviewResponse, SourceRef, Strategy, StrategySpec } from '../types'
import { AddNodeModal } from '../components/builder/AddNodeModal'
import { FunnelPreview } from '../components/builder/FunnelPreview'
import { NodeCard } from '../components/builder/NodeCard'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { Input } from '../components/ui/Input'

const KIND_SECTIONS: { kind: NodeKind; question: string }[] = [
  { kind: 'universe', question: 'What do I watch?' },
  { kind: 'trigger', question: 'When do I buy?' },
  { kind: 'confirm', question: 'What must also be true?' },
  { kind: 'veto', question: 'When do I never buy?' },
  { kind: 'exit', question: 'When do I sell?' },
  { kind: 'size', question: 'How much?' },
]

// M6's builder only ever writes this one source — price_bars is still the
// only source with real data (see the plan's expression-field scope note).
const FIXED_SOURCES: SourceRef[] = [{ id: 'px', type: 'price_bars' }]

export function StrategyBuilderPage() {
  const { id } = useParams<{ id?: string }>()
  const isEditing = id !== undefined
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const initialPresetSpec = (location.state as { spec?: StrategySpec } | null)?.spec

  const { data: existingStrategy } = useQuery({
    queryKey: ['strategy', id],
    queryFn: () => api.get<Strategy>(`/strategies/${id}`),
    enabled: isEditing,
  })

  const [name, setName] = useState(initialPresetSpec?.name ?? 'New Strategy')
  const [nodes, setNodes] = useState<NodeSpec[]>(initialPresetSpec?.nodes ?? [])

  useEffect(() => {
    // Skip the fetched-strategy overwrite when a proposal seeded this page
    // (e.g. the copilot's "Review in builder" link) — otherwise the
    // proposed-but-unsaved spec is clobbered by the currently-saved one
    // the instant the fetch resolves.
    if (existingStrategy && !initialPresetSpec) {
      setName(existingStrategy.name)
      setNodes(existingStrategy.spec.nodes)
    }
  }, [existingStrategy, initialPresetSpec])

  const spec: StrategySpec = useMemo(
    () => ({ spec_version: 2, name, sources: FIXED_SOURCES, nodes, edges: buildEdges(nodes) }),
    [name, nodes],
  )
  const complexity = useMemo(() => computeComplexity(spec), [spec])

  // Debounced live preview — re-runs ~500ms after the last edit rather than
  // on every keystroke.
  const [debouncedSpec, setDebouncedSpec] = useState(spec)
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSpec(spec), 500)
    return () => clearTimeout(t)
  }, [spec])

  const { data: preview, isFetching: previewLoading } = useQuery({
    queryKey: ['strategy-preview', debouncedSpec],
    queryFn: () => api.post<PreviewResponse>('/strategies/preview', { spec: debouncedSpec }),
    enabled: nodes.length > 0,
    retry: false,
  })

  const stagesByNodeId = useMemo(() => {
    const map = new Map<string, FunnelStage>()
    preview?.stages.forEach((s) => map.set(s.node_id, s))
    return map
  }, [preview])

  const [addingKind, setAddingKind] = useState<NodeKind | null>(null)

  function addNode(node: NodeSpec) {
    setNodes((prev) => [...prev, node])
  }
  function removeNode(nodeId: string) {
    setNodes((prev) => prev.filter((n) => n.id !== nodeId))
  }
  function moveNode(nodeId: string, direction: -1 | 1) {
    setNodes((prev) => {
      const kind = prev.find((n) => n.id === nodeId)?.kind
      const sameKindIndices = prev.map((n, i) => (n.kind === kind ? i : -1)).filter((i) => i >= 0)
      const idx = prev.findIndex((n) => n.id === nodeId)
      const posInKind = sameKindIndices.indexOf(idx)
      const swapPos = posInKind + direction
      if (swapPos < 0 || swapPos >= sameKindIndices.length) return prev
      const swapIdx = sameKindIndices[swapPos]
      const next = [...prev]
      ;[next[idx], next[swapIdx]] = [next[swapIdx], next[idx]]
      return next
    })
  }

  const saveMutation = useMutation({
    mutationFn: () =>
      isEditing
        ? api.put<Strategy>(`/strategies/${id}`, { name, spec })
        : api.post<Strategy>('/strategies', { name, spec }),
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: ['strategies'] })
      navigate(`/strategies/${saved.id}/edit`)
    },
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <Input value={name} onChange={(e) => setName(e.target.value)} className="text-lg font-semibold max-w-sm" />
        <Button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending || nodes.length === 0}>
          {saveMutation.isPending ? 'Saving…' : 'Save'}
        </Button>
      </div>
      {saveMutation.isError && <p className="text-sm text-red-600">Could not save — check the sections below.</p>}

      <FunnelPreview
        stages={preview?.stages ?? []}
        trustLabel={preview?.trust_label ?? null}
        complexity={complexity}
        loading={previewLoading}
      />

      {KIND_SECTIONS.map(({ kind, question }) => {
        const kindNodes = nodes.filter((n) => n.kind === kind)
        return (
          <Card key={kind}>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-medium text-slate-600">{question}</h2>
              <Button variant="secondary" onClick={() => setAddingKind(kind)}>
                + Add
              </Button>
            </div>
            <div className="space-y-2">
              {kindNodes.map((node, i) => {
                const stage = stagesByNodeId.get(node.id)
                return (
                  <NodeCard
                    key={node.id}
                    node={node}
                    description={stage?.description ?? preview?.descriptions[node.id]}
                    candidatesBefore={stage?.candidates_before}
                    candidatesAfter={stage?.candidates_after}
                    missingDataCount={stage?.missing_data_count}
                    canMoveUp={i > 0}
                    canMoveDown={i < kindNodes.length - 1}
                    onMoveUp={() => moveNode(node.id, -1)}
                    onMoveDown={() => moveNode(node.id, 1)}
                    onRemove={() => removeNode(node.id)}
                  />
                )
              })}
              {kindNodes.length === 0 && <p className="text-sm text-slate-400">No nodes yet.</p>}
            </div>
          </Card>
        )
      })}

      {addingKind && <AddNodeModal kind={addingKind} onAdd={addNode} onClose={() => setAddingKind(null)} />}
    </div>
  )
}

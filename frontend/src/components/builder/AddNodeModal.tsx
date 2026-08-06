import { useState } from 'react'
import type { NodeKind, NodeSpec, NodeTypeInfo, ParamField } from '../../types'
import { useNodeTypes } from '../../lib/nodeTypes'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { ParamForm } from './ParamForm'

function defaultParamsFor(schema: ParamField[]): Record<string, unknown> {
  const params: Record<string, unknown> = {}
  for (const field of schema) {
    if (field.default !== null && field.default !== undefined) {
      params[field.name] = field.default
      continue
    }
    switch (field.type) {
      case 'expression':
        params[field.name] = 'px.close'
        break
      case 'enum':
        params[field.name] = field.options?.[0] ?? ''
        break
      case 'number':
        params[field.name] = 0
        break
      case 'ticker_list':
        params[field.name] = []
        break
      default:
        params[field.name] = ''
    }
  }
  return params
}

let nodeIdCounter = 0
function generateNodeId(type: string): string {
  nodeIdCounter += 1
  return `${type}-${nodeIdCounter}-${Date.now()}`
}

interface AddNodeModalProps {
  kind: NodeKind
  onAdd: (node: NodeSpec) => void
  onClose: () => void
}

export function AddNodeModal({ kind, onAdd, onClose }: AddNodeModalProps) {
  const { data: nodeTypes } = useNodeTypes()
  const [selected, setSelected] = useState<NodeTypeInfo | null>(null)
  const [params, setParams] = useState<Record<string, unknown>>({})

  const available = (nodeTypes ?? []).filter((t) => t.allowed_kinds.includes(kind))

  function selectType(info: NodeTypeInfo) {
    setSelected(info)
    setParams(defaultParamsFor(info.params_schema))
  }

  function confirm() {
    if (!selected) return
    // `time_stop`'s calendar source isn't a user-facing field (see
    // backend/engine/graph/nodes.py) — the frontend fills it in silently.
    const finalParams = selected.type === 'time_stop' ? { ...params, calendar_feature: 'px.close' } : params
    onAdd({ id: generateNodeId(selected.type), kind, type: selected.type, params: finalParams })
    onClose()
  }

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-md max-h-[80vh] overflow-y-auto">
        {!selected ? (
          <>
            <h2 className="text-sm font-semibold text-slate-700 mb-3">Add a {kind} node</h2>
            <div className="space-y-2">
              {available.map((info) => (
                <button
                  key={info.type}
                  className="w-full text-left px-3 py-2 rounded-md border border-slate-200 hover:border-slate-400 hover:bg-slate-50 text-sm"
                  onClick={() => selectType(info)}
                >
                  {info.type}
                </button>
              ))}
              {available.length === 0 && (
                <p className="text-sm text-slate-400">No node types available for this section.</p>
              )}
            </div>
            <div className="mt-4 flex justify-end">
              <Button variant="secondary" onClick={onClose}>
                Cancel
              </Button>
            </div>
          </>
        ) : (
          <>
            <h2 className="text-sm font-semibold text-slate-700 mb-3">{selected.type}</h2>
            <ParamForm schema={selected.params_schema} value={params} onChange={setParams} />
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setSelected(null)}>
                Back
              </Button>
              <Button onClick={confirm}>Add</Button>
            </div>
          </>
        )}
      </Card>
    </div>
  )
}

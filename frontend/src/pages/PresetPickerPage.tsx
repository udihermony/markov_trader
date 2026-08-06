import { useNavigate } from 'react-router-dom'
import { PRESETS } from '../lib/presets'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'

export function PresetPickerPage() {
  const navigate = useNavigate()

  function startFrom(spec: (typeof PRESETS)[number]['spec']) {
    navigate('/strategies/new/build', { state: { spec } })
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-800">New strategy</h1>
        <p className="text-sm text-slate-500">
          Start from a preset and customize it, or build from scratch. Every preset says honestly how it behaves
          and when it fails.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {PRESETS.map((preset) => (
          <Card key={preset.name}>
            <h2 className="font-semibold text-slate-800 mb-2">{preset.name}</h2>
            <p className="text-sm text-slate-600 mb-2">{preset.behavior}</p>
            <p className="text-xs text-slate-400 mb-3">
              <span className="font-medium">When it fails:</span> {preset.failureMode}
            </p>
            <Button onClick={() => startFrom(preset.spec)}>Start from this</Button>
          </Card>
        ))}
        <Card className="flex flex-col items-start justify-between">
          <div>
            <h2 className="font-semibold text-slate-800 mb-2">Start from scratch</h2>
            <p className="text-sm text-slate-600 mb-3">Build a strategy node by node, with no starting point.</p>
          </div>
          <Button variant="secondary" onClick={() => navigate('/strategies/new/build')}>
            Start blank
          </Button>
        </Card>
      </div>
    </div>
  )
}

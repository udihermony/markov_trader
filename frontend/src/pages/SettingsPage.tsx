import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { ApiKeyInfo } from '../types'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { Input } from '../components/ui/Input'

export function SettingsPage() {
  const queryClient = useQueryClient()
  const { data: keys } = useQuery({
    queryKey: ['api-keys'],
    queryFn: () => api.get<ApiKeyInfo[]>('/api-keys'),
  })
  const [key, setKey] = useState('')

  const anthropicKey = keys?.find((k) => k.provider === 'anthropic')

  const saveMutation = useMutation({
    mutationFn: () => api.post<ApiKeyInfo>('/api-keys', { provider: 'anthropic', key }),
    onSuccess: () => {
      setKey('')
      queryClient.invalidateQueries({ queryKey: ['api-keys'] })
    },
  })

  const removeMutation = useMutation({
    mutationFn: () => api.del<void>('/api-keys/anthropic'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['api-keys'] }),
  })

  return (
    <div className="max-w-md space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-800">Settings</h1>
        <p className="text-sm text-slate-500">Your key stays on your account and is encrypted at rest.</p>
      </div>

      <Card>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-slate-700">Anthropic API key</h2>
          {anthropicKey && <Badge tone="green">Connected</Badge>}
        </div>
        <p className="text-xs text-slate-400 mb-3">
          Needed for the copilot panel. Get a key at{' '}
          <span className="text-slate-500">console.anthropic.com</span>.
        </p>

        {anthropicKey ? (
          <div className="flex items-center justify-between">
            <p className="text-sm text-slate-600">
              Connected since {new Date(anthropicKey.created_at).toLocaleDateString()}
            </p>
            <Button variant="danger" onClick={() => removeMutation.mutate()} disabled={removeMutation.isPending}>
              Remove
            </Button>
          </div>
        ) : (
          <div className="space-y-2">
            <Input
              type="password"
              placeholder="sk-ant-..."
              value={key}
              onChange={(e) => setKey(e.target.value)}
            />
            <Button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending || !key.trim()}>
              {saveMutation.isPending ? 'Saving…' : 'Save'}
            </Button>
            {saveMutation.isError && <p className="text-sm text-red-600">Could not save the key.</p>}
          </div>
        )}
      </Card>
    </div>
  )
}

import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useLocation } from 'react-router-dom'
import { api, ApiError } from '../../lib/api'
import { copilotContextForPath } from '../../lib/copilotContext'
import type { ApiKeyInfo, ChatMessage, Conversation } from '../../types'
import { Button } from '../ui/Button'
import { Input } from '../ui/Input'
import { StrategyProposalCard } from './StrategyProposalCard'

export function CopilotPanel({ open }: { open: boolean }) {
  const queryClient = useQueryClient()
  const location = useLocation()
  const [conversationId, setConversationId] = useState<number | null>(null)
  const [input, setInput] = useState('')
  const [sendError, setSendError] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  const { data: apiKeys } = useQuery({
    queryKey: ['api-keys'],
    queryFn: () => api.get<ApiKeyInfo[]>('/api-keys'),
  })
  const hasKey = apiKeys?.some((k) => k.provider === 'anthropic') ?? false

  useEffect(() => {
    if (!open || conversationId !== null) return
    api.post<Conversation>('/chat/conversations').then((c) => setConversationId(c.id))
  }, [open, conversationId])

  const { data: messages } = useQuery({
    queryKey: ['chat-messages', conversationId],
    queryFn: () => api.get<ChatMessage[]>(`/chat/conversations/${conversationId}/messages`),
    enabled: conversationId !== null,
  })

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages])

  const sendMutation = useMutation({
    mutationFn: () =>
      api.post<ChatMessage>(`/chat/conversations/${conversationId}/messages`, {
        content: input, context: copilotContextForPath(location.pathname),
      }),
    onSuccess: () => {
      setInput('')
      setSendError(null)
      queryClient.invalidateQueries({ queryKey: ['chat-messages', conversationId] })
    },
    onError: (err) => setSendError(err instanceof ApiError ? err.message : 'Something went wrong.'),
  })

  function send() {
    if (!input.trim() || conversationId === null) return
    sendMutation.mutate()
  }

  return (
    <div
      className={`shrink-0 border-l border-slate-200 bg-white flex flex-col transition-all overflow-hidden ${
        open ? 'w-96' : 'w-0'
      }`}
    >
      <div className="w-96 flex flex-col h-full">
        <div className="px-4 py-3 border-b border-slate-200">
          <p className="text-sm font-semibold text-slate-700">Copilot</p>
          <p className="text-xs text-slate-400">Renders strategies, not prose — every proposal is reviewable.</p>
        </div>

        {!hasKey ? (
          <div className="flex-1 flex items-center justify-center p-4 text-center">
            <p className="text-sm text-slate-500">
              Add your Anthropic API key in{' '}
              <Link to="/settings" className="underline font-medium text-slate-700">
                Settings
              </Link>{' '}
              to use the copilot.
            </p>
          </div>
        ) : (
          <>
            <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
              {messages?.map((m) => (
                <div key={m.id} className={m.role === 'user' ? 'flex justify-end' : 'flex justify-start'}>
                  <div
                    className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                      m.role === 'user' ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-800'
                    }`}
                  >
                    <p className="whitespace-pre-wrap">{m.content}</p>
                    {m.proposal_json && (
                      <div className="mt-2">
                        <StrategyProposalCard proposal={m.proposal_json} />
                      </div>
                    )}
                  </div>
                </div>
              ))}
              {messages?.length === 0 && (
                <p className="text-xs text-slate-400 text-center pt-8">
                  Describe a strategy in plain English to get started.
                </p>
              )}
            </div>
            <div className="p-3 border-t border-slate-200 space-y-1">
              {sendError && <p className="text-xs text-red-600">{sendError}</p>}
              <div className="flex gap-2">
                <Input
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && send()}
                  placeholder="Ask the copilot…"
                  disabled={sendMutation.isPending}
                />
                <Button onClick={send} disabled={sendMutation.isPending || !input.trim()}>
                  {sendMutation.isPending ? '…' : 'Send'}
                </Button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

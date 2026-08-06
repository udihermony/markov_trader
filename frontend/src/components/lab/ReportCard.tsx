import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import type { QuestionAnswer, ReportCard as ReportCardData } from '../../types'
import { Card } from '../ui/Card'

function QuestionBlock({ question, qa }: { question: string; qa: QuestionAnswer | null }) {
  if (!qa) return null
  return (
    <div>
      <p className="text-xs font-medium text-slate-500">{question}</p>
      <p className="text-sm font-semibold text-slate-800">{qa.answer}</p>
      <p className="text-xs text-slate-500">{qa.detail}</p>
    </div>
  )
}

export function ReportCard({ strategyId }: { strategyId: number }) {
  const { data: card } = useQuery({
    queryKey: ['report-card', strategyId],
    queryFn: () => api.get<ReportCardData>(`/strategies/${strategyId}/report-card`),
  })

  if (!card) return null

  return (
    <Card>
      <h2 className="text-sm font-semibold text-slate-700 mb-3">Report card</h2>
      {!card.has_evidence ? (
        <p className="text-sm text-slate-400">No evidence yet — run an experiment to see how this strategy grades.</p>
      ) : (
        <div className="space-y-3">
          {card.evidence_source === 'lab' && (
            <p className="text-xs text-amber-700 bg-amber-50 rounded px-2 py-1">
              Graded from Lab results only — contaminated by search, not yet a holdout or wallet result.
            </p>
          )}
          <QuestionBlock question="Did it beat doing nothing?" qa={card.beat_doing_nothing} />
          <QuestionBlock question="Is it real or luck?" qa={card.real_or_luck} />
          <QuestionBlock question="How often was it right?" qa={card.how_often_right} />
          <QuestionBlock question="Could you have stomached it?" qa={card.could_stomach_it} />
        </div>
      )}
    </Card>
  )
}

import { useState, useEffect } from 'react'
import { Scale, User, FolderOpen } from 'lucide-react'
import type { ContentRoute } from '../types'

interface Decision {
  id: number
  title: string
  context: string | null
  decision: string | null
  rationale: string | null
  outcome: string | null
  outcome_date: string | null
  status: string | null
  confidence_level: number | null
  project_id: number | null
  contact_id: number | null
  decided_at: string | null
  project_name: string | null
  contact_name: string | null
}

const statusColors: Record<string, string> = {
  open: 'bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-400',
  decided: 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400',
  revisit: 'bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400',
  validated: 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400',
  regretted: 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400',
}

function timeAgo(dateStr: string): string {
  const d = new Date(dateStr)
  const now = new Date()
  const days = Math.floor((now.getTime() - d.getTime()) / 86400000)
  if (days === 0) return 'today'
  if (days === 1) return 'yesterday'
  if (days < 7) return `${days}d ago`
  if (days < 30) return `${Math.floor(days / 7)}w ago`
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export default function DecisionsView({ onNavigate }: { onNavigate: (r: ContentRoute) => void }) {
  const [decisions, setDecisions] = useState<Decision[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/decisions')
      .then(res => res.json())
      .then(data => { setDecisions(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  if (loading) return <main className="flex-1 flex items-center justify-center min-h-screen"><p className="text-sm text-zinc-500 dark:text-zinc-400">Loading...</p></main>

  return (
    <main className="flex-1 min-h-screen p-6 lg:p-10">
      <div className="max-w-3xl mx-auto">
        <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-2 mb-6">
          <Scale className="w-5 h-5" /> Decisions
          <span className="text-sm font-normal text-zinc-500 dark:text-zinc-400 ml-2">{decisions.length} logged</span>
        </h1>

        {decisions.length === 0 ? (
          <div className="text-center py-16">
            <Scale className="w-10 h-10 text-zinc-300 dark:text-zinc-600 mx-auto mb-3" />
            <p className="text-sm text-zinc-500 dark:text-zinc-400">No decisions logged yet.</p>
          </div>
        ) : (
          <div className="space-y-3">
            {decisions.map(d => (
              <div key={d.id} className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl px-5 py-4">
                <div className="flex items-start justify-between gap-3">
                  <h3 className="text-sm font-medium text-zinc-900 dark:text-zinc-100">{d.title}</h3>
                  <div className="flex items-center gap-2 shrink-0">
                    {d.confidence_level != null && (
                      <span className="text-xs text-zinc-500 dark:text-zinc-400">{d.confidence_level}/10</span>
                    )}
                    {d.status && (
                      <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${statusColors[d.status] || statusColors.open}`}>
                        {d.status}
                      </span>
                    )}
                  </div>
                </div>
                {d.decision && (
                  <p className="text-sm text-zinc-700 dark:text-zinc-300 mt-2">{d.decision}</p>
                )}
                {d.rationale && (
                  <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-2 line-clamp-2">{d.rationale}</p>
                )}
                {d.outcome && (
                  <div className="mt-2 px-3 py-2 bg-zinc-50 dark:bg-zinc-800 rounded-lg">
                    <p className="text-xs font-medium text-zinc-600 dark:text-zinc-300">Outcome</p>
                    <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">{d.outcome}</p>
                  </div>
                )}
                <div className="flex items-center gap-3 mt-3">
                  {d.decided_at && (
                    <span className="text-xs text-zinc-500 dark:text-zinc-400">{timeAgo(d.decided_at)}</span>
                  )}
                  {d.contact_name && (
                    <button onClick={() => d.contact_id && onNavigate({ type: 'contact', id: d.contact_id })} className="text-xs text-blue-600 dark:text-blue-400 hover:underline flex items-center gap-1">
                      <User className="w-3 h-3" /> {d.contact_name}
                    </button>
                  )}
                  {d.project_name && (
                    <button onClick={() => d.project_id && onNavigate({ type: 'project', id: d.project_id })} className="text-xs text-blue-600 dark:text-blue-400 hover:underline flex items-center gap-1">
                      <FolderOpen className="w-3 h-3" /> {d.project_name}
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </main>
  )
}

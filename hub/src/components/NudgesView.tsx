import { useState, useEffect } from 'react'
import { Bell, AlertTriangle, Clock, Eye, User, FolderOpen } from 'lucide-react'
import type { ContentRoute } from '../types'

interface Nudge {
  nudge_type: string
  entity_id: number | null
  tier: string
  entity_name: string | null
  contact_id: number | null
  project_id: number | null
  description: string | null
  relevant_date: string | null
  days_value: number | null
  extra_context: string | null
  icon: string | null
}

const tierConfig: Record<string, { label: string; icon: React.ReactNode; border: string; bg: string }> = {
  urgent: {
    label: 'Needs Attention',
    icon: <AlertTriangle className="w-4 h-4 text-red-500 dark:text-red-400" />,
    border: 'border-red-200 dark:border-red-800',
    bg: 'bg-red-50 dark:bg-red-900/20',
  },
  soon: {
    label: 'Coming Up',
    icon: <Clock className="w-4 h-4 text-amber-500 dark:text-amber-400" />,
    border: 'border-amber-200 dark:border-amber-800',
    bg: 'bg-amber-50 dark:bg-amber-900/20',
  },
  awareness: {
    label: 'Awareness',
    icon: <Eye className="w-4 h-4 text-blue-500 dark:text-blue-400" />,
    border: 'border-blue-200 dark:border-blue-800',
    bg: 'bg-blue-50 dark:bg-blue-900/20',
  },
}

export default function NudgesView({ onNavigate }: { onNavigate: (r: ContentRoute) => void }) {
  const [nudges, setNudges] = useState<Nudge[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/nudges')
      .then(res => res.json())
      .then(data => { setNudges(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  if (loading) return <main className="flex-1 flex items-center justify-center min-h-screen"><p className="text-sm text-zinc-500 dark:text-zinc-400">Loading...</p></main>

  const tiers = ['urgent', 'soon', 'awareness']
  const grouped = tiers.map(t => ({ tier: t, items: nudges.filter(n => n.tier === t) })).filter(g => g.items.length > 0)

  if (grouped.length === 0) {
    return (
      <main className="flex-1 min-h-screen p-6 lg:p-10">
        <div className="max-w-3xl mx-auto">
          <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-2 mb-6">
            <Bell className="w-5 h-5" /> Nudges
          </h1>
          <div className="text-center py-16">
            <Bell className="w-10 h-10 text-zinc-300 dark:text-zinc-600 mx-auto mb-3" />
            <p className="text-sm text-zinc-500 dark:text-zinc-400">Nothing needs attention right now.</p>
          </div>
        </div>
      </main>
    )
  }

  return (
    <main className="flex-1 min-h-screen p-6 lg:p-10">
      <div className="max-w-3xl mx-auto">
        <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-2 mb-6">
          <Bell className="w-5 h-5" /> Nudges
          <span className="text-sm font-normal text-zinc-500 dark:text-zinc-400 ml-2">{nudges.length} items</span>
        </h1>

        <div className="space-y-6">
          {grouped.map(({ tier, items }) => {
            const cfg = tierConfig[tier] || tierConfig.awareness
            return (
              <div key={tier} className={`border ${cfg.border} rounded-xl overflow-hidden`}>
                <div className={`${cfg.bg} px-5 py-3 flex items-center gap-2`}>
                  {cfg.icon}
                  <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">{cfg.label}</h2>
                  <span className="text-xs text-zinc-500 dark:text-zinc-400 ml-auto">{items.length}</span>
                </div>
                <div className="bg-white dark:bg-zinc-900 divide-y divide-zinc-100 dark:divide-zinc-800">
                  {items.map((n, i) => (
                    <div key={i} className="px-5 py-3 flex items-start gap-3">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-zinc-900 dark:text-zinc-100">{n.entity_name}</p>
                        <p className="text-sm text-zinc-600 dark:text-zinc-400 mt-0.5">{n.description}</p>
                        {n.days_value != null && (
                          <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1">
                            {n.days_value > 0 ? `${n.days_value} days overdue` : n.days_value === 0 ? 'Due today' : `Due in ${Math.abs(n.days_value)} days`}
                          </p>
                        )}
                      </div>
                      <div className="flex gap-1.5 shrink-0">
                        {n.contact_id && (
                          <button onClick={() => onNavigate({ type: 'contact', id: n.contact_id! })} className="p-1 rounded hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-500 dark:text-zinc-400">
                            <User className="w-3.5 h-3.5" />
                          </button>
                        )}
                        {n.project_id && (
                          <button onClick={() => onNavigate({ type: 'project', id: n.project_id! })} className="p-1 rounded hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-500 dark:text-zinc-400">
                            <FolderOpen className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </main>
  )
}

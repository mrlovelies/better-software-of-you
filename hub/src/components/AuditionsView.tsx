import { useState, useEffect } from 'react'
import { Mic, Clock, Send, CheckCircle2, XCircle, AlertCircle } from 'lucide-react'

interface Audition {
  id: number
  project_name: string
  role_name: string | null
  role_type: string | null
  production_type: string | null
  casting_director: string | null
  casting_company: string | null
  source: string | null
  status: string
  received_at: string | null
  deadline: string | null
  submitted_at: string | null
  callback_date: string | null
  notes: string | null
  urgency: string | null
  days_until_deadline: number | null
  agent_name: string | null
}

const statusConfig: Record<string, { icon: React.ReactNode; color: string; label: string }> = {
  new: { icon: <AlertCircle className="w-4 h-4" />, color: 'bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-400 border-blue-200 dark:border-blue-800', label: 'New' },
  preparing: { icon: <Clock className="w-4 h-4" />, color: 'bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400 border-amber-200 dark:border-amber-800', label: 'Preparing' },
  submitted: { icon: <Send className="w-4 h-4" />, color: 'bg-purple-50 dark:bg-purple-900/20 text-purple-700 dark:text-purple-400 border-purple-200 dark:border-purple-800', label: 'Submitted' },
  callback: { icon: <CheckCircle2 className="w-4 h-4" />, color: 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800', label: 'Callback' },
  booked: { icon: <CheckCircle2 className="w-4 h-4" />, color: 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800', label: 'Booked' },
  passed: { icon: <XCircle className="w-4 h-4" />, color: 'bg-zinc-50 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 border-zinc-200 dark:border-zinc-700', label: 'Passed' },
}

function timeAgo(dateStr: string): string {
  const d = new Date(dateStr)
  const now = new Date()
  const days = Math.floor((now.getTime() - d.getTime()) / 86400000)
  if (days === 0) return 'today'
  if (days === 1) return 'yesterday'
  if (days < 7) return `${days}d ago`
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export default function AuditionsView() {
  const [auditions, setAuditions] = useState<Audition[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/auditions')
      .then(res => res.json())
      .then(data => { setAuditions(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  if (loading) return <main className="flex-1 flex items-center justify-center min-h-screen"><p className="text-sm text-zinc-500 dark:text-zinc-400">Loading...</p></main>

  const active = auditions.filter(a => !['passed', 'booked'].includes(a.status))
  const past = auditions.filter(a => ['passed', 'booked'].includes(a.status))

  return (
    <main className="flex-1 min-h-screen p-6 lg:p-10">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center gap-3 mb-6">
          <Mic className="w-6 h-6 text-zinc-500" />
          <h1 className="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">Auditions</h1>
          <span className="text-sm text-zinc-500 dark:text-zinc-400">{auditions.length} total</span>
        </div>

        {/* Status summary */}
        <div className="flex flex-wrap gap-2 mb-6">
          {Object.entries(
            auditions.reduce<Record<string, number>>((acc, a) => { acc[a.status] = (acc[a.status] || 0) + 1; return acc }, {})
          ).map(([status, count]) => {
            const cfg = statusConfig[status] || statusConfig.new
            return (
              <span key={status} className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-sm font-medium ${cfg.color}`}>
                {cfg.icon} {cfg.label}: {count}
              </span>
            )
          })}
        </div>

        {/* Active auditions */}
        {active.length > 0 && (
          <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl mb-6">
            <div className="px-5 py-4 border-b border-zinc-100 dark:border-zinc-800">
              <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Active Pipeline</h2>
            </div>
            <div className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {active.map(a => {
                const cfg = statusConfig[a.status] || statusConfig.new
                return (
                  <div key={a.id} className="px-5 py-4">
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-zinc-900 dark:text-zinc-100">{a.project_name}</p>
                        {a.role_name && <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">Role: {a.role_name}</p>}
                        <div className="flex flex-wrap items-center gap-2 mt-2">
                          <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full border ${cfg.color}`}>
                            {cfg.icon} {cfg.label}
                          </span>
                          {a.casting_director && <span className="text-xs text-zinc-500 dark:text-zinc-400">CD: {a.casting_director}</span>}
                          {a.agent_name && <span className="text-xs text-zinc-500 dark:text-zinc-400">via {a.agent_name}</span>}
                        </div>
                      </div>
                      <div className="text-right shrink-0">
                        {a.deadline && (
                          <p className={`text-xs font-medium ${a.days_until_deadline != null && a.days_until_deadline < 2 ? 'text-red-600 dark:text-red-400' : 'text-zinc-500 dark:text-zinc-400'}`}>
                            {a.days_until_deadline != null && a.days_until_deadline >= 0 ? `${a.days_until_deadline}d left` : 'overdue'}
                          </p>
                        )}
                        {a.received_at && <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">{timeAgo(a.received_at)}</p>}
                      </div>
                    </div>
                    {a.notes && <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-2 leading-relaxed">{a.notes}</p>}
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* Past */}
        {past.length > 0 && (
          <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl">
            <div className="px-5 py-4 border-b border-zinc-100 dark:border-zinc-800">
              <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Past</h2>
            </div>
            <div className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {past.map(a => {
                const cfg = statusConfig[a.status] || statusConfig.new
                return (
                  <div key={a.id} className="px-5 py-3 flex items-center justify-between opacity-60">
                    <div className="min-w-0">
                      <p className="text-sm text-zinc-700 dark:text-zinc-300">{a.project_name}{a.role_name ? ` — ${a.role_name}` : ''}</p>
                    </div>
                    <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full border shrink-0 ${cfg.color}`}>
                      {cfg.label}
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>
    </main>
  )
}

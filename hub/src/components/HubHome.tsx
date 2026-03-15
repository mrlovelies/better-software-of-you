import { useState, useEffect } from 'react'
import type { ContentRoute } from '../types'
import {
  Users,
  Mail,
  Calendar,
  MessageSquare,
  Scale,
  BookOpen,
  StickyNote,
  FolderOpen,
  AlertTriangle,
  Clock,
  Bell,
  MapPin,
  CheckCircle2,
  Circle,
  Activity,
} from 'lucide-react'

interface HomeData {
  user_name: string | null
  badges: Record<string, number>
  projects: Array<{
    id: number
    name: string
    status: string
    completion_pct: number
    total_tasks: number
    done_tasks: number
    overdue_tasks: number
    days_to_target: number | null
  }>
  upcoming_events: Array<{
    title: string
    start_time: string
    end_time: string
    location: string | null
    all_day: number
  }>
  nudges: { urgent: number; soon: number; awareness: number }
  recent_activity: Array<{
    entity_type: string
    action: string
    details: string
    created_at: string
  }>
}

function StatCard({
  icon: Icon,
  label,
  value,
  onClick,
}: {
  icon: React.ComponentType<{ className?: string }>
  label: string
  value: number
  onClick?: () => void
}) {
  const Wrapper = onClick ? 'button' : 'div'
  return (
    <Wrapper
      onClick={onClick}
      className={`bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl p-4 flex items-center gap-3 transition-all
        ${onClick ? 'hover:border-blue-300 dark:hover:border-blue-600 hover:shadow-sm cursor-pointer' : ''}`}
    >
      <div className="p-2 rounded-lg bg-zinc-50 dark:bg-zinc-800">
        <Icon className="w-5 h-5 text-zinc-500 dark:text-zinc-400" />
      </div>
      <div className="text-left">
        <p className="text-2xl font-semibold text-zinc-900 dark:text-zinc-100 leading-none">{value}</p>
        <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">{label}</p>
      </div>
    </Wrapper>
  )
}

function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)

  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays === 1) return 'yesterday'
  if (diffDays < 7) return `${diffDays}d ago`
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function formatEventTime(dateStr: string, allDay: number): string {
  if (allDay) return 'All day'
  const date = new Date(dateStr)
  const now = new Date()
  const tomorrow = new Date(now)
  tomorrow.setDate(tomorrow.getDate() + 1)

  const timeStr = date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })

  if (date.toDateString() === now.toDateString()) return `Today ${timeStr}`
  if (date.toDateString() === tomorrow.toDateString()) return `Tomorrow ${timeStr}`
  return date.toLocaleDateString('en-US', { weekday: 'short' }) + ' ' + timeStr
}

function getGreeting(): string {
  const hour = new Date().getHours()
  if (hour < 12) return 'Good morning'
  if (hour < 17) return 'Good afternoon'
  return 'Good evening'
}

function actionIcon(action: string) {
  if (action.includes('created') || action.includes('added')) return <Circle className="w-3 h-3 text-blue-500 dark:text-blue-400" />
  if (action.includes('completed') || action.includes('done') || action.includes('milestone')) return <CheckCircle2 className="w-3 h-3 text-emerald-500 dark:text-emerald-400" />
  return <Activity className="w-3 h-3 text-zinc-500 dark:text-zinc-400" />
}

export default function HubHome({ onNavigate }: { onNavigate: (route: ContentRoute) => void }) {
  const [data, setData] = useState<HomeData | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    fetch('/api/home')
      .then(res => res.json())
      .then(setData)
      .catch(() => setError(true))
  }, [])

  if (error) {
    return (
      <main className="flex-1 flex items-center justify-center min-h-screen">
        <p className="text-sm text-zinc-500 dark:text-zinc-400">Couldn't load home data.</p>
      </main>
    )
  }

  if (!data) {
    return (
      <main className="flex-1 flex items-center justify-center min-h-screen">
        <p className="text-sm text-zinc-500 dark:text-zinc-400">Loading...</p>
      </main>
    )
  }

  const totalNudges = data.nudges.urgent + data.nudges.soon + data.nudges.awareness
  const activeProjects = data.projects.filter(p => p.status === 'active')

  return (
    <main className="flex-1 min-h-screen p-6 lg:p-10">
      <div className="max-w-5xl mx-auto">
        {/* Greeting */}
        <div className="mb-8">
          <h1 className="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">
            {getGreeting()}{data.user_name ? `, ${data.user_name}` : ''}
          </h1>
          <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-1">
            {new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })}
          </p>
        </div>

        {/* Nudge banner */}
        {data.nudges.urgent > 0 && (
          <button
            onClick={() => onNavigate({ type: 'nudges' })}
            className="w-full mb-6 flex items-center gap-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl px-4 py-3 hover:bg-red-100 dark:hover:bg-red-900/30 transition-colors cursor-pointer"
          >
            <AlertTriangle className="w-5 h-5 text-red-500 dark:text-red-400 shrink-0" />
            <span className="text-sm font-medium text-red-700 dark:text-red-300">
              {data.nudges.urgent} item{data.nudges.urgent !== 1 ? 's' : ''} need{data.nudges.urgent === 1 ? 's' : ''} your attention
            </span>
          </button>
        )}

        {/* Stats grid */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-8">
          <StatCard icon={Users} label="Contacts" value={data.badges.contacts ?? 0} onClick={() => onNavigate({ type: 'page', filename: 'contacts.html' })} />
          <StatCard icon={Mail} label="Emails" value={data.badges.emails ?? 0} onClick={() => onNavigate({ type: 'emails' })} />
          <StatCard icon={FolderOpen} label="Projects" value={activeProjects.length} />
          {totalNudges > 0 ? (
            <StatCard icon={Bell} label="Nudges" value={totalNudges} onClick={() => onNavigate({ type: 'nudges' })} />
          ) : (
            <StatCard icon={Scale} label="Decisions" value={data.badges.decisions ?? 0} onClick={() => onNavigate({ type: 'decisions' })} />
          )}
        </div>

        <div className="grid lg:grid-cols-3 gap-6">
          {/* Left column */}
          <div className="lg:col-span-2 space-y-6">
            {/* Projects */}
            {activeProjects.length > 0 && (
              <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl">
                <div className="px-5 py-4 border-b border-zinc-100 dark:border-zinc-800">
                  <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Active Projects</h2>
                </div>
                <div className="divide-y divide-zinc-100 dark:divide-zinc-800">
                  {activeProjects.map(p => (
                    <div key={p.id} className="px-5 py-3.5 flex items-center gap-4">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-zinc-900 dark:text-zinc-100 truncate">{p.name}</p>
                        <div className="flex items-center gap-3 mt-1.5">
                          {/* Progress bar */}
                          <div className="flex-1 h-1.5 bg-zinc-100 dark:bg-zinc-800 rounded-full overflow-hidden">
                            <div
                              className="h-full bg-emerald-500 dark:bg-emerald-400 rounded-full transition-all duration-500"
                              style={{ width: `${p.completion_pct}%` }}
                            />
                          </div>
                          <span className="text-xs text-zinc-500 dark:text-zinc-400 shrink-0 tabular-nums">
                            {p.done_tasks}/{p.total_tasks}
                          </span>
                        </div>
                      </div>
                      {p.overdue_tasks > 0 && (
                        <span className="text-xs px-2 py-0.5 rounded-full bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 font-medium shrink-0">
                          {p.overdue_tasks} overdue
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Recent Activity */}
            {data.recent_activity.length > 0 && (
              <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl">
                <div className="px-5 py-4 border-b border-zinc-100 dark:border-zinc-800">
                  <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Recent Activity</h2>
                </div>
                <div className="px-5 py-3 space-y-3">
                  {data.recent_activity.map((a, i) => (
                    <div key={i} className="flex items-start gap-2.5">
                      <div className="mt-1 shrink-0">{actionIcon(a.action)}</div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm text-zinc-700 dark:text-zinc-300 leading-snug">{a.details}</p>
                        <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">{formatRelativeTime(a.created_at)}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Right column */}
          <div className="space-y-6">
            {/* Upcoming */}
            {data.upcoming_events.length > 0 && (
              <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl">
                <div className="px-5 py-4 border-b border-zinc-100 dark:border-zinc-800">
                  <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
                    <Calendar className="w-4 h-4 text-zinc-500 dark:text-zinc-400" />
                    Coming Up
                  </h2>
                </div>
                <div className="divide-y divide-zinc-100 dark:divide-zinc-800">
                  {data.upcoming_events.map((ev, i) => (
                    <div key={i} className="px-5 py-3">
                      <p className="text-sm font-medium text-zinc-900 dark:text-zinc-100">{ev.title}</p>
                      <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5 flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {formatEventTime(ev.start_time, ev.all_day)}
                      </p>
                      {ev.location && (
                        <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5 flex items-center gap-1">
                          <MapPin className="w-3 h-3" />
                          <span className="truncate">{ev.location}</span>
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Quick stats */}
            <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl">
              <div className="px-5 py-4 border-b border-zinc-100 dark:border-zinc-800">
                <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Data at a Glance</h2>
              </div>
              <div className="px-5 py-3 space-y-2.5">
                {[
                  { icon: MessageSquare, label: 'Transcripts', value: data.badges.transcripts ?? 0 },
                  { icon: BookOpen, label: 'Journal Entries', value: data.badges.journal ?? 0 },
                  { icon: StickyNote, label: 'Notes', value: data.badges.notes ?? 0 },
                  { icon: Scale, label: 'Decisions', value: data.badges.decisions ?? 0 },
                ].map(({ icon: Icon, label, value }) => (
                  <div key={label} className="flex items-center justify-between">
                    <span className="text-sm text-zinc-600 dark:text-zinc-400 flex items-center gap-2">
                      <Icon className="w-3.5 h-3.5" />
                      {label}
                    </span>
                    <span className="text-sm font-medium text-zinc-900 dark:text-zinc-100 tabular-nums">{value}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>
  )
}

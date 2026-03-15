import { useState, useEffect } from 'react'
import { Calendar, Clock, MapPin, FolderOpen } from 'lucide-react'
import type { ContentRoute } from '../types'

interface CalendarEvent {
  id: number
  title: string
  description: string | null
  location: string | null
  start_time: string
  end_time: string | null
  all_day: number
  status: string | null
  attendees: string | null
  project_id: number | null
  project_name: string | null
}

function formatTime(dateStr: string): string {
  return new Date(dateStr).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
}

function formatDateHeader(dateStr: string): string {
  const d = new Date(dateStr)
  const now = new Date()
  const tomorrow = new Date(now)
  tomorrow.setDate(tomorrow.getDate() + 1)
  if (d.toDateString() === now.toDateString()) return 'Today'
  if (d.toDateString() === tomorrow.toDateString()) return 'Tomorrow'
  return d.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })
}

function getDateKey(dateStr: string): string {
  return new Date(dateStr).toDateString()
}

export default function CalendarView({ onNavigate }: { onNavigate: (r: ContentRoute) => void }) {
  const [events, setEvents] = useState<CalendarEvent[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/calendar')
      .then(res => res.json())
      .then(data => { setEvents(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  if (loading) return <main className="flex-1 flex items-center justify-center min-h-screen"><p className="text-sm text-zinc-500 dark:text-zinc-400">Loading...</p></main>

  // Group by day
  const dayGroups: Array<{ key: string; label: string; events: CalendarEvent[] }> = []
  const seen = new Set<string>()
  for (const ev of events) {
    const key = getDateKey(ev.start_time)
    if (!seen.has(key)) {
      seen.add(key)
      dayGroups.push({ key, label: formatDateHeader(ev.start_time), events: [] })
    }
    dayGroups.find(g => g.key === key)!.events.push(ev)
  }

  const isPast = (dateStr: string) => new Date(dateStr) < new Date()

  return (
    <main className="flex-1 min-h-screen p-6 lg:p-10">
      <div className="max-w-3xl mx-auto">
        <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-2 mb-6">
          <Calendar className="w-5 h-5" /> Calendar
          <span className="text-sm font-normal text-zinc-500 dark:text-zinc-400 ml-2">{events.length} events</span>
        </h1>

        {events.length === 0 ? (
          <div className="text-center py-16">
            <Calendar className="w-10 h-10 text-zinc-300 dark:text-zinc-600 mx-auto mb-3" />
            <p className="text-sm text-zinc-500 dark:text-zinc-400">No upcoming events.</p>
          </div>
        ) : (
          <div className="space-y-6">
            {dayGroups.map(group => (
              <div key={group.key}>
                <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 mb-2">{group.label}</h2>
                <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl divide-y divide-zinc-100 dark:divide-zinc-800">
                  {group.events.map(ev => (
                    <div key={ev.id} className={`px-5 py-3 ${isPast(ev.start_time) ? 'opacity-50' : ''}`}>
                      <div className="flex items-start gap-3">
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium text-zinc-900 dark:text-zinc-100">{ev.title}</p>
                          <div className="flex items-center gap-3 mt-1">
                            <span className="text-xs text-zinc-500 dark:text-zinc-400 flex items-center gap-1">
                              <Clock className="w-3 h-3" />
                              {ev.all_day ? 'All day' : `${formatTime(ev.start_time)}${ev.end_time ? ` – ${formatTime(ev.end_time)}` : ''}`}
                            </span>
                            {ev.location && (
                              <span className="text-xs text-zinc-500 dark:text-zinc-400 flex items-center gap-1 truncate">
                                <MapPin className="w-3 h-3 shrink-0" />
                                <span className="truncate">{ev.location}</span>
                              </span>
                            )}
                          </div>
                        </div>
                        {ev.project_id && (
                          <button onClick={() => onNavigate({ type: 'project', id: ev.project_id! })} className="text-xs text-blue-600 dark:text-blue-400 hover:underline flex items-center gap-1 shrink-0">
                            <FolderOpen className="w-3 h-3" /> {ev.project_name}
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </main>
  )
}

import { useState, useEffect } from 'react'
import { BookOpen } from 'lucide-react'

interface JournalEntry {
  id: number
  content: string | null
  mood: string | null
  energy: number | null
  highlights: string | null
  entry_date: string | null
  linked_contacts: string | null
  linked_projects: string | null
  created_at: string | null
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr)
  const now = new Date()
  const days = Math.floor((now.getTime() - d.getTime()) / 86400000)
  if (days === 0) return 'Today'
  if (days === 1) return 'Yesterday'
  return d.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })
}

const moodColors: Record<string, string> = {
  great: 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400',
  good: 'bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-400',
  okay: 'bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400',
  low: 'bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400',
  rough: 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400',
}

export default function JournalView() {
  const [entries, setEntries] = useState<JournalEntry[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/journal')
      .then(res => res.json())
      .then(data => { setEntries(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  if (loading) return <main className="flex-1 flex items-center justify-center min-h-screen"><p className="text-sm text-zinc-500 dark:text-zinc-400">Loading...</p></main>

  return (
    <main className="flex-1 min-h-screen p-6 lg:p-10">
      <div className="max-w-3xl mx-auto">
        <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-2 mb-6">
          <BookOpen className="w-5 h-5" /> Journal
          <span className="text-sm font-normal text-zinc-500 dark:text-zinc-400 ml-2">{entries.length} entries</span>
        </h1>

        {entries.length === 0 ? (
          <div className="text-center py-16">
            <BookOpen className="w-10 h-10 text-zinc-300 dark:text-zinc-600 mx-auto mb-3" />
            <p className="text-sm text-zinc-500 dark:text-zinc-400">No journal entries yet. Start writing to see them here.</p>
          </div>
        ) : (
          <div className="space-y-4">
            {entries.map(e => {
              let highlights: string[] = []
              try { highlights = e.highlights ? JSON.parse(e.highlights) : [] } catch { /* ignore */ }

              return (
                <div key={e.id} className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl px-5 py-4">
                  <div className="flex items-center gap-3 mb-2">
                    <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                      {e.entry_date ? formatDate(e.entry_date) : 'Undated'}
                    </h3>
                    {e.mood && (
                      <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${moodColors[e.mood] || moodColors.okay}`}>
                        {e.mood}
                      </span>
                    )}
                    {e.energy != null && (
                      <span className="text-xs text-zinc-500 dark:text-zinc-400">
                        Energy: {'●'.repeat(e.energy)}{'○'.repeat(Math.max(0, 5 - e.energy))}
                      </span>
                    )}
                  </div>
                  {e.content && (
                    <p className="text-sm text-zinc-700 dark:text-zinc-300 whitespace-pre-wrap">{e.content}</p>
                  )}
                  {highlights.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mt-3">
                      {highlights.map((h, i) => (
                        <span key={i} className="text-xs px-2 py-0.5 rounded-full bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400">{h}</span>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </main>
  )
}

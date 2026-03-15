import { useState, useEffect, useRef } from 'react'
import { StickyNote, Pin, Search } from 'lucide-react'

interface Note {
  id: number
  title: string | null
  content: string | null
  tags: string | null
  pinned: number
  linked_contacts: string | null
  linked_projects: string | null
  created_at: string | null
  updated_at: string | null
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

export default function NotesView() {
  const [notes, setNotes] = useState<Note[]>([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const debounceRef = useRef<ReturnType<typeof setTimeout>>()

  function fetchNotes(q: string) {
    const url = q ? `/api/notes?q=${encodeURIComponent(q)}` : '/api/notes'
    fetch(url)
      .then(res => res.json())
      .then(data => { setNotes(data); setLoading(false) })
      .catch(() => setLoading(false))
  }

  useEffect(() => { fetchNotes('') }, [])

  function handleSearch(val: string) {
    setQuery(val)
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => fetchNotes(val), 300)
  }

  if (loading) return <main className="flex-1 flex items-center justify-center min-h-screen"><p className="text-sm text-zinc-500 dark:text-zinc-400">Loading...</p></main>

  return (
    <main className="flex-1 min-h-screen p-6 lg:p-10">
      <div className="max-w-3xl mx-auto">
        <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-2 mb-6">
          <StickyNote className="w-5 h-5" /> Notes
          <span className="text-sm font-normal text-zinc-500 dark:text-zinc-400 ml-2">{notes.length} notes</span>
        </h1>

        <div className="relative mb-4">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-400 dark:text-zinc-500 pointer-events-none" />
          <input
            type="text"
            value={query}
            onChange={e => handleSearch(e.target.value)}
            placeholder="Search notes..."
            className="w-full pl-10 pr-4 py-2 text-sm bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-lg text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 dark:placeholder-zinc-500 outline-none focus:border-blue-400 dark:focus:border-blue-500"
          />
        </div>

        {notes.length === 0 ? (
          <div className="text-center py-16">
            <StickyNote className="w-10 h-10 text-zinc-300 dark:text-zinc-600 mx-auto mb-3" />
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              {query ? `No notes matching "${query}"` : 'No notes yet.'}
            </p>
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {notes.map(n => {
              let tags: string[] = []
              try { tags = n.tags ? JSON.parse(n.tags) : [] } catch { /* ignore */ }
              const title = n.title || (n.content ? n.content.split('\n')[0].slice(0, 60) : 'Untitled')
              const preview = n.content ? n.content.slice(0, 150) : ''

              return (
                <div key={n.id} className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl px-4 py-3">
                  <div className="flex items-start gap-2">
                    {n.pinned ? <Pin className="w-3.5 h-3.5 text-blue-500 dark:text-blue-400 mt-0.5 shrink-0" /> : null}
                    <div className="flex-1 min-w-0">
                      <h3 className="text-sm font-medium text-zinc-900 dark:text-zinc-100 truncate">{title}</h3>
                      {preview && title !== preview && (
                        <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1 line-clamp-2">{preview}</p>
                      )}
                      <div className="flex items-center gap-2 mt-2">
                        {tags.length > 0 && (
                          <div className="flex gap-1 flex-wrap">
                            {tags.slice(0, 3).map((t, i) => (
                              <span key={i} className="text-[0.625rem] px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400">{t}</span>
                            ))}
                          </div>
                        )}
                        <span className="text-[0.625rem] text-zinc-400 dark:text-zinc-500 ml-auto shrink-0">
                          {n.updated_at ? timeAgo(n.updated_at) : ''}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </main>
  )
}

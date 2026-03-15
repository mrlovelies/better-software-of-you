import { useState, useEffect } from 'react'
import { Mail, ArrowUpRight, ArrowDownLeft, Star } from 'lucide-react'
import type { ContentRoute } from '../types'

interface Email {
  id: number
  subject: string | null
  snippet: string | null
  from_name: string | null
  from_address: string | null
  to_addresses: string | null
  direction: string | null
  is_read: number
  is_starred: number
  received_at: string | null
  thread_id: string | null
  labels: string | null
  contact_id: number | null
  contact_name: string | null
}

function timeAgo(dateStr: string): string {
  const d = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const mins = Math.floor(diffMs / 60000)
  const hours = Math.floor(diffMs / 3600000)
  const days = Math.floor(diffMs / 86400000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  if (hours < 24) return `${hours}h ago`
  if (days === 1) return 'yesterday'
  if (days < 7) return `${days}d ago`
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export default function EmailView({ onNavigate }: { onNavigate: (r: ContentRoute) => void }) {
  const [emails, setEmails] = useState<Email[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/emails')
      .then(res => res.json())
      .then(data => { setEmails(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  if (loading) return <main className="flex-1 flex items-center justify-center min-h-screen"><p className="text-sm text-zinc-500 dark:text-zinc-400">Loading...</p></main>

  const unread = emails.filter(e => !e.is_read)
  const read = emails.filter(e => e.is_read)

  return (
    <main className="flex-1 min-h-screen p-6 lg:p-10">
      <div className="max-w-4xl mx-auto">
        <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-2 mb-6">
          <Mail className="w-5 h-5" /> Email
          <span className="text-sm font-normal text-zinc-500 dark:text-zinc-400 ml-2">
            {emails.length} messages{unread.length > 0 && `, ${unread.length} unread`}
          </span>
        </h1>

        {emails.length === 0 ? (
          <div className="text-center py-16">
            <Mail className="w-10 h-10 text-zinc-300 dark:text-zinc-600 mx-auto mb-3" />
            <p className="text-sm text-zinc-500 dark:text-zinc-400">No emails synced yet.</p>
          </div>
        ) : (
          <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl divide-y divide-zinc-100 dark:divide-zinc-800">
            {[...unread, ...read].map(e => (
              <div key={e.id} className={`px-5 py-3 flex items-start gap-3 ${!e.is_read ? 'bg-blue-50/50 dark:bg-blue-900/10' : ''}`}>
                <div className="mt-0.5 shrink-0">
                  {e.direction === 'outbound' ? (
                    <ArrowUpRight className="w-4 h-4 text-emerald-500 dark:text-emerald-400" />
                  ) : (
                    <ArrowDownLeft className="w-4 h-4 text-blue-500 dark:text-blue-400" />
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <p className={`text-sm truncate ${!e.is_read ? 'font-semibold text-zinc-900 dark:text-zinc-100' : 'text-zinc-900 dark:text-zinc-100'}`}>
                      {e.subject || '(no subject)'}
                    </p>
                    {e.is_starred ? <Star className="w-3.5 h-3.5 text-amber-500 fill-amber-500 shrink-0" /> : null}
                  </div>
                  <div className="flex items-center gap-2 mt-0.5">
                    {e.contact_name ? (
                      <button
                        onClick={() => e.contact_id && onNavigate({ type: 'contact', id: e.contact_id })}
                        className="text-xs text-blue-600 dark:text-blue-400 hover:underline truncate"
                      >
                        {e.contact_name}
                      </button>
                    ) : (
                      <span className="text-xs text-zinc-500 dark:text-zinc-400 truncate">
                        {e.from_name || e.from_address}
                      </span>
                    )}
                  </div>
                  {e.snippet && (
                    <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1 line-clamp-1">{e.snippet}</p>
                  )}
                </div>
                <span className="text-xs text-zinc-500 dark:text-zinc-400 shrink-0 whitespace-nowrap">
                  {e.received_at ? timeAgo(e.received_at) : ''}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </main>
  )
}

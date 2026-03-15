import { useState, useEffect } from 'react'
import {
  User,
  Building2,
  Briefcase,
  Mail,
  Phone,
  FolderOpen,
  MessageSquare,
  ArrowUpRight,
  ArrowDownLeft,
  Clock,
  Trash2,
} from 'lucide-react'
import type { ContentRoute } from '../types'

interface ContactData {
  id: number
  name: string
  email: string | null
  phone: string | null
  company: string | null
  role: string | null
  type: string
  status: string
  notes: string | null
  created_at: string
  projects: Array<{ id: number; name: string; status: string; completion_pct: number }>
  interactions: Array<{ id: number; interaction_type: string; summary: string; occurred_at: string }>
  emails: Array<{ id: number; subject: string; snippet: string; direction: string; received_at: string }>
  page_filename: string | null
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

export default function ContactView({ contactId, onNavigate }: { contactId: number; onNavigate: (r: ContentRoute) => void }) {
  const [data, setData] = useState<ContactData | null>(null)
  const [error, setError] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    fetch(`/api/contacts/${contactId}`)
      .then(res => res.json())
      .then(setData)
      .catch(() => setError(true))
  }, [contactId])

  function handleDelete() {
    setDeleting(true)
    fetch(`/api/contacts/${contactId}`, { method: 'DELETE' })
      .then(res => res.json())
      .then(() => onNavigate({ type: 'home' }))
      .catch(() => { setDeleting(false); setConfirmDelete(false) })
  }

  if (error) return <main className="flex-1 flex items-center justify-center min-h-screen"><p className="text-sm text-zinc-500">Failed to load contact.</p></main>
  if (!data) return <main className="flex-1 flex items-center justify-center min-h-screen"><p className="text-sm text-zinc-500 dark:text-zinc-400">Loading...</p></main>

  return (
    <main className="flex-1 min-h-screen p-6 lg:p-10">
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl p-6 mb-6">
          <div className="flex items-start gap-4">
            <div className="w-14 h-14 rounded-full bg-blue-50 dark:bg-blue-900/30 flex items-center justify-center shrink-0">
              <User className="w-7 h-7 text-blue-600 dark:text-blue-400" />
            </div>
            <div className="flex-1 min-w-0">
              <h1 className="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">{data.name}</h1>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-1.5 text-sm text-zinc-500 dark:text-zinc-400">
                {data.company && (
                  <span className="flex items-center gap-1.5"><Building2 className="w-3.5 h-3.5" />{data.company}</span>
                )}
                {data.role && (
                  <span className="flex items-center gap-1.5"><Briefcase className="w-3.5 h-3.5" />{data.role}</span>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-2 text-sm">
                {data.email && (
                  <a href={`mailto:${data.email}`} className="flex items-center gap-1.5 text-blue-600 dark:text-blue-400 hover:underline">
                    <Mail className="w-3.5 h-3.5" />{data.email}
                  </a>
                )}
                {data.phone && (
                  <span className="flex items-center gap-1.5 text-zinc-500 dark:text-zinc-400"><Phone className="w-3.5 h-3.5" />{data.phone}</span>
                )}
              </div>
              {data.notes && (
                <p className="mt-3 text-sm text-zinc-600 dark:text-zinc-400 leading-relaxed">{data.notes}</p>
              )}
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {data.page_filename && (
                <button
                  onClick={() => onNavigate({ type: 'page', filename: data.page_filename! })}
                  className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
                >
                  Full Brief →
                </button>
              )}
              <button
                onClick={() => setConfirmDelete(true)}
                className="p-1.5 rounded-md text-zinc-500 hover:text-red-500 dark:text-zinc-400 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
                title="Delete contact"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>

        {/* Delete confirmation */}
        {confirmDelete && (
          <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl p-4 mb-6">
            <p className="text-sm text-red-700 dark:text-red-300 font-medium">
              Delete "{data.name}" and all their interactions, follow-ups, and generated pages?
            </p>
            <p className="text-xs text-red-600/70 dark:text-red-400/70 mt-1">This cannot be undone. Linked projects will keep their data but lose this client association.</p>
            <div className="flex gap-2 mt-3">
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="px-3 py-1.5 text-xs font-medium bg-red-600 text-white rounded-md hover:bg-red-700 disabled:opacity-50 transition-colors"
              >
                {deleting ? 'Deleting...' : 'Delete Contact'}
              </button>
              <button
                onClick={() => setConfirmDelete(false)}
                disabled={deleting}
                className="px-3 py-1.5 text-xs font-medium text-zinc-600 dark:text-zinc-400 bg-white dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 rounded-md hover:bg-zinc-50 dark:hover:bg-zinc-700 disabled:opacity-50 transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        <div className="grid lg:grid-cols-3 gap-6">
          {/* Left: activity */}
          <div className="lg:col-span-2 space-y-6">
            {/* Projects */}
            {data.projects.length > 0 && (
              <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl">
                <div className="px-5 py-4 border-b border-zinc-100 dark:border-zinc-800">
                  <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
                    <FolderOpen className="w-4 h-4 text-zinc-500" /> Projects
                  </h2>
                </div>
                <div className="divide-y divide-zinc-100 dark:divide-zinc-800">
                  {data.projects.map(p => (
                    <button
                      key={p.id}
                      onClick={() => onNavigate({ type: 'project', id: p.id })}
                      className="w-full text-left px-5 py-3 hover:bg-zinc-50 dark:hover:bg-zinc-800 transition-colors"
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium text-zinc-900 dark:text-zinc-100">{p.name}</span>
                        <span className={`text-xs px-2 py-0.5 rounded-full font-medium
                          ${p.status === 'active' ? 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400'
                            : 'bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400'}`}>
                          {p.status}
                        </span>
                      </div>
                      {p.completion_pct > 0 && (
                        <div className="flex items-center gap-2 mt-1.5">
                          <div className="flex-1 h-1.5 bg-zinc-100 dark:bg-zinc-800 rounded-full overflow-hidden">
                            <div className="h-full bg-emerald-500 dark:bg-emerald-400 rounded-full" style={{ width: `${p.completion_pct}%` }} />
                          </div>
                          <span className="text-xs text-zinc-500 dark:text-zinc-400 tabular-nums">{p.completion_pct}%</span>
                        </div>
                      )}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Recent emails */}
            {data.emails.length > 0 && (
              <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl">
                <div className="px-5 py-4 border-b border-zinc-100 dark:border-zinc-800">
                  <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
                    <Mail className="w-4 h-4 text-zinc-500" /> Recent Emails
                  </h2>
                </div>
                <div className="divide-y divide-zinc-100 dark:divide-zinc-800">
                  {data.emails.slice(0, 10).map(e => (
                    <div key={e.id} className="px-5 py-3">
                      <div className="flex items-center gap-2">
                        {e.direction === 'sent' ? (
                          <ArrowUpRight className="w-3.5 h-3.5 text-blue-500 shrink-0" />
                        ) : (
                          <ArrowDownLeft className="w-3.5 h-3.5 text-zinc-500 shrink-0" />
                        )}
                        <span className="text-sm font-medium text-zinc-900 dark:text-zinc-100 truncate">{e.subject || '(no subject)'}</span>
                        <span className="text-xs text-zinc-500 dark:text-zinc-400 shrink-0 ml-auto">{timeAgo(e.received_at)}</span>
                      </div>
                      {e.snippet && <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1 truncate pl-5.5">{e.snippet}</p>}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Right: quick info */}
          <div className="space-y-6">
            {/* Interactions */}
            {data.interactions.length > 0 && (
              <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl">
                <div className="px-5 py-4 border-b border-zinc-100 dark:border-zinc-800">
                  <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
                    <MessageSquare className="w-4 h-4 text-zinc-500" /> Interactions
                  </h2>
                </div>
                <div className="px-5 py-3 space-y-3">
                  {data.interactions.slice(0, 8).map(i => (
                    <div key={i.id}>
                      <div className="flex items-center gap-2">
                        <span className="text-xs px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 font-medium">
                          {i.interaction_type}
                        </span>
                        <span className="text-xs text-zinc-500 dark:text-zinc-400 ml-auto">{timeAgo(i.occurred_at)}</span>
                      </div>
                      <p className="text-sm text-zinc-700 dark:text-zinc-300 mt-1 leading-snug">{i.summary}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Meta */}
            <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl px-5 py-4">
              <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 mb-3">Details</h2>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-zinc-500 dark:text-zinc-400">Added</span>
                  <span className="text-zinc-900 dark:text-zinc-100 flex items-center gap-1"><Clock className="w-3 h-3" />{timeAgo(data.created_at)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500 dark:text-zinc-400">Emails</span>
                  <span className="text-zinc-900 dark:text-zinc-100 tabular-nums">{data.emails.length}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500 dark:text-zinc-400">Interactions</span>
                  <span className="text-zinc-900 dark:text-zinc-100 tabular-nums">{data.interactions.length}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500 dark:text-zinc-400">Projects</span>
                  <span className="text-zinc-900 dark:text-zinc-100 tabular-nums">{data.projects.length}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>
  )
}

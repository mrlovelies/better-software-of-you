import { useState, useEffect } from 'react'
import {
  CheckCircle2,
  Circle,
  AlertTriangle,
  Clock,
  Target,
  User,
  FileText,
  BarChart3,
  Lightbulb,
  Trash2,
} from 'lucide-react'
import type { ContentRoute } from '../types'

interface ProjectData {
  project: {
    id: number; name: string; status: string; priority: string
    description: string | null; target_date: string | null
    client_id: number | null; created_at: string; updated_at: string
  }
  client: { id: number; name: string; email: string | null; company: string | null; role: string | null } | null
  tasks: Array<{ id: number; title: string; status: string; priority: string; due_date: string | null; completed_at: string | null }>
  task_stats: { total: number; todo: number; in_progress: number; done: number; blocked: number; completion_pct: number }
  health: { completion_pct: number; overdue_tasks: number; days_to_target: number | null } | null
  milestones: Array<{ id: number; title: string; target_date: string | null; status: string; completed_at: string | null }>
  decisions: Array<{ id: number; title: string; decision: string | null; status: string; decided_at: string }>
  activity: Array<{ action: string; details: string; created_at: string }>
  page_filename: string | null
  sub_views: Array<{ id: number; view_type: string; entity_name: string; filename: string }>
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

const statusIcon: Record<string, React.ReactNode> = {
  done: <CheckCircle2 className="w-4 h-4 text-emerald-500" />,
  in_progress: <Circle className="w-4 h-4 text-blue-500" />,
  todo: <Circle className="w-4 h-4 text-zinc-300 dark:text-zinc-600" />,
  blocked: <AlertTriangle className="w-4 h-4 text-red-500" />,
}

const subViewIcons: Record<string, React.ReactNode> = {
  pm_report: <BarChart3 className="w-4 h-4" />,
  project_analysis: <Lightbulb className="w-4 h-4" />,
  prep_page: <FileText className="w-4 h-4" />,
  project_docs: <FileText className="w-4 h-4" />,
}

export default function ProjectView({ projectId, onNavigate }: { projectId: number; onNavigate: (r: ContentRoute) => void }) {
  const [data, setData] = useState<ProjectData | null>(null)
  const [error, setError] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [togglingTask, setTogglingTask] = useState<number | null>(null)

  function fetchProject() {
    return fetch(`/api/projects/${projectId}`)
      .then(res => res.json())
      .then(setData)
      .catch(() => setError(true))
  }

  useEffect(() => { fetchProject() }, [projectId])

  function toggleTask(task: ProjectData['tasks'][number]) {
    if (togglingTask) return
    setTogglingTask(task.id)
    const newStatus = task.status === 'done' ? 'todo' : 'done'
    fetch(`/api/tasks/${task.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus }),
    })
      .then(() => fetchProject())
      .finally(() => setTogglingTask(null))
  }

  function handleDelete() {
    setDeleting(true)
    fetch(`/api/projects/${projectId}`, { method: 'DELETE' })
      .then(res => res.json())
      .then(() => onNavigate({ type: 'home' }))
      .catch(() => { setDeleting(false); setConfirmDelete(false) })
  }

  if (error) return <main className="flex-1 flex items-center justify-center min-h-screen"><p className="text-sm text-zinc-500">Failed to load project.</p></main>
  if (!data) return <main className="flex-1 flex items-center justify-center min-h-screen"><p className="text-sm text-zinc-500 dark:text-zinc-400">Loading...</p></main>

  const p = data.project
  const stats = data.task_stats

  return (
    <main className="flex-1 min-h-screen p-6 lg:p-10">
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl p-6 mb-6">
          <div className="flex items-start justify-between">
            <div>
              <div className="flex items-center gap-3">
                <h1 className="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">{p.name}</h1>
                <span className={`text-xs px-2.5 py-1 rounded-full font-medium
                  ${p.status === 'active' ? 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400'
                    : p.status === 'idea' ? 'bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400'
                    : 'bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400'}`}>
                  {p.status}
                </span>
              </div>
              {data.client && (
                <button
                  onClick={() => onNavigate({ type: 'contact', id: data.client!.id })}
                  className="flex items-center gap-1.5 mt-2 text-sm text-blue-600 dark:text-blue-400 hover:underline"
                >
                  <User className="w-3.5 h-3.5" /> {data.client.name}
                  {data.client.company && <span className="text-zinc-500">· {data.client.company}</span>}
                </button>
              )}
              {p.description && <p className="mt-3 text-sm text-zinc-600 dark:text-zinc-400">{p.description}</p>}
              {p.target_date && (
                <p className="mt-2 text-xs text-zinc-500 dark:text-zinc-400 flex items-center gap-1">
                  <Target className="w-3 h-3" /> Target: {new Date(p.target_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                </p>
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
                title="Delete project"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          </div>

          {/* Progress bar */}
          {stats.total > 0 && (
            <div className="mt-5">
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-xs text-zinc-500 dark:text-zinc-400">Progress</span>
                <span className="text-xs font-medium text-zinc-700 dark:text-zinc-300 tabular-nums">{stats.done}/{stats.total} tasks</span>
              </div>
              <div className="h-2 bg-zinc-100 dark:bg-zinc-800 rounded-full overflow-hidden">
                <div className="h-full bg-emerald-500 dark:bg-emerald-400 rounded-full transition-all" style={{ width: `${stats.completion_pct}%` }} />
              </div>
              <div className="flex gap-4 mt-2 text-xs text-zinc-500 dark:text-zinc-400">
                {stats.in_progress > 0 && <span className="flex items-center gap-1"><Circle className="w-2.5 h-2.5 text-blue-500" /> {stats.in_progress} in progress</span>}
                {stats.todo > 0 && <span>{stats.todo} to do</span>}
                {stats.blocked > 0 && <span className="text-red-500">{stats.blocked} blocked</span>}
              </div>
            </div>
          )}
        </div>

        {/* Delete confirmation */}
        {confirmDelete && (
          <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl p-4 mb-6">
            <p className="text-sm text-red-700 dark:text-red-300 font-medium">
              Delete "{p.name}" and all its tasks, milestones, decisions, and generated pages?
            </p>
            <p className="text-xs text-red-600/70 dark:text-red-400/70 mt-1">This cannot be undone.</p>
            <div className="flex gap-2 mt-3">
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="px-3 py-1.5 text-xs font-medium bg-red-600 text-white rounded-md hover:bg-red-700 disabled:opacity-50 transition-colors"
              >
                {deleting ? 'Deleting...' : 'Delete Project'}
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

        {/* Sub-views */}
        {data.sub_views.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-6">
            {data.sub_views.map(sv => (
              <button
                key={sv.id}
                onClick={() => onNavigate({ type: 'page', filename: sv.filename })}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 text-sm text-zinc-600 dark:text-zinc-400 hover:border-blue-300 dark:hover:border-blue-600 hover:text-blue-600 dark:hover:text-blue-400 transition-all"
              >
                {subViewIcons[sv.view_type] || <FileText className="w-4 h-4" />}
                {sv.entity_name || sv.view_type.replace('_', ' ')}
              </button>
            ))}
          </div>
        )}

        <div className="grid lg:grid-cols-3 gap-6">
          {/* Left: tasks + decisions */}
          <div className="lg:col-span-2 space-y-6">
            {/* Tasks */}
            {data.tasks.length > 0 && (
              <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl">
                <div className="px-5 py-4 border-b border-zinc-100 dark:border-zinc-800">
                  <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Tasks</h2>
                </div>
                <div className="divide-y divide-zinc-100 dark:divide-zinc-800">
                  {data.tasks.map(t => (
                    <div
                      key={t.id}
                      onClick={() => toggleTask(t)}
                      className={`px-5 py-2.5 flex items-center gap-3 cursor-pointer hover:bg-zinc-50 dark:hover:bg-zinc-800/50 transition-colors ${t.status === 'done' ? 'opacity-50' : ''} ${togglingTask === t.id ? 'opacity-40 pointer-events-none' : ''}`}
                    >
                      {statusIcon[t.status] || statusIcon.todo}
                      <span className={`text-sm flex-1 ${t.status === 'done' ? 'line-through text-zinc-500 dark:text-zinc-400' : 'text-zinc-800 dark:text-zinc-200'}`}>
                        {t.title}
                      </span>
                      {t.due_date && (
                        <span className="text-xs text-zinc-500 dark:text-zinc-400 shrink-0">{timeAgo(t.due_date)}</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Decisions */}
            {data.decisions.length > 0 && (
              <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl">
                <div className="px-5 py-4 border-b border-zinc-100 dark:border-zinc-800">
                  <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Decisions</h2>
                </div>
                <div className="px-5 py-3 space-y-3">
                  {data.decisions.map(d => (
                    <div key={d.id}>
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-zinc-900 dark:text-zinc-100">{d.title}</span>
                        <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium
                          ${d.status === 'decided' ? 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400'
                            : 'bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400'}`}>
                          {d.status}
                        </span>
                      </div>
                      {d.decision && <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">{d.decision}</p>}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Right: milestones + activity */}
          <div className="space-y-6">
            {/* Milestones */}
            {data.milestones.length > 0 && (
              <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl">
                <div className="px-5 py-4 border-b border-zinc-100 dark:border-zinc-800">
                  <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
                    <Target className="w-4 h-4 text-zinc-500" /> Milestones
                  </h2>
                </div>
                <div className="px-5 py-3 space-y-3">
                  {data.milestones.map(m => (
                    <div key={m.id} className="flex items-start gap-2">
                      {m.status === 'completed' ? (
                        <CheckCircle2 className="w-4 h-4 text-emerald-500 mt-0.5 shrink-0" />
                      ) : (
                        <Circle className="w-4 h-4 text-zinc-300 dark:text-zinc-600 mt-0.5 shrink-0" />
                      )}
                      <div>
                        <p className="text-sm text-zinc-800 dark:text-zinc-200">{m.title}</p>
                        {m.target_date && <p className="text-xs text-zinc-500 dark:text-zinc-400">{new Date(m.target_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}</p>}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Activity */}
            {data.activity.length > 0 && (
              <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl">
                <div className="px-5 py-4 border-b border-zinc-100 dark:border-zinc-800">
                  <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
                    <Clock className="w-4 h-4 text-zinc-500" /> Activity
                  </h2>
                </div>
                <div className="px-5 py-3 space-y-2.5">
                  {data.activity.slice(0, 8).map((a, i) => (
                    <div key={i}>
                      <p className="text-sm text-zinc-700 dark:text-zinc-300 leading-snug">{a.details}</p>
                      <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">{timeAgo(a.created_at)}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </main>
  )
}

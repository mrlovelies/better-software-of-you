import { useState, useEffect, useCallback } from 'react'
import { GraduationCap, BookOpen, Calendar, ChevronRight, Sparkles, Brain, Dumbbell, Activity, MessageSquare } from 'lucide-react'

interface DigestListItem {
  id: number
  digest_type: string
  digest_date: string
  title: string
  generation_duration_ms: number | null
  created_at: string
  feedback_count: number
}

interface Section {
  id: string
  type: string
  title: string
  content: string
  domain?: string
  depth_level?: number
}

interface Feedback {
  id: number
  section_id: string
  reaction: string
  comment: string | null
  created_at: string
}

interface DigestDetail {
  id: number
  digest_type: string
  digest_date: string
  title: string
  sections: Section[]
  sources: string | null
  feedback: Feedback[]
}

const reactions = [
  { key: 'got_it', label: 'Got it', icon: '✓' },
  { key: 'tell_me_more', label: 'Tell me more', icon: '→' },
  { key: 'too_basic', label: 'Too basic', icon: '↑' },
  { key: 'too_advanced', label: 'Too advanced', icon: '↓' },
  { key: 'this_clicked', label: 'This clicked', icon: '★' },
]

const sectionIcons: Record<string, React.ComponentType<{ className?: string }>> = {
  recap: BookOpen,
  concept: Brain,
  pattern: Sparkles,
  exercise: Dumbbell,
  health: Activity,
  overview: BookOpen,
  tutorial: GraduationCap,
  reflection: MessageSquare,
}

function timeAgo(dateStr: string): string {
  const d = new Date(dateStr + 'Z')
  const now = new Date()
  const mins = Math.floor((now.getTime() - d.getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  if (days < 7) return `${days}d ago`
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function renderMarkdown(md: string): string {
  let html = md
  html = html.replace(/^### (.+)$/gm, '<h3 class="text-sm font-semibold text-zinc-800 dark:text-zinc-200 mt-4 mb-2">$1</h3>')
  html = html.replace(/^## (.+)$/gm, '<h2 class="text-base font-semibold text-zinc-800 dark:text-zinc-200 mt-4 mb-2">$1</h2>')
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>')
  html = html.replace(/`([^`]+)`/g, '<code class="bg-zinc-100 dark:bg-zinc-800 px-1.5 py-0.5 rounded text-[0.8125rem]">$1</code>')
  html = html.replace(/^- (.+)$/gm, '<li class="ml-4 list-disc text-sm text-zinc-600 dark:text-zinc-400">$1</li>')
  html = html.replace(/^(\d+)\. (.+)$/gm, '<li class="ml-4 list-decimal text-sm text-zinc-600 dark:text-zinc-400">$2</li>')
  // Wrap paragraphs
  html = html.split('\n\n').map(p => {
    const trimmed = p.trim()
    if (!trimmed) return ''
    if (trimmed.startsWith('<h') || trimmed.startsWith('<li') || trimmed.startsWith('<ul') || trimmed.startsWith('<ol')) return trimmed
    return `<p class="text-sm text-zinc-600 dark:text-zinc-400 mb-3 leading-relaxed">${trimmed}</p>`
  }).join('\n')
  return html
}

function SectionCard({
  section,
  existingFeedback,
  onFeedback,
}: {
  section: Section
  existingFeedback: Feedback | undefined
  onFeedback: (sectionId: string, reaction: string) => void
}) {
  const Icon = sectionIcons[section.type] || BookOpen
  const [activeReaction, setActiveReaction] = useState<string | null>(existingFeedback?.reaction || null)

  function handleReaction(reaction: string) {
    setActiveReaction(reaction)
    onFeedback(section.id, reaction)
  }

  return (
    <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl overflow-hidden">
      {/* Header */}
      <div className="px-5 py-3 border-b border-zinc-100 dark:border-zinc-800 flex items-center gap-2">
        <Icon className="w-4 h-4 text-zinc-500 dark:text-zinc-400" />
        <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 flex-1">{section.title}</h3>
        {section.domain && (
          <span className="text-[0.625rem] px-2 py-0.5 rounded-full bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-400 font-medium">
            {section.domain}
          </span>
        )}
        {section.depth_level && (
          <span className="text-[0.625rem] text-zinc-400 dark:text-zinc-500">
            depth {section.depth_level}/5
          </span>
        )}
      </div>

      {/* Content */}
      <div
        className="px-5 py-4 prose-sm"
        dangerouslySetInnerHTML={{ __html: renderMarkdown(section.content) }}
      />

      {/* Feedback row */}
      <div className="px-5 py-2.5 border-t border-zinc-100 dark:border-zinc-800 flex items-center gap-1.5">
        {reactions.map(r => (
          <button
            key={r.key}
            onClick={() => handleReaction(r.key)}
            className={`flex items-center gap-1 px-2.5 py-1 rounded-full text-[0.6875rem] font-medium transition-all
              ${activeReaction === r.key
                ? 'bg-blue-600 text-white'
                : 'bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 hover:bg-zinc-200 dark:hover:bg-zinc-700'
              }`}
          >
            <span>{r.icon}</span>
            <span className="hidden sm:inline">{r.label}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

export default function LearningView() {
  const [digests, setDigests] = useState<DigestListItem[]>([])
  const [activeDigest, setActiveDigest] = useState<DigestDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [filter, setFilter] = useState<'all' | 'daily' | 'weekly'>('all')

  useEffect(() => {
    fetch('/api/learning/digests?limit=50')
      .then(res => res.json())
      .then(data => {
        setDigests(data)
        setLoading(false)
        if (data.length > 0) loadDigest(data[0].id)
      })
      .catch(() => setLoading(false))
  }, [])

  function loadDigest(id: number) {
    setLoadingDetail(true)
    fetch(`/api/learning/digests/${id}`)
      .then(res => res.json())
      .then(data => {
        // Ensure sections is parsed
        if (typeof data.sections === 'string') {
          try { data.sections = JSON.parse(data.sections) } catch { data.sections = [] }
        }
        setActiveDigest(data)
        setLoadingDetail(false)
      })
      .catch(() => setLoadingDetail(false))
  }

  const handleFeedback = useCallback((sectionId: string, reaction: string) => {
    if (!activeDigest) return
    fetch('/api/learning/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        digest_id: activeDigest.id,
        section_id: sectionId,
        reaction,
      }),
    }).then(res => {
      if (res.ok) {
        res.json().then(fb => {
          setActiveDigest(prev => {
            if (!prev) return prev
            const filtered = prev.feedback.filter(f => f.section_id !== sectionId)
            return { ...prev, feedback: [...filtered, fb] }
          })
        })
      }
    })
  }, [activeDigest])

  const filtered = filter === 'all' ? digests : digests.filter(d => d.digest_type === filter)

  if (loading) {
    return (
      <main className="flex-1 flex items-center justify-center min-h-screen">
        <p className="text-sm text-zinc-500 dark:text-zinc-400">Loading...</p>
      </main>
    )
  }

  if (digests.length === 0) {
    return (
      <main className="flex-1 min-h-screen p-6 lg:p-10">
        <div className="max-w-3xl mx-auto">
          <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-2 mb-6">
            <GraduationCap className="w-5 h-5" /> Learning
          </h1>
          <div className="text-center py-16">
            <GraduationCap className="w-10 h-10 text-zinc-300 dark:text-zinc-600 mx-auto mb-3" />
            <p className="text-sm text-zinc-500 dark:text-zinc-400">No digests yet.</p>
            <p className="text-xs text-zinc-400 dark:text-zinc-500 mt-1">Your first morning digest arrives at 8am.</p>
          </div>
        </div>
      </main>
    )
  }

  return (
    <main className="flex-1 min-h-screen flex flex-col lg:flex-row">
      {/* Left sidebar: archive list */}
      <div className="w-full lg:w-64 shrink-0 border-r border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-950 overflow-hidden flex flex-col">
        <div className="p-3 border-b border-zinc-200 dark:border-zinc-700">
          <div className="flex items-center gap-2 mb-2">
            <GraduationCap className="w-4 h-4 text-zinc-600 dark:text-zinc-400" />
            <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Learning</h2>
            <span className="text-[0.6875rem] text-zinc-500 dark:text-zinc-400 ml-auto">{digests.length}</span>
          </div>
          <div className="flex gap-1">
            {(['all', 'daily', 'weekly'] as const).map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`px-2.5 py-1 rounded-md text-[0.6875rem] font-medium transition-colors
                  ${filter === f
                    ? 'bg-blue-600/15 text-blue-600 dark:text-blue-400'
                    : 'text-zinc-500 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800'
                  }`}
              >
                {f === 'all' ? 'All' : f.charAt(0).toUpperCase() + f.slice(1)}
              </button>
            ))}
          </div>
        </div>
        <nav className="flex-1 overflow-y-auto p-2">
          {filtered.map(d => (
            <button
              key={d.id}
              onClick={() => loadDigest(d.id)}
              className={`flex items-start gap-2 w-full text-left px-3 py-2 rounded-md text-[0.8125rem] transition-all mb-0.5
                ${activeDigest?.id === d.id
                  ? 'bg-blue-600/15 text-blue-600 dark:text-blue-400'
                  : 'text-zinc-600 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800'
                }`}
            >
              <div className="flex-1 min-w-0">
                <p className="font-medium truncate">{d.title}</p>
                <div className="flex items-center gap-2 mt-0.5">
                  <span className={`text-[0.625rem] px-1.5 py-0.5 rounded font-medium
                    ${d.digest_type === 'daily'
                      ? 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400'
                      : 'bg-purple-50 dark:bg-purple-900/20 text-purple-700 dark:text-purple-400'
                    }`}
                  >
                    {d.digest_type}
                  </span>
                  <span className="text-[0.625rem] text-zinc-400 dark:text-zinc-500">{timeAgo(d.created_at)}</span>
                  {d.feedback_count > 0 && (
                    <span className="text-[0.625rem] text-zinc-400 dark:text-zinc-500">{d.feedback_count} fb</span>
                  )}
                </div>
              </div>
              <ChevronRight className="w-3.5 h-3.5 shrink-0 mt-1 text-zinc-400 dark:text-zinc-500" />
            </button>
          ))}
        </nav>
      </div>

      {/* Main area: digest content */}
      <div className="flex-1 min-w-0 overflow-y-auto">
        {loadingDetail ? (
          <div className="flex items-center justify-center py-20">
            <p className="text-sm text-zinc-500 dark:text-zinc-400">Loading...</p>
          </div>
        ) : activeDigest ? (
          <div className="max-w-3xl mx-auto px-6 py-8 lg:px-10">
            <div className="mb-6">
              <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">{activeDigest.title}</h1>
              <div className="flex items-center gap-3 mt-1">
                <span className="text-sm text-zinc-500 dark:text-zinc-400">
                  <Calendar className="w-3.5 h-3.5 inline -mt-0.5 mr-1" />
                  {new Date(activeDigest.digest_date).toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })}
                </span>
                <span className="text-sm text-zinc-500 dark:text-zinc-400">
                  {activeDigest.sections.length} sections
                </span>
              </div>
            </div>

            <div className="space-y-4">
              {activeDigest.sections.map(section => (
                <SectionCard
                  key={section.id}
                  section={section}
                  existingFeedback={activeDigest.feedback.find(f => f.section_id === section.id)}
                  onFeedback={handleFeedback}
                />
              ))}
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <GraduationCap className="w-10 h-10 text-zinc-300 dark:text-zinc-600 mb-3" />
            <p className="text-sm text-zinc-500 dark:text-zinc-400">Select a digest to start reading.</p>
          </div>
        )}
      </div>
    </main>
  )
}

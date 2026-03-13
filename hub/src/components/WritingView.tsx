import { useState, useEffect, useCallback, useRef } from 'react'
import {
  PenTool, ChevronRight, ChevronDown, MessageSquare, BookOpen,
  Users, Link2, X, Send, FileText, AlertTriangle, Lightbulb,
  HelpCircle, Edit3, StickyNote, Eye, Tag,
} from 'lucide-react'

// ── Types ──────────────────────────────────────────────────

interface Draft {
  id: number
  project_id: number | null
  parent_id: number | null
  title: string
  draft_type: string
  sort_order: number
  status: string
  current_version: number
  pov_character: string | null
  synopsis: string | null
  tags: string | null
  word_count: number
  characters: string | null
  open_feedback: number
  project_name: string | null
  created_at: string
  updated_at: string
}

interface DraftVersion {
  id: number
  version_number: number
  content: string
  word_count: number
  change_summary: string | null
  created_at: string
}

interface Feedback {
  id: number
  draft_id: number
  version_number: number | null
  feedback_type: string
  author: string
  highlighted_text: string | null
  content: string
  status: string
  resolution: string | null
  created_at: string
  resolved_at: string | null
}

interface Character {
  id: number
  character_name: string
  role: string
  notes: string | null
}

interface LoreLink {
  id: number
  context_id: number
  link_type: string
  note: string | null
  context_title: string
  context_type: string
}

interface DraftDetail extends Omit<Draft, "characters"> {
  content: DraftVersion | null
  versions: { id: number; version_number: number; word_count: number; change_summary: string | null; created_at: string }[]
  feedback: Feedback[]
  characters: Character[] | string | null
  lore_links: LoreLink[]
}

// ── Helpers ────────────────────────────────────────────────

function wordCount(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
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

const statusColors: Record<string, string> = {
  outline: 'bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400',
  draft: 'bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-400',
  revision: 'bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400',
  review: 'bg-purple-50 dark:bg-purple-900/20 text-purple-700 dark:text-purple-400',
  final: 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400',
  archived: 'bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-500',
}

const feedbackIcons: Record<string, typeof StickyNote> = {
  note: StickyNote,
  revision: Edit3,
  critique: AlertTriangle,
  suggestion: Lightbulb,
  question: HelpCircle,
}

const feedbackColors: Record<string, string> = {
  note: 'text-zinc-500 dark:text-zinc-400',
  revision: 'text-amber-600 dark:text-amber-400',
  critique: 'text-red-500 dark:text-red-400',
  suggestion: 'text-blue-500 dark:text-blue-400',
  question: 'text-purple-500 dark:text-purple-400',
}

const linkTypeColors: Record<string, string> = {
  references: 'bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400',
  establishes: 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400',
  contradicts: 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400',
  extends: 'bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-400',
}

// ── Annotation Popover ─────────────────────────────────────

function AnnotationPopover({
  selectedText,
  position,
  draftId,
  onSubmit,
  onClose,
}: {
  selectedText: string
  position: { x: number; y: number }
  draftId: number
  onSubmit: (fb: Feedback) => void
  onClose: () => void
}) {
  const [note, setNote] = useState('')
  const [type, setType] = useState<string>('note')
  const [submitting, setSubmitting] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    textareaRef.current?.focus()
  }, [])

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [onClose])

  async function handleSubmit() {
    if (!note.trim()) return
    setSubmitting(true)
    try {
      const res = await fetch('/api/writing/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          draft_id: draftId,
          highlighted_text: selectedText,
          content: note.trim(),
          feedback_type: type,
          author: 'user',
        }),
      })
      if (res.ok) {
        const fb = await res.json()
        onSubmit(fb)
        onClose()
      }
    } finally {
      setSubmitting(false)
    }
  }

  // Position the popover — clamp to viewport
  const style: React.CSSProperties = {
    position: 'fixed',
    left: Math.min(position.x, window.innerWidth - 340),
    top: Math.min(position.y + 8, window.innerHeight - 260),
    zIndex: 50,
  }

  const types = ['note', 'revision', 'critique', 'suggestion', 'question']

  return (
    <div ref={ref} style={style} className="w-80 bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl shadow-xl p-3">
      <div className="flex items-start gap-2 mb-2">
        <div className="flex-1 min-w-0">
          <p className="text-[0.6875rem] text-zinc-500 dark:text-zinc-400 line-clamp-2 italic">"{selectedText.slice(0, 120)}{selectedText.length > 120 ? '...' : ''}"</p>
        </div>
        <button onClick={onClose} className="p-0.5 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300">
          <X className="w-3.5 h-3.5" />
        </button>
      </div>

      <div className="flex gap-1 mb-2">
        {types.map(t => {
          const Icon = feedbackIcons[t]
          return (
            <button
              key={t}
              onClick={() => setType(t)}
              className={`flex items-center gap-1 px-2 py-1 rounded-md text-[0.625rem] font-medium transition-colors
                ${type === t
                  ? 'bg-zinc-900 dark:bg-zinc-100 text-white dark:text-zinc-900'
                  : 'bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 hover:bg-zinc-200 dark:hover:bg-zinc-700'
                }`}
              title={t}
            >
              <Icon className="w-3 h-3" />
              <span className="hidden sm:inline">{t}</span>
            </button>
          )
        })}
      </div>

      <textarea
        ref={textareaRef}
        value={note}
        onChange={e => setNote(e.target.value)}
        placeholder="Add your note..."
        rows={3}
        className="w-full px-2.5 py-2 text-sm bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 rounded-lg text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 dark:placeholder-zinc-500 outline-none focus:border-blue-400 dark:focus:border-blue-500 resize-none"
        onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSubmit() }}
      />

      <div className="flex items-center justify-between mt-2">
        <span className="text-[0.625rem] text-zinc-400 dark:text-zinc-500">{navigator.platform.includes('Mac') ? '\u2318' : 'Ctrl'}+Enter to submit</span>
        <button
          onClick={handleSubmit}
          disabled={!note.trim() || submitting}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          <Send className="w-3 h-3" />
          {submitting ? 'Saving...' : 'Add Note'}
        </button>
      </div>
    </div>
  )
}

// ── Outline Tree ───────────────────────────────────────────

function OutlineItem({
  draft,
  children,
  activeDraftId,
  onSelect,
  depth = 0,
}: {
  draft: Draft
  children: Draft[]
  activeDraftId: number | null
  onSelect: (id: number) => void
  depth?: number
}) {
  const [expanded, setExpanded] = useState(true)
  const hasChildren = children.length > 0
  const isActive = activeDraftId === draft.id
  const isChapter = draft.draft_type === 'chapter'

  return (
    <div>
      <button
        onClick={() => {
          if (hasChildren && isChapter) setExpanded(!expanded)
          else onSelect(draft.id)
        }}
        className={`flex items-center gap-1.5 w-full text-left px-2 py-1.5 rounded-md text-[0.8125rem] transition-all
          ${isActive
            ? 'bg-blue-600/15 text-blue-600 dark:text-blue-400 font-semibold'
            : 'text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800'
          }`}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        {hasChildren ? (
          expanded ? <ChevronDown className="w-3.5 h-3.5 shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 shrink-0" />
        ) : (
          <FileText className="w-3.5 h-3.5 shrink-0 text-zinc-400 dark:text-zinc-500" />
        )}
        <span className="truncate flex-1">{draft.title}</span>
        {draft.word_count > 0 && !isChapter && (
          <span className="text-[0.625rem] text-zinc-400 dark:text-zinc-500 shrink-0">{wordCount(draft.word_count)}</span>
        )}
        {draft.open_feedback > 0 && (
          <span className="text-[0.625rem] px-1.5 rounded-full bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 shrink-0">
            {draft.open_feedback}
          </span>
        )}
      </button>
      {expanded && hasChildren && (
        <div>
          {children.map(child => (
            <OutlineItem
              key={child.id}
              draft={child}
              children={[]}
              activeDraftId={activeDraftId}
              onSelect={onSelect}
              depth={depth + 1}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// ── Prose Reader with Selection ────────────────────────────

function ProseReader({
  content,
  feedback,
  draftId,
  onNewFeedback,
}: {
  content: string
  feedback: Feedback[]
  draftId: number
  onNewFeedback: (fb: Feedback) => void
}) {
  const [popover, setPopover] = useState<{ text: string; x: number; y: number } | null>(null)
  const proseRef = useRef<HTMLDivElement>(null)

  const handleMouseUp = useCallback(() => {
    const sel = window.getSelection()
    if (!sel || sel.isCollapsed || !sel.toString().trim()) {
      return
    }
    const text = sel.toString().trim()
    if (text.length < 3) return

    const range = sel.getRangeAt(0)
    const rect = range.getBoundingClientRect()
    setPopover({
      text,
      x: rect.left + rect.width / 2 - 160,
      y: rect.bottom,
    })
  }, [])

  // Highlight passages that have feedback
  const highlightedTexts = feedback
    .filter(f => f.highlighted_text && f.status === 'open')
    .map(f => f.highlighted_text!)

  // Render prose with highlighted passages marked
  function renderProse(text: string): React.ReactNode[] {
    if (highlightedTexts.length === 0) {
      return text.split('\n\n').map((para, i) => {
        if (para.trim().match(/^[•·\s]+$/)) {
          return <div key={i} className="text-center text-zinc-400 dark:text-zinc-500 py-2 text-sm tracking-widest">* * *</div>
        }
        return <p key={i} className="mb-4 leading-relaxed">{para.trim()}</p>
      })
    }

    // Simple approach: render paragraphs, highlight matching text spans
    return text.split('\n\n').map((para, i) => {
      if (para.trim().match(/^[•·\s]+$/)) {
        return <div key={i} className="text-center text-zinc-400 dark:text-zinc-500 py-2 text-sm tracking-widest">* * *</div>
      }

      let html = para.trim()
      for (const ht of highlightedTexts) {
        const escaped = ht.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
        html = html.replace(new RegExp(`(${escaped})`, 'g'),
          '<mark class="bg-amber-100/70 dark:bg-amber-800/30 rounded px-0.5">$1</mark>')
      }

      return <p key={i} className="mb-4 leading-relaxed" dangerouslySetInnerHTML={{ __html: html }} />
    })
  }

  return (
    <div className="relative">
      <div
        ref={proseRef}
        onMouseUp={handleMouseUp}
        className="prose-reader text-[0.9375rem] text-zinc-800 dark:text-zinc-200 font-serif leading-relaxed selection:bg-blue-200 dark:selection:bg-blue-800/50 cursor-text"
      >
        {renderProse(content)}
      </div>

      {popover && (
        <AnnotationPopover
          selectedText={popover.text}
          position={{ x: popover.x, y: popover.y }}
          draftId={draftId}
          onSubmit={onNewFeedback}
          onClose={() => { setPopover(null); window.getSelection()?.removeAllRanges() }}
        />
      )}
    </div>
  )
}

// ── Feedback Panel ─────────────────────────────────────────

function FeedbackPanel({ items }: { items: Feedback[] }) {
  if (items.length === 0) return null

  const open = items.filter(f => f.status === 'open')
  const resolved = items.filter(f => f.status !== 'open')

  return (
    <div className="space-y-2">
      {open.map(f => {
        const Icon = feedbackIcons[f.feedback_type] || StickyNote
        return (
          <div key={f.id} className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-lg px-3 py-2.5">
            <div className="flex items-center gap-1.5 mb-1">
              <Icon className={`w-3.5 h-3.5 ${feedbackColors[f.feedback_type]}`} />
              <span className="text-[0.6875rem] font-medium text-zinc-700 dark:text-zinc-300 capitalize">{f.feedback_type}</span>
              <span className="text-[0.625rem] text-zinc-400 dark:text-zinc-500 ml-auto">{timeAgo(f.created_at)}</span>
            </div>
            {f.highlighted_text && (
              <p className="text-[0.6875rem] text-zinc-500 dark:text-zinc-400 italic line-clamp-2 mb-1 pl-5">"{f.highlighted_text.slice(0, 100)}{f.highlighted_text.length > 100 ? '...' : ''}"</p>
            )}
            <p className="text-[0.8125rem] text-zinc-800 dark:text-zinc-200 pl-5">{f.content}</p>
          </div>
        )
      })}
      {resolved.length > 0 && (
        <details className="mt-3">
          <summary className="text-[0.6875rem] text-zinc-500 dark:text-zinc-400 cursor-pointer hover:text-zinc-700 dark:hover:text-zinc-300">
            {resolved.length} resolved
          </summary>
          <div className="space-y-1.5 mt-1.5 opacity-60">
            {resolved.map(f => (
              <div key={f.id} className="text-[0.75rem] text-zinc-500 dark:text-zinc-400 pl-5 py-1">
                <span className="capitalize font-medium">{f.feedback_type}:</span> {f.content.slice(0, 80)}
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}

// ── Main Writing View ──────────────────────────────────────

export default function WritingView() {
  const [drafts, setDrafts] = useState<Draft[]>([])
  const [activeDraft, setActiveDraft] = useState<DraftDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingDraft, setLoadingDraft] = useState(false)
  const [outlineOpen, setOutlineOpen] = useState(true)
  const [panelTab, setPanelTab] = useState<'feedback' | 'lore' | 'characters'>('feedback')

  // Load all drafts (for now, project 209 — Braska's Pilgrimage)
  useEffect(() => {
    fetch('/api/writing/drafts?project_id=209')
      .then(res => res.json())
      .then(data => {
        setDrafts(data)
        setLoading(false)
        // Auto-select first scene
        const firstScene = data.find((d: Draft) => d.parent_id && d.current_version > 0)
        if (firstScene) loadDraft(firstScene.id)
      })
      .catch(() => setLoading(false))
  }, [])

  function loadDraft(id: number) {
    setLoadingDraft(true)
    fetch(`/api/writing/drafts/${id}`)
      .then(res => res.json())
      .then(data => { setActiveDraft(data); setLoadingDraft(false) })
      .catch(() => setLoadingDraft(false))
  }

  function handleNewFeedback(fb: Feedback) {
    if (activeDraft) {
      setActiveDraft({
        ...activeDraft,
        feedback: [fb, ...activeDraft.feedback],
      })
      // Update open_feedback count in outline
      setDrafts(prev => prev.map(d =>
        d.id === activeDraft.id ? { ...d, open_feedback: d.open_feedback + 1 } : d
      ))
    }
  }

  // Build tree structure
  const chapters = drafts.filter(d => !d.parent_id)
  const getChildren = (parentId: number) => drafts.filter(d => d.parent_id === parentId)

  // Stats
  const totalWords = drafts.reduce((sum, d) => d.parent_id ? sum + d.word_count : sum, 0)
  const totalScenes = drafts.filter(d => d.parent_id).length
  const totalFeedback = drafts.reduce((sum, d) => sum + d.open_feedback, 0)

  if (loading) {
    return (
      <main className="flex-1 flex items-center justify-center min-h-screen">
        <p className="text-sm text-zinc-500 dark:text-zinc-400">Loading writing environment...</p>
      </main>
    )
  }

  const chars = activeDraft?.characters
  const characterList: Character[] = Array.isArray(chars) ? chars : []
  const loreLinks: LoreLink[] = activeDraft?.lore_links || []
  const feedback: Feedback[] = activeDraft?.feedback || []

  return (
    <main className="flex-1 min-h-screen flex flex-col lg:flex-row">
      {/* ── Outline Panel ── */}
      <div className={`${outlineOpen ? 'w-full lg:w-64' : 'w-0'} shrink-0 border-r border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-950 overflow-hidden transition-all`}>
        <div className="p-3 border-b border-zinc-200 dark:border-zinc-700">
          <div className="flex items-center gap-2">
            <PenTool className="w-4 h-4 text-zinc-600 dark:text-zinc-400" />
            <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Braska's Pilgrimage</h2>
          </div>
          <div className="flex gap-3 mt-1.5 text-[0.6875rem] text-zinc-500 dark:text-zinc-400">
            <span>{totalScenes} scenes</span>
            <span>{wordCount(totalWords)} words</span>
            {totalFeedback > 0 && <span className="text-amber-600 dark:text-amber-400">{totalFeedback} notes</span>}
          </div>
        </div>
        <nav className="p-2 overflow-y-auto" style={{ maxHeight: 'calc(100vh - 80px)' }}>
          {chapters.map(ch => (
            <OutlineItem
              key={ch.id}
              draft={ch}
              children={getChildren(ch.id)}
              activeDraftId={activeDraft?.id ?? null}
              onSelect={loadDraft}
            />
          ))}
        </nav>
      </div>

      {/* ── Main Reading Area ── */}
      <div className="flex-1 min-w-0 flex flex-col">
        {/* Toolbar */}
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 shrink-0">
          <button
            onClick={() => setOutlineOpen(!outlineOpen)}
            className="p-1.5 rounded-md text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 lg:hidden"
          >
            <BookOpen className="w-4 h-4" />
          </button>

          {activeDraft && (
            <>
              <h1 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 truncate">
                {activeDraft.title}
              </h1>
              {activeDraft.pov_character && (
                <span className="text-[0.6875rem] px-2 py-0.5 rounded-full bg-indigo-50 dark:bg-indigo-900/20 text-indigo-700 dark:text-indigo-400 shrink-0">
                  <Eye className="w-3 h-3 inline -mt-0.5 mr-0.5" />
                  {activeDraft.pov_character}
                </span>
              )}
              <span className={`text-[0.625rem] px-2 py-0.5 rounded-full font-medium shrink-0 ${statusColors[activeDraft.status]}`}>
                {activeDraft.status}
              </span>
              <span className="text-[0.6875rem] text-zinc-400 dark:text-zinc-500 ml-auto shrink-0">
                {wordCount(activeDraft.word_count)} words
                {activeDraft.current_version > 0 && ` · v${activeDraft.current_version}`}
              </span>
            </>
          )}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto">
          {loadingDraft ? (
            <div className="flex items-center justify-center py-20">
              <p className="text-sm text-zinc-500 dark:text-zinc-400">Loading...</p>
            </div>
          ) : activeDraft?.content ? (
            <div className="max-w-2xl mx-auto px-6 py-8 lg:px-10">
              {activeDraft.synopsis && (
                <p className="text-[0.8125rem] text-zinc-500 dark:text-zinc-400 italic mb-6 pb-4 border-b border-zinc-100 dark:border-zinc-800">
                  {activeDraft.synopsis}
                </p>
              )}
              <ProseReader
                content={activeDraft.content.content}
                feedback={feedback}
                draftId={activeDraft.id}
                onNewFeedback={handleNewFeedback}
              />
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <PenTool className="w-10 h-10 text-zinc-300 dark:text-zinc-600 mb-3" />
              <p className="text-sm text-zinc-500 dark:text-zinc-400">
                {drafts.length === 0 ? 'No drafts yet.' : 'Select a scene from the outline to start reading.'}
              </p>
              <p className="text-xs text-zinc-400 dark:text-zinc-500 mt-1">
                Highlight any text to add a note.
              </p>
            </div>
          )}
        </div>
      </div>

      {/* ── Right Panel: Feedback / Lore / Characters ── */}
      {activeDraft && (
        <div className="w-full lg:w-72 shrink-0 border-l border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-950 flex flex-col overflow-hidden">
          {/* Tab bar */}
          <div className="flex border-b border-zinc-200 dark:border-zinc-700 shrink-0">
            {[
              { key: 'feedback' as const, icon: MessageSquare, label: 'Notes', count: feedback.filter(f => f.status === 'open').length },
              { key: 'lore' as const, icon: Link2, label: 'Lore', count: loreLinks.length },
              { key: 'characters' as const, icon: Users, label: 'Cast', count: characterList.length },
            ].map(tab => (
              <button
                key={tab.key}
                onClick={() => setPanelTab(tab.key)}
                className={`flex-1 flex items-center justify-center gap-1.5 px-2 py-2 text-[0.6875rem] font-medium transition-colors
                  ${panelTab === tab.key
                    ? 'text-blue-600 dark:text-blue-400 border-b-2 border-blue-600 dark:border-blue-400 -mb-px'
                    : 'text-zinc-500 dark:text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-300'
                  }`}
              >
                <tab.icon className="w-3.5 h-3.5" />
                {tab.label}
                {tab.count > 0 && (
                  <span className="text-[0.5625rem] px-1 rounded-full bg-zinc-200 dark:bg-zinc-700">{tab.count}</span>
                )}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <div className="flex-1 overflow-y-auto p-3">
            {panelTab === 'feedback' && (
              feedback.length === 0 ? (
                <div className="text-center py-8">
                  <MessageSquare className="w-8 h-8 text-zinc-300 dark:text-zinc-600 mx-auto mb-2" />
                  <p className="text-xs text-zinc-500 dark:text-zinc-400">No notes yet.</p>
                  <p className="text-[0.625rem] text-zinc-400 dark:text-zinc-500 mt-1">Highlight text to annotate.</p>
                </div>
              ) : (
                <FeedbackPanel items={feedback} />
              )
            )}

            {panelTab === 'lore' && (
              loreLinks.length === 0 ? (
                <div className="text-center py-8">
                  <Link2 className="w-8 h-8 text-zinc-300 dark:text-zinc-600 mx-auto mb-2" />
                  <p className="text-xs text-zinc-500 dark:text-zinc-400">No lore links.</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {loreLinks.map(l => (
                    <div key={l.id} className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-lg px-3 py-2">
                      <div className="flex items-center gap-1.5">
                        <span className={`text-[0.5625rem] px-1.5 py-0.5 rounded font-medium ${linkTypeColors[l.link_type]}`}>
                          {l.link_type}
                        </span>
                        <Tag className="w-3 h-3 text-zinc-400 dark:text-zinc-500" />
                        <span className="text-[0.625rem] text-zinc-400 dark:text-zinc-500 capitalize">{l.context_type}</span>
                      </div>
                      <p className="text-[0.8125rem] font-medium text-zinc-800 dark:text-zinc-200 mt-1">{l.context_title}</p>
                      {l.note && <p className="text-[0.6875rem] text-zinc-500 dark:text-zinc-400 mt-0.5">{l.note}</p>}
                    </div>
                  ))}
                </div>
              )
            )}

            {panelTab === 'characters' && (
              characterList.length === 0 ? (
                <div className="text-center py-8">
                  <Users className="w-8 h-8 text-zinc-300 dark:text-zinc-600 mx-auto mb-2" />
                  <p className="text-xs text-zinc-500 dark:text-zinc-400">No characters tagged.</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {characterList.map(c => (
                    <div key={c.id} className="flex items-center gap-2 bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-lg px-3 py-2">
                      <div className={`w-7 h-7 rounded-full flex items-center justify-center text-[0.6875rem] font-semibold shrink-0
                        ${c.role === 'pov' ? 'bg-indigo-100 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-400'
                          : c.role === 'featured' ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400'
                          : 'bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400'
                        }`}>
                        {c.character_name[0]}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-[0.8125rem] font-medium text-zinc-800 dark:text-zinc-200">{c.character_name}</p>
                        <p className="text-[0.625rem] text-zinc-500 dark:text-zinc-400 capitalize">{c.role}</p>
                      </div>
                      {c.notes && <p className="text-[0.625rem] text-zinc-400 dark:text-zinc-500 max-w-[120px] truncate">{c.notes}</p>}
                    </div>
                  ))}
                </div>
              )
            )}
          </div>
        </div>
      )}
    </main>
  )
}

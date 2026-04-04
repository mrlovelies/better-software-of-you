import { useState, useEffect, useCallback, useRef } from 'react'
import {
  PenTool, ChevronRight, ChevronDown, MessageSquare, BookOpen,
  Users, Link2, X, Send, FileText, AlertTriangle, Lightbulb,
  HelpCircle, Edit3, StickyNote, Eye, Tag, Bold, Italic, Minus,
  Save, Check, Trash2, Menu, AlignLeft, Hash, Underline,
  Heading, Undo2, Redo2, Plus, Search, Unlink, GitCompare,
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

interface WritingProject {
  id: number
  name: string
  description: string | null
  status: string
  draft_count: number
  total_words: number
  open_feedback: number
  last_writing_activity: string | null
}

interface LoreEntry {
  id: number
  project_id: number | null
  context_type: string
  title: string
  status: string
  tags: string | null
  content_preview: string
  created_at: string
  updated_at: string
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

// Left border colors for feedback items (#6 right panel polish)
const feedbackBorderColors: Record<string, string> = {
  note: 'border-l-zinc-300 dark:border-l-zinc-600',
  revision: 'border-l-amber-400 dark:border-l-amber-500',
  critique: 'border-l-red-400 dark:border-l-red-500',
  suggestion: 'border-l-blue-400 dark:border-l-blue-500',
  question: 'border-l-purple-400 dark:border-l-purple-500',
}

const linkTypeColors: Record<string, string> = {
  references: 'bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400',
  establishes: 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400',
  contradicts: 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400',
  extends: 'bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-400',
}

// Focus mode states: off -> focus -> typewriter -> off
type FocusState = 'off' | 'focus' | 'typewriter'
function nextFocusState(current: FocusState): FocusState {
  if (current === 'off') return 'focus'
  if (current === 'focus') return 'typewriter'
  return 'off'
}

// ── Word-level Diff Algorithm ─────────────────────────────

interface DiffSegment {
  type: 'equal' | 'add' | 'remove'
  text: string
}

function computeWordDiff(oldText: string, newText: string): DiffSegment[] {
  // Strip HTML tags for comparison
  const strip = (s: string) => s.replace(/<[^>]*>/g, ' ').replace(/&nbsp;/g, ' ')
  const oldWords = strip(oldText).split(/\s+/).filter(Boolean)
  const newWords = strip(newText).split(/\s+/).filter(Boolean)

  // Simple LCS-based diff (O(mn) but fine for typical draft sizes)
  const m = oldWords.length
  const n = newWords.length

  // For very large texts, fall back to paragraph-level diff
  if (m * n > 2_000_000) {
    return computeParagraphDiff(oldText, newText)
  }

  // Build LCS table
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0))
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (oldWords[i - 1] === newWords[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1])
      }
    }
  }

  // Backtrack to build diff
  const segments: DiffSegment[] = []
  let i = m, j = n
  const raw: { type: DiffSegment['type']; word: string }[] = []

  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && oldWords[i - 1] === newWords[j - 1]) {
      raw.push({ type: 'equal', word: oldWords[i - 1] })
      i--; j--
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      raw.push({ type: 'add', word: newWords[j - 1] })
      j--
    } else {
      raw.push({ type: 'remove', word: oldWords[i - 1] })
      i--
    }
  }

  raw.reverse()

  // Merge consecutive same-type segments
  for (const r of raw) {
    if (segments.length > 0 && segments[segments.length - 1].type === r.type) {
      segments[segments.length - 1].text += ' ' + r.word
    } else {
      segments.push({ type: r.type, text: r.word })
    }
  }

  return segments
}

function computeParagraphDiff(oldText: string, newText: string): DiffSegment[] {
  const strip = (s: string) => s.replace(/<[^>]*>/g, '').trim()
  const oldParas = oldText.split(/\n\n|<\/p>\s*<p[^>]*>/i).map(strip).filter(Boolean)
  const newParas = newText.split(/\n\n|<\/p>\s*<p[^>]*>/i).map(strip).filter(Boolean)
  const segments: DiffSegment[] = []
  const maxLen = Math.max(oldParas.length, newParas.length)
  for (let i = 0; i < maxLen; i++) {
    const op = oldParas[i] || ''
    const np = newParas[i] || ''
    if (op === np) {
      segments.push({ type: 'equal', text: op })
    } else {
      if (op) segments.push({ type: 'remove', text: op })
      if (np) segments.push({ type: 'add', text: np })
    }
  }
  return segments
}

// ── useScrollDirection hook (for mobile bottom bar) ────────

function useScrollDirection() {
  const [visible, setVisible] = useState(true)
  const lastScrollY = useRef(0)

  useEffect(() => {
    function handleScroll() {
      const currentY = window.scrollY
      if (currentY < 10) {
        setVisible(true)
      } else if (currentY > lastScrollY.current + 5) {
        setVisible(false) // scrolling down
      } else if (currentY < lastScrollY.current - 5) {
        setVisible(true) // scrolling up
      }
      lastScrollY.current = currentY
    }
    window.addEventListener('scroll', handleScroll, { passive: true })
    return () => window.removeEventListener('scroll', handleScroll)
  }, [])

  return visible
}

// ── Text <-> HTML conversion ───────────────────────────────

function plainTextToHtml(text: string): string {
  // If it already contains HTML tags, return as-is
  if (/<\w+[^>]*>/.test(text)) return text
  return text
    .split('\n\n')
    .map(para => {
      const trimmed = para.trim()
      if (!trimmed) return ''
      if (/^[•·\s*]+$/.test(trimmed) || trimmed === '* * *') {
        return '<hr />'
      }
      return `<p>${trimmed.replace(/\n/g, '<br />')}</p>`
    })
    .filter(Boolean)
    .join('\n')
}

// ── Floating Format Toolbar ────────────────────────────────

function FloatingToolbar({
  position,
  onBold,
  onItalic,
  onSectionBreak,
}: {
  position: { x: number; y: number }
  onBold: () => void
  onItalic: () => void
  onSectionBreak: () => void
}) {
  const style: React.CSSProperties = {
    position: 'fixed',
    left: Math.max(8, Math.min(position.x, window.innerWidth - 180)),
    top: Math.max(8, position.y - 44),
    zIndex: 50,
  }

  return (
    <div
      style={style}
      className="flex items-center gap-0.5 bg-zinc-900/90 dark:bg-zinc-100/90 backdrop-blur-sm rounded-xl px-1.5 py-1 shadow-xl animate-in"
    >
      <button
        onMouseDown={e => { e.preventDefault(); onBold() }}
        className="p-1.5 rounded text-zinc-200 dark:text-zinc-800 hover:bg-zinc-700 dark:hover:bg-zinc-300 transition-colors"
        title="Bold"
      >
        <Bold className="w-4 h-4" />
      </button>
      <button
        onMouseDown={e => { e.preventDefault(); onItalic() }}
        className="p-1.5 rounded text-zinc-200 dark:text-zinc-800 hover:bg-zinc-700 dark:hover:bg-zinc-300 transition-colors"
        title="Italic"
      >
        <Italic className="w-4 h-4" />
      </button>
      <div className="w-px h-5 bg-zinc-700 dark:bg-zinc-300 mx-0.5" />
      <button
        onMouseDown={e => { e.preventDefault(); onSectionBreak() }}
        className="p-1.5 rounded text-zinc-200 dark:text-zinc-800 hover:bg-zinc-700 dark:hover:bg-zinc-300 transition-colors"
        title="Section Break"
      >
        <Minus className="w-4 h-4" />
      </button>
    </div>
  )
}

// ── Annotation Popover ─────────────────────────────────────

function AnnotationPopover({
  selectedText,
  position,
  draftId,
  onSubmit,
  onClose,
  existingFeedback,
  onUpdated,
}: {
  selectedText: string
  position: { x: number; y: number }
  draftId: number
  onSubmit: (fb: Feedback) => void
  onClose: () => void
  existingFeedback?: Feedback | null
  onUpdated?: (fb: Feedback) => void
}) {
  const isEditing = !!existingFeedback
  const [note, setNote] = useState(existingFeedback?.content || '')
  const [type, setType] = useState<string>(existingFeedback?.feedback_type || 'note')
  const [submitting, setSubmitting] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    textareaRef.current?.focus()
    // Place cursor at end when editing
    if (isEditing && textareaRef.current) {
      const len = textareaRef.current.value.length
      textareaRef.current.setSelectionRange(len, len)
    }
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
      if (isEditing && existingFeedback) {
        // PATCH existing feedback
        const res = await fetch(`/api/writing/feedback/${existingFeedback.id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            content: note.trim(),
            feedback_type: type,
          }),
        })
        if (res.ok) {
          const updated = await res.json()
          onUpdated?.(updated)
          onClose()
        }
      } else {
        // POST new feedback
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
      }
    } finally {
      setSubmitting(false)
    }
  }

  const style: React.CSSProperties = {
    position: 'fixed',
    left: Math.min(position.x, window.innerWidth - 340),
    top: Math.min(position.y + 8, window.innerHeight - 260),
    zIndex: 50,
  }

  const types = ['note', 'revision', 'critique', 'suggestion', 'question']

  return (
    <div ref={ref} style={style} className="w-80 bg-white dark:bg-[#222226] border border-zinc-200 dark:border-zinc-700 rounded-xl shadow-xl p-3">
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
          {submitting ? 'Saving...' : isEditing ? 'Update' : 'Add Note'}
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
  defaultExpanded = false,
}: {
  draft: Draft
  children: Draft[]
  activeDraftId: number | null
  onSelect: (id: number) => void
  depth?: number
  defaultExpanded?: boolean
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const hasChildren = children.length > 0
  const isActive = activeDraftId === draft.id
  const isChapter = draft.draft_type === 'chapter'
  const hasContent = draft.word_count > 0 || draft.current_version > 0

  // Auto-expand when a child becomes active
  useEffect(() => {
    if (hasChildren && children.some(c => c.id === activeDraftId)) {
      setExpanded(true)
    }
  }, [activeDraftId])

  return (
    <div>
      <button
        onClick={() => {
          if (hasChildren && isChapter) setExpanded(!expanded)
          else onSelect(draft.id)
        }}
        className={`flex items-center gap-1.5 w-full text-left px-2 py-1.5 rounded-md text-[0.8125rem] transition-all
          ${isActive
            ? 'border-l-2 border-l-blue-500 dark:border-l-blue-400 bg-transparent text-blue-600 dark:text-blue-400 font-semibold'
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
        {/* Content indicator dot (#8) */}
        {!isChapter && (
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${hasContent ? 'bg-emerald-400 dark:bg-emerald-500' : 'bg-zinc-300 dark:bg-zinc-600'}`} />
        )}
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

// ── Prose Reader with Selection (Read Mode) ────────────────

function ProseReader({
  content,
  feedback,
  draftId,
  onNewFeedback,
  onUpdateFeedback,
  focusState,
}: {
  content: string
  feedback: Feedback[]
  draftId: number
  onNewFeedback: (fb: Feedback) => void
  onUpdateFeedback: (fb: Feedback) => void
  focusState: FocusState
}) {
  const [popover, setPopover] = useState<{ text: string; x: number; y: number; editFeedback?: Feedback } | null>(null)
  const proseRef = useRef<HTMLDivElement>(null)
  const [activeParagraph, setActiveParagraph] = useState<number>(0)

  const handleMouseUp = useCallback((e: React.MouseEvent) => {
    // Check if user clicked on a highlight mark
    const target = e.target as HTMLElement
    if (target.tagName === 'MARK' && target.dataset.highlight) {
      const highlightPrefix = target.dataset.highlight.replace(/&quot;/g, '"')
      const matchingFeedback = feedback.find(f =>
        f.highlighted_text && f.status === 'open' && f.highlighted_text.slice(0, 40) === highlightPrefix
      )
      if (matchingFeedback) {
        const rect = target.getBoundingClientRect()
        setPopover({
          text: matchingFeedback.highlighted_text!,
          x: rect.left + rect.width / 2 - 160,
          y: rect.bottom,
          editFeedback: matchingFeedback,
        })
        return
      }
    }

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
  }, [feedback])

  // IntersectionObserver for focus mode (#4)
  useEffect(() => {
    if (focusState === 'off' || !proseRef.current) return

    const paragraphs = proseRef.current.querySelectorAll('p, .section-break')
    if (paragraphs.length === 0) return

    const observer = new IntersectionObserver(
      (entries) => {
        // Find the entry closest to the viewport center
        let bestIdx = activeParagraph
        let bestDistance = Infinity
        entries.forEach(entry => {
          if (entry.isIntersecting) {
            const rect = entry.boundingClientRect
            const viewportCenter = window.innerHeight * 0.4
            const elemCenter = rect.top + rect.height / 2
            const distance = Math.abs(elemCenter - viewportCenter)
            if (distance < bestDistance) {
              bestDistance = distance
              const idx = Array.from(paragraphs).indexOf(entry.target as Element)
              if (idx >= 0) bestIdx = idx
            }
          }
        })
        setActiveParagraph(bestIdx)
      },
      {
        rootMargin: '-20% 0px -20% 0px',
        threshold: [0, 0.25, 0.5, 0.75, 1.0],
      }
    )

    paragraphs.forEach(p => observer.observe(p))
    return () => observer.disconnect()
  }, [focusState, content])

  // Typewriter scroll (#5): keep active paragraph at 40% from top
  useEffect(() => {
    if (focusState !== 'typewriter' || !proseRef.current) return
    const paragraphs = proseRef.current.querySelectorAll('p, .section-break')
    const target = paragraphs[activeParagraph]
    if (!target) return

    const rect = target.getBoundingClientRect()
    const viewportTarget = window.innerHeight * 0.4
    const scrollBy = rect.top - viewportTarget + rect.height / 2
    if (Math.abs(scrollBy) > 20) {
      window.scrollBy({ top: scrollBy, behavior: 'smooth' })
    }
  }, [activeParagraph, focusState])

  // Build highlight fragments — split multi-paragraph highlights into per-paragraph pieces
  const highlightFragments: { fragment: string; dataAttr: string }[] = []
  feedback
    .filter(f => f.highlighted_text && f.status === 'open')
    .forEach(f => {
      const ht = f.highlighted_text!
      const dataAttr = ht.slice(0, 40).replace(/"/g, '&quot;')
      // Split on double-newlines (paragraph boundaries) and on single newlines
      const fragments = ht.split(/\n\n|\n/).map(s => s.trim()).filter(s => s.length > 0)
      for (const frag of fragments) {
        highlightFragments.push({ fragment: frag, dataAttr })
      }
    })

  function applyHighlights(html: string): string {
    for (const { fragment, dataAttr } of highlightFragments) {
      const escaped = fragment.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      html = html.replace(new RegExp(`(${escaped})`, 'g'),
        `<mark class="bg-amber-200 dark:bg-amber-500/40 rounded px-0.5 dark:text-amber-100 scroll-mt-32 transition-all duration-300" data-highlight="${dataAttr}">$1</mark>`)
    }
    return html
  }

  function renderProse(text: string): React.ReactNode[] {
    // Handle HTML content (from edit mode saves)
    if (/<\w+[^>]*>/.test(text)) {
      const div = document.createElement('div')
      div.innerHTML = text
      const nodes: React.ReactNode[] = []
      let paraIndex = 0
      div.childNodes.forEach((node, i) => {
        if (node.nodeType === Node.ELEMENT_NODE) {
          const el = node as HTMLElement
          if (el.tagName === 'HR') {
            nodes.push(<div key={i} className="section-break text-center text-zinc-400 dark:text-zinc-500 py-4 text-sm tracking-[0.3em]">&bull; &bull; &bull;</div>)
            paraIndex++
          } else {
            const html = applyHighlights(el.innerHTML)
            const isFocused = focusState === 'off' || paraIndex === activeParagraph
            nodes.push(
              <p
                key={i}
                className={`mb-5 leading-[1.58] transition-opacity duration-200 ${isFocused ? 'opacity-100' : 'opacity-[0.3]'}`}
                dangerouslySetInnerHTML={{ __html: html }}
              />
            )
            paraIndex++
          }
        }
      })
      return nodes
    }

    // Handle plain text content
    let paraIndex = 0
    return text.split('\n\n').map((para, i) => {
      if (para.trim().match(/^[•·\s]+$/)) {
        const idx = paraIndex++
        const isFocused = focusState === 'off' || idx === activeParagraph
        return <div key={i} className={`section-break text-center text-zinc-400 dark:text-zinc-500 py-4 text-sm tracking-[0.3em] transition-opacity duration-200 ${isFocused ? 'opacity-100' : 'opacity-[0.3]'}`}>* * *</div>
      }

      const html = applyHighlights(para.trim())
      const hasMarks = html.includes('<mark')

      const idx = paraIndex++
      const isFocused = focusState === 'off' || idx === activeParagraph
      if (hasMarks) {
        return <p key={i} className={`mb-5 leading-[1.58] transition-opacity duration-200 ${isFocused ? 'opacity-100' : 'opacity-[0.3]'}`} dangerouslySetInnerHTML={{ __html: html }} />
      }
      return <p key={i} className={`mb-5 leading-[1.58] transition-opacity duration-200 ${isFocused ? 'opacity-100' : 'opacity-[0.3]'}`}>{para.trim()}</p>
    })
  }

  return (
    <div className="relative">
      <div
        ref={proseRef}
        onMouseUp={handleMouseUp}
        className={`prose-reader text-[1.1875rem] text-zinc-800 dark:text-zinc-200 font-sans leading-[1.58] selection:bg-blue-200 dark:selection:bg-blue-800/50 cursor-text antialiased ${focusState === 'typewriter' ? 'pb-[50vh]' : ''}`}
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
          existingFeedback={popover.editFeedback}
          onUpdated={onUpdateFeedback}
        />
      )}
    </div>
  )
}

// ── Prose Editor (Edit Mode) ───────────────────────────────

function ProseEditor({
  initialContent,
  draftId,
  currentVersion,
  onSaved,
}: {
  initialContent: string
  draftId: number
  currentVersion: number
  onSaved: (newVersion: number, newContent: string) => void
}) {
  const editorRef = useRef<HTMLDivElement>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [showSummary, setShowSummary] = useState(false)
  const [changeSummary, setChangeSummary] = useState('')
  const [savedMsg, setSavedMsg] = useState<string | null>(null)
  const [formatToolbar, setFormatToolbar] = useState<{ x: number; y: number } | null>(null)
  const [fontSize, setFontSize] = useState<string>('normal')
  const initialHtml = useRef(plainTextToHtml(initialContent))

  useEffect(() => {
    if (editorRef.current) {
      editorRef.current.innerHTML = initialHtml.current
    }
  }, [])

  const handleInput = useCallback(() => {
    setDirty(true)
  }, [])

  const handleSelectionChange = useCallback(() => {
    const sel = window.getSelection()
    if (!sel || sel.isCollapsed || !sel.toString().trim()) {
      setFormatToolbar(null)
      return
    }
    // Check selection is within editor
    if (editorRef.current && editorRef.current.contains(sel.anchorNode)) {
      const range = sel.getRangeAt(0)
      const rect = range.getBoundingClientRect()
      setFormatToolbar({
        x: rect.left + rect.width / 2 - 80,
        y: rect.top,
      })
    } else {
      setFormatToolbar(null)
    }
  }, [])

  useEffect(() => {
    document.addEventListener('selectionchange', handleSelectionChange)
    return () => document.removeEventListener('selectionchange', handleSelectionChange)
  }, [handleSelectionChange])

  function execBold() {
    document.execCommand('bold')
    setDirty(true)
  }

  function execItalic() {
    document.execCommand('italic')
    setDirty(true)
  }

  function insertSectionBreak() {
    const sel = window.getSelection()
    if (!sel || !editorRef.current) return
    // Remove current selection
    if (!sel.isCollapsed) {
      sel.deleteFromDocument()
    }
    // Insert HR styled as divider
    const hr = document.createElement('hr')
    hr.style.border = 'none'
    hr.style.textAlign = 'center'
    hr.className = 'section-break-hr'

    const range = sel.getRangeAt(0)
    range.insertNode(hr)
    // Move cursor after HR
    range.setStartAfter(hr)
    range.collapse(true)
    sel.removeAllRanges()
    sel.addRange(range)
    setDirty(true)
    setFormatToolbar(null)
  }

  function execUnderline() {
    document.execCommand('underline')
    setDirty(true)
  }

  function execHeading() {
    document.execCommand('formatBlock', false, '<h3>')
    setDirty(true)
  }

  function execUndo() {
    document.execCommand('undo')
  }

  function execRedo() {
    document.execCommand('redo')
  }

  function execFontSize(size: string) {
    setFontSize(size)
    if (!editorRef.current) return
    const sizeMap: Record<string, string> = { small: '0.9375rem', normal: '1.1875rem', large: '1.375rem' }
    editorRef.current.style.fontSize = sizeMap[size] || sizeMap.normal
  }

  function handleSaveClick() {
    if (!dirty) return
    setShowSummary(true)
  }

  async function handleSaveConfirm() {
    if (!editorRef.current) return
    setSaving(true)
    const htmlContent = editorRef.current.innerHTML
    try {
      const res = await fetch(`/api/writing/drafts/${draftId}/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content: htmlContent,
          change_summary: changeSummary.trim() || undefined,
        }),
      })
      if (res.ok) {
        const result = await res.json()
        const newVersion = result.version_number || currentVersion + 1
        setDirty(false)
        setShowSummary(false)
        setChangeSummary('')
        setSavedMsg(`Saved v${newVersion}`)
        setTimeout(() => setSavedMsg(null), 2500)
        onSaved(newVersion, htmlContent)
      }
    } finally {
      setSaving(false)
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 's' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      if (dirty) handleSaveClick()
    }
  }

  return (
    <div className="relative">
      {/* Enhanced Editor Toolbar */}
      <EditorToolbar
        onBold={execBold}
        onItalic={execItalic}
        onUnderline={execUnderline}
        onHeading={execHeading}
        onSectionBreak={insertSectionBreak}
        onFontSize={execFontSize}
        onUndo={execUndo}
        onRedo={execRedo}
        currentFontSize={fontSize}
      />

      {/* Save bar */}
      <div className="flex items-center gap-2 mb-4">
        {showSummary ? (
          <div className="flex items-center gap-2 flex-1">
            <input
              type="text"
              value={changeSummary}
              onChange={e => setChangeSummary(e.target.value)}
              placeholder="What did you change? (optional)"
              className="flex-1 px-3 py-1.5 text-sm bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 rounded-lg text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 dark:placeholder-zinc-500 outline-none focus:border-blue-400 dark:focus:border-blue-500"
              autoFocus
              onKeyDown={e => { if (e.key === 'Enter') handleSaveConfirm() }}
            />
            <button
              onClick={handleSaveConfirm}
              disabled={saving}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              <Save className="w-3.5 h-3.5" />
              {saving ? 'Saving...' : 'Save'}
            </button>
            <button
              onClick={() => { setShowSummary(false); setChangeSummary('') }}
              className="p-1.5 rounded-md text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        ) : (
          <>
            <button
              onClick={handleSaveClick}
              disabled={!dirty}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors
                ${dirty
                  ? 'bg-blue-600 text-white hover:bg-blue-700'
                  : 'bg-zinc-100 dark:bg-zinc-800 text-zinc-400 dark:text-zinc-500 cursor-not-allowed'
                }`}
            >
              <Save className="w-3.5 h-3.5" />
              Save
            </button>
            {savedMsg && (
              <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400 animate-pulse">
                <Check className="w-3.5 h-3.5" />
                {savedMsg}
              </span>
            )}
            {dirty && (
              <span className="text-[0.625rem] text-zinc-400 dark:text-zinc-500">
                {navigator.platform.includes('Mac') ? '\u2318' : 'Ctrl'}+S to save
              </span>
            )}
          </>
        )}
      </div>

      {/* Editor area */}
      <div
        ref={editorRef}
        contentEditable
        suppressContentEditableWarning
        onInput={handleInput}
        onKeyDown={handleKeyDown}
        className="prose-editor text-[1.1875rem] text-zinc-800 dark:text-zinc-200 font-sans leading-[1.58] selection:bg-blue-200 dark:selection:bg-blue-800/50 cursor-text outline-none border-l-2 border-blue-200 dark:border-blue-800/40 pl-6 min-h-[400px] antialiased [&_p]:mb-5 [&_hr]:border-none [&_hr]:text-center [&_hr]:py-4 [&_hr]:my-4 [&_hr]:[content:''] [&_hr]:block [&_hr]:h-6 [&_strong]:font-bold [&_em]:italic"
        style={{
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      />

      {/* Section break HR styling */}
      <style>{`
        .prose-editor hr {
          border: none;
          text-align: center;
          display: block;
          height: 1.5rem;
          margin: 1rem 0;
        }
        .prose-editor hr::after {
          content: '\\2022  \\2022  \\2022';
          color: #a1a1aa;
          font-size: 0.875rem;
          letter-spacing: 0.3em;
        }
        .dark .prose-editor hr::after {
          color: #71717a;
        }

        /* Floating toolbar entrance animation */
        @keyframes floatIn {
          from { opacity: 0; transform: translateY(4px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .animate-in { animation: floatIn 150ms ease-out; }
      `}</style>

      {/* Floating format toolbar */}
      {formatToolbar && (
        <FloatingToolbar
          position={formatToolbar}
          onBold={execBold}
          onItalic={execItalic}
          onSectionBreak={insertSectionBreak}
        />
      )}
    </div>
  )
}

// ── Diff Viewer ───────────────────────────────────────────

function DiffViewer({
  draftId,
  versions,
  currentVersionNumber,
}: {
  draftId: number
  versions: { id: number; version_number: number; word_count: number; change_summary: string | null; created_at: string }[]
  currentVersionNumber: number
}) {
  const [leftVersion, setLeftVersion] = useState<number>(Math.max(1, currentVersionNumber - 1))
  const [rightVersion, setRightVersion] = useState<number>(currentVersionNumber)
  const [leftContent, setLeftContent] = useState<string | null>(null)
  const [rightContent, setRightContent] = useState<string | null>(null)
  const [rightSummary, setRightSummary] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [diffSegments, setDiffSegments] = useState<DiffSegment[]>([])

  useEffect(() => {
    setLoading(true)
    Promise.all([
      fetch(`/api/writing/drafts/${draftId}/version/${leftVersion}`).then(r => r.json()),
      fetch(`/api/writing/drafts/${draftId}/version/${rightVersion}`).then(r => r.json()),
    ]).then(([left, right]) => {
      const lc = left.content || ''
      const rc = right.content || ''
      setLeftContent(lc)
      setRightContent(rc)
      setRightSummary(right.change_summary || null)
      setDiffSegments(computeWordDiff(lc, rc))
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [draftId, leftVersion, rightVersion])

  return (
    <div>
      {/* Version selectors */}
      <div className="flex items-center gap-3 mb-6 flex-wrap">
        <div className="flex items-center gap-2">
          <span className="text-xs text-zinc-500 dark:text-zinc-400">From</span>
          <select
            value={leftVersion}
            onChange={e => setLeftVersion(Number(e.target.value))}
            className="text-sm bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 rounded-lg px-2 py-1 text-zinc-900 dark:text-zinc-100 outline-none"
          >
            {versions.map(v => (
              <option key={v.version_number} value={v.version_number}>
                v{v.version_number} ({wordCount(v.word_count)} words)
              </option>
            ))}
          </select>
        </div>
        <span className="text-zinc-400 dark:text-zinc-500">→</span>
        <div className="flex items-center gap-2">
          <span className="text-xs text-zinc-500 dark:text-zinc-400">To</span>
          <select
            value={rightVersion}
            onChange={e => setRightVersion(Number(e.target.value))}
            className="text-sm bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 rounded-lg px-2 py-1 text-zinc-900 dark:text-zinc-100 outline-none"
          >
            {versions.map(v => (
              <option key={v.version_number} value={v.version_number}>
                v{v.version_number} ({wordCount(v.word_count)} words)
                {v.version_number === currentVersionNumber ? ' (latest)' : ''}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Change summary */}
      {rightSummary && (
        <div className="mb-4 px-3 py-2 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg">
          <p className="text-xs text-blue-700 dark:text-blue-400">
            <span className="font-medium">v{rightVersion} changes:</span> {rightSummary}
          </p>
        </div>
      )}

      {loading ? (
        <div className="text-center py-12 text-sm text-zinc-500 dark:text-zinc-400">Computing diff...</div>
      ) : leftVersion === rightVersion ? (
        <div className="text-center py-12 text-sm text-zinc-500 dark:text-zinc-400">Select two different versions to compare.</div>
      ) : (
        <div className="text-[1.0625rem] leading-[1.58] text-zinc-800 dark:text-zinc-200 font-sans antialiased">
          {diffSegments.map((seg, i) => {
            if (seg.type === 'equal') {
              return <span key={i}>{seg.text} </span>
            }
            if (seg.type === 'add') {
              return (
                <span key={i} className="bg-emerald-100 dark:bg-emerald-900/40 text-emerald-800 dark:text-emerald-300 rounded px-0.5">
                  {seg.text}{' '}
                </span>
              )
            }
            return (
              <span key={i} className="bg-red-100 dark:bg-red-900/40 text-red-800 dark:text-red-300 line-through rounded px-0.5">
                {seg.text}{' '}
              </span>
            )
          })}
        </div>
      )}

      {/* Stats */}
      {!loading && leftContent !== null && rightContent !== null && leftVersion !== rightVersion && (
        <div className="mt-6 pt-4 border-t border-zinc-200 dark:border-zinc-700 flex gap-4 text-xs text-zinc-500 dark:text-zinc-400">
          <span className="text-red-600 dark:text-red-400">{diffSegments.filter(s => s.type === 'remove').reduce((n, s) => n + s.text.split(/\s+/).length, 0)} words removed</span>
          <span className="text-emerald-600 dark:text-emerald-400">{diffSegments.filter(s => s.type === 'add').reduce((n, s) => n + s.text.split(/\s+/).length, 0)} words added</span>
        </div>
      )}
    </div>
  )
}

// ── Enhanced Editor Toolbar ───────────────────────────────

function EditorToolbar({
  onBold,
  onItalic,
  onUnderline,
  onHeading,
  onSectionBreak,
  onFontSize,
  onUndo,
  onRedo,
  currentFontSize,
}: {
  onBold: () => void
  onItalic: () => void
  onUnderline: () => void
  onHeading: () => void
  onSectionBreak: () => void
  onFontSize: (size: string) => void
  onUndo: () => void
  onRedo: () => void
  currentFontSize: string
}) {
  return (
    <div className="flex items-center gap-1 px-3 py-1.5 bg-white dark:bg-[#1e1e22] border border-zinc-200 dark:border-zinc-700 rounded-lg mb-3 flex-wrap">
      {/* Undo / Redo */}
      <button
        onClick={onUndo}
        className="p-1.5 rounded text-zinc-500 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
        title="Undo"
      >
        <Undo2 className="w-4 h-4" />
      </button>
      <button
        onClick={onRedo}
        className="p-1.5 rounded text-zinc-500 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
        title="Redo"
      >
        <Redo2 className="w-4 h-4" />
      </button>

      <div className="w-px h-5 bg-zinc-200 dark:bg-zinc-700 mx-1" />

      {/* Formatting */}
      <button
        onClick={onBold}
        className="p-1.5 rounded text-zinc-500 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
        title="Bold"
      >
        <Bold className="w-4 h-4" />
      </button>
      <button
        onClick={onItalic}
        className="p-1.5 rounded text-zinc-500 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
        title="Italic"
      >
        <Italic className="w-4 h-4" />
      </button>
      <button
        onClick={onUnderline}
        className="p-1.5 rounded text-zinc-500 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
        title="Underline"
      >
        <Underline className="w-4 h-4" />
      </button>

      <div className="w-px h-5 bg-zinc-200 dark:bg-zinc-700 mx-1" />

      {/* Heading */}
      <button
        onClick={onHeading}
        className="p-1.5 rounded text-zinc-500 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
        title="Heading (chapter/scene title)"
      >
        <Heading className="w-4 h-4" />
      </button>

      {/* Font Size */}
      <select
        value={currentFontSize}
        onChange={e => onFontSize(e.target.value)}
        className="text-xs bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 rounded px-1.5 py-1 text-zinc-700 dark:text-zinc-300 outline-none"
        title="Font size"
      >
        <option value="small">Small</option>
        <option value="normal">Normal</option>
        <option value="large">Large</option>
      </select>

      <div className="w-px h-5 bg-zinc-200 dark:bg-zinc-700 mx-1" />

      {/* Section Break */}
      <button
        onClick={onSectionBreak}
        className="p-1.5 rounded text-zinc-500 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
        title="Section break (• • •)"
      >
        <Minus className="w-4 h-4" />
      </button>
    </div>
  )
}

// ── Lore Link Panel ───────────────────────────────────────

function LoreLinkPanel({
  draftId,
  links,
  projectId,
  onLinkCreated,
  onLinkRemoved,
}: {
  draftId: number
  links: LoreLink[]
  projectId: number | null
  onLinkCreated: (link: LoreLink) => void
  onLinkRemoved: (linkId: number) => void
}) {
  const [showSearch, setShowSearch] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [loreEntries, setLoreEntries] = useState<LoreEntry[]>([])
  const [loadingLore, setLoadingLore] = useState(false)
  const [linkType, setLinkType] = useState<string>('references')
  const [creating, setCreating] = useState<number | null>(null)

  useEffect(() => {
    if (!showSearch || !projectId) return
    setLoadingLore(true)
    fetch(`/api/writing/lore?project_id=${projectId}`)
      .then(r => r.json())
      .then(data => { setLoreEntries(data); setLoadingLore(false) })
      .catch(() => setLoadingLore(false))
  }, [showSearch, projectId])

  const linkedIds = new Set(links.map(l => l.context_id))
  const filtered = loreEntries.filter(e =>
    !linkedIds.has(e.id) &&
    (searchQuery === '' || e.title.toLowerCase().includes(searchQuery.toLowerCase()) || e.context_type.toLowerCase().includes(searchQuery.toLowerCase()))
  )

  // Group by context_type
  const grouped: Record<string, LoreEntry[]> = {}
  for (const e of filtered) {
    if (!grouped[e.context_type]) grouped[e.context_type] = []
    grouped[e.context_type].push(e)
  }

  async function handleLink(entryId: number) {
    setCreating(entryId)
    try {
      const res = await fetch(`/api/writing/drafts/${draftId}/lore-links`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ context_id: entryId, link_type: linkType }),
      })
      if (res.ok) {
        const link = await res.json()
        onLinkCreated(link)
      }
    } finally {
      setCreating(null)
    }
  }

  async function handleUnlink(linkId: number) {
    const res = await fetch(`/api/writing/drafts/${draftId}/lore-links/${linkId}`, { method: 'DELETE' })
    if (res.ok) onLinkRemoved(linkId)
  }

  const linkTypes = ['references', 'establishes', 'contradicts', 'extends']

  return (
    <div className="space-y-3">
      {/* Linked entries */}
      {links.length > 0 && (
        <div className="space-y-2">
          <div className="text-[0.6875rem] font-medium text-zinc-500 dark:text-zinc-400 uppercase tracking-wider">Linked</div>
          {links.map(l => (
            <div
              key={l.id}
              className={`group bg-white dark:bg-[#222226] border border-zinc-200 dark:border-zinc-700 rounded-lg px-3 py-2 ${
                l.link_type === 'contradicts' ? 'border-l-2 border-l-red-400 dark:border-l-red-500' : ''
              }`}
            >
              <div className="flex items-center gap-1.5">
                <span className={`text-[0.5625rem] px-1.5 py-0.5 rounded font-medium ${linkTypeColors[l.link_type]}`}>
                  {l.link_type}
                </span>
                <Tag className="w-3 h-3 text-zinc-400 dark:text-zinc-500" />
                <span className="text-[0.625rem] text-zinc-400 dark:text-zinc-500 capitalize">{l.context_type}</span>
                <button
                  onClick={() => handleUnlink(l.id)}
                  className="ml-auto opacity-0 group-hover:opacity-100 p-0.5 rounded text-zinc-400 hover:text-red-500 dark:hover:text-red-400 transition-all"
                  title="Unlink"
                >
                  <Unlink className="w-3 h-3" />
                </button>
              </div>
              <p className="text-[0.8125rem] font-medium text-zinc-800 dark:text-zinc-200 mt-1">{l.context_title}</p>
              {l.note && <p className="text-[0.6875rem] text-zinc-500 dark:text-zinc-400 mt-0.5">{l.note}</p>}
              {l.link_type === 'contradicts' && (
                <div className="flex items-center gap-1 mt-1.5">
                  <AlertTriangle className="w-3 h-3 text-red-500 dark:text-red-400" />
                  <span className="text-[0.625rem] text-red-600 dark:text-red-400 font-medium">Contradiction — needs resolution</span>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Add link button / search */}
      {!showSearch ? (
        <button
          onClick={() => setShowSearch(true)}
          className="w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-[0.8125rem] font-medium border border-dashed border-zinc-300 dark:border-zinc-600 text-zinc-500 dark:text-zinc-400 hover:border-zinc-400 dark:hover:border-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300 transition-colors"
        >
          <Plus className="w-3.5 h-3.5" />
          Link Lore Entry
        </button>
      ) : (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-400" />
              <input
                type="text"
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                placeholder="Search lore..."
                className="w-full pl-7 pr-2 py-1.5 text-sm bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 rounded-lg text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 outline-none focus:border-blue-400"
                autoFocus
              />
            </div>
            <button
              onClick={() => { setShowSearch(false); setSearchQuery('') }}
              className="p-1 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Link type selector */}
          <div className="flex gap-1">
            {linkTypes.map(lt => (
              <button
                key={lt}
                onClick={() => setLinkType(lt)}
                className={`flex-1 text-center px-1 py-1 rounded text-[0.625rem] font-medium transition-colors ${
                  linkType === lt
                    ? linkTypeColors[lt]
                    : 'bg-zinc-50 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-700'
                }`}
              >
                {lt}
              </button>
            ))}
          </div>

          {/* Results */}
          {loadingLore ? (
            <p className="text-xs text-zinc-500 dark:text-zinc-400 py-2 text-center">Loading...</p>
          ) : filtered.length === 0 ? (
            <p className="text-xs text-zinc-500 dark:text-zinc-400 py-2 text-center">
              {loreEntries.length === 0 ? 'No lore entries for this project.' : 'No matching entries.'}
            </p>
          ) : (
            <div className="space-y-1 max-h-64 overflow-y-auto">
              {Object.entries(grouped).map(([type, entries]) => (
                <div key={type}>
                  <div className="text-[0.5625rem] uppercase tracking-wider text-zinc-400 dark:text-zinc-500 font-medium px-1 py-1">{type}</div>
                  {entries.map(e => (
                    <button
                      key={e.id}
                      onClick={() => handleLink(e.id)}
                      disabled={creating === e.id}
                      className="w-full text-left px-2 py-1.5 rounded-md text-[0.8125rem] text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors disabled:opacity-50"
                    >
                      <span className="font-medium">{e.title}</span>
                      {e.content_preview && (
                        <p className="text-[0.625rem] text-zinc-400 dark:text-zinc-500 line-clamp-1 mt-0.5">{e.content_preview}</p>
                      )}
                    </button>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Empty state when no links and not searching */}
      {links.length === 0 && !showSearch && (
        <div className="text-center py-4">
          <Link2 className="w-8 h-8 text-zinc-300 dark:text-zinc-600 mx-auto mb-2" />
          <p className="text-xs text-zinc-500 dark:text-zinc-400">No lore links yet.</p>
        </div>
      )}
    </div>
  )
}

// ── Feedback Panel ─────────────────────────────────────────

function FeedbackPanel({ items, onDelete, onProcess, processing }: { items: Feedback[]; onDelete: (id: number) => void; onProcess?: () => void; processing?: boolean }) {
  if (items.length === 0) return null

  const open = items.filter(f => f.status === 'open')
  const resolved = items.filter(f => f.status !== 'open')

  return (
    <div className="space-y-2">
      {open.length > 0 && onProcess && (
        <button
          onClick={onProcess}
          disabled={processing}
          className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-[0.8125rem] font-medium transition-all bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-60"
        >
          {processing ? (
            <>
              <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
              Processing...
            </>
          ) : (
            <>
              <Lightbulb className="w-3.5 h-3.5" />
              Process {open.length} Open Note{open.length > 1 ? 's' : ''}
            </>
          )}
        </button>
      )}
      {open.map(f => {
        const Icon = feedbackIcons[f.feedback_type] || StickyNote
        return (
          <div key={f.id} className={`group bg-white dark:bg-[#222226] border border-zinc-200 dark:border-zinc-700 border-l-2 ${feedbackBorderColors[f.feedback_type] || 'border-l-zinc-300 dark:border-l-zinc-600'} rounded-lg px-3 py-2.5`}>
            <div className="flex items-center gap-1.5 mb-1">
              <Icon className={`w-3.5 h-3.5 ${feedbackColors[f.feedback_type]}`} />
              <span className="text-[0.6875rem] font-medium text-zinc-700 dark:text-zinc-300 capitalize">{f.feedback_type}</span>
              <span className="text-[0.625rem] text-zinc-400 dark:text-zinc-500 ml-auto">{timeAgo(f.created_at)}</span>
              <button
                onClick={() => onDelete(f.id)}
                className="opacity-0 group-hover:opacity-100 p-0.5 rounded text-zinc-400 hover:text-red-500 dark:hover:text-red-400 transition-all"
                title="Delete note"
              >
                <Trash2 className="w-3 h-3" />
              </button>
            </div>
            {f.highlighted_text && (
              <p
                className="text-[0.6875rem] text-zinc-500 dark:text-zinc-400 italic line-clamp-2 mb-1 pl-5 cursor-pointer hover:text-amber-600 dark:hover:text-amber-400 transition-colors"
                onClick={() => {
                  const prefix = f.highlighted_text!.slice(0, 40).replace(/"/g, '&quot;')
                  const mark = document.querySelector(`mark[data-highlight="${CSS.escape(prefix)}"]`) as HTMLElement
                  if (mark) {
                    mark.scrollIntoView({ behavior: 'smooth', block: 'center' })
                    mark.classList.add('ring-2', 'ring-amber-400', 'ring-offset-1', 'dark:ring-offset-zinc-900')
                    setTimeout(() => mark.classList.remove('ring-2', 'ring-amber-400', 'ring-offset-1', 'dark:ring-offset-zinc-900'), 1500)
                  }
                }}
                title="Click to scroll to highlight"
              >"{f.highlighted_text.slice(0, 100)}{f.highlighted_text.length > 100 ? '...' : ''}"</p>
            )}
            <p className="text-[0.8125rem] text-zinc-800 dark:text-zinc-200 pl-5">{f.content}</p>
          </div>
        )
      })}
      {resolved.length > 0 && (
        <div className="mt-3">
          <div className="text-[0.6875rem] text-zinc-500 dark:text-zinc-400 font-medium mb-2">{resolved.length} resolved</div>
          <div className="space-y-2">
            {resolved.map(f => {
              const Icon = feedbackIcons[f.feedback_type] || StickyNote
              return (
                <details key={f.id} className="group">
                  <summary className={`flex items-center gap-1.5 px-3 py-2 rounded-lg cursor-pointer transition-colors bg-white/50 dark:bg-[#222226]/50 border border-zinc-200/60 dark:border-zinc-700/60 hover:bg-white dark:hover:bg-[#222226]`}>
                    <Icon className={`w-3 h-3 ${feedbackColors[f.feedback_type]} opacity-50`} />
                    <span className="text-[0.75rem] text-zinc-500 dark:text-zinc-400 truncate flex-1">{f.content.slice(0, 60)}{f.content.length > 60 ? '...' : ''}</span>
                    <ChevronRight className="w-3 h-3 text-zinc-400 dark:text-zinc-500 group-open:rotate-90 transition-transform shrink-0" />
                  </summary>
                  <div className="mt-1 ml-2 pl-3 border-l-2 border-zinc-200 dark:border-zinc-700 space-y-2 pb-2">
                    {/* Original note */}
                    <div className="pt-2">
                      <div className="flex items-center gap-1.5 mb-1">
                        <div className="w-4 h-4 rounded-full bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center">
                          <span className="text-[0.5rem] font-bold text-blue-700 dark:text-blue-400">A</span>
                        </div>
                        <span className="text-[0.625rem] text-zinc-400 dark:text-zinc-500">{timeAgo(f.created_at)}</span>
                      </div>
                      {f.highlighted_text && (
                        <p className="text-[0.6875rem] text-zinc-500 dark:text-zinc-400 italic mb-1">"{f.highlighted_text.slice(0, 120)}{f.highlighted_text.length > 120 ? '...' : ''}"</p>
                      )}
                      <p className="text-[0.8125rem] text-zinc-700 dark:text-zinc-300">{f.content}</p>
                    </div>
                    {/* Resolution */}
                    {f.resolution && (
                      <div>
                        <div className="flex items-center gap-1.5 mb-1">
                          <div className="w-4 h-4 rounded-full bg-amber-100 dark:bg-amber-900/30 flex items-center justify-center">
                            <span className="text-[0.5rem] font-bold text-amber-700 dark:text-amber-400">C</span>
                          </div>
                          <span className="text-[0.625rem] text-zinc-400 dark:text-zinc-500">{f.resolved_at ? timeAgo(f.resolved_at) : 'resolved'}</span>
                        </div>
                        <p className="text-[0.8125rem] text-zinc-600 dark:text-zinc-400">{f.resolution}</p>
                      </div>
                    )}
                    {/* Reopen button */}
                    <button
                      onClick={() => {
                        fetch(`/api/writing/feedback/${f.id}`, {
                          method: 'PATCH',
                          headers: { 'Content-Type': 'application/json' },
                          body: JSON.stringify({ status: 'open' }),
                        }).then(res => {
                          if (res.ok && onProcess) {
                            // Trigger a reload by calling onProcess's parent refresh pattern
                            window.location.reload()
                          }
                        })
                      }}
                      className="text-[0.6875rem] text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 font-medium transition-colors"
                    >
                      Reopen
                    </button>
                  </div>
                </details>
              )
            })}
          </div>
        </div>
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
  const [mode, setMode] = useState<'read' | 'edit' | 'diff'>('read')
  const [rightPanelOpen, setRightPanelOpen] = useState(true)
  const [versionMenuOpen, setVersionMenuOpen] = useState(false)
  const [processingFeedback, setProcessingFeedback] = useState(false)
  const [focusState, setFocusState] = useState<FocusState>('off')
  // Mobile: sidebar overlay state
  const [mobileOutlineOpen, setMobileOutlineOpen] = useState(false)
  const [mobileFeedbackOpen, setMobileFeedbackOpen] = useState(false)
  const bottomBarVisible = useScrollDirection()
  // Project selector state
  const [projects, setProjects] = useState<WritingProject[]>([])
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(() => {
    const saved = localStorage.getItem('soy_writing_project_id')
    return saved ? parseInt(saved) : null
  })
  const [showProjectSelector, setShowProjectSelector] = useState(false)
  const [newProjectName, setNewProjectName] = useState('')
  const [creatingProject, setCreatingProject] = useState(false)

  // Load projects list
  useEffect(() => {
    fetch('/api/writing/projects')
      .then(res => res.json())
      .then((data: WritingProject[]) => {
        setProjects(data)
        // If no project selected, pick the one with most drafts or first active
        if (!selectedProjectId && data.length > 0) {
          const withDrafts = data.filter(p => p.draft_count > 0)
          const pick = withDrafts.length > 0 ? withDrafts[0].id : data[0].id
          setSelectedProjectId(pick)
          localStorage.setItem('soy_writing_project_id', String(pick))
        }
      })
      .catch(() => {})
  }, [])

  // Load drafts when project changes
  useEffect(() => {
    if (!selectedProjectId) {
      setLoading(false)
      return
    }
    setLoading(true)
    setActiveDraft(null)
    fetch(`/api/writing/drafts?project_id=${selectedProjectId}`)
      .then(res => res.json())
      .then(data => {
        setDrafts(data)
        setLoading(false)
        // Check URL for draft param (deep link from chapter outline)
        const params = new URLSearchParams(window.location.search)
        const draftParam = params.get('draft')
        if (draftParam) {
          loadDraft(parseInt(draftParam))
        } else {
          // Auto-select first scene with content
          const firstScene = data.find((d: Draft) => d.parent_id && d.current_version > 0)
          if (firstScene) loadDraft(firstScene.id)
        }
      })
      .catch(() => setLoading(false))
  }, [selectedProjectId])

  function handleSelectProject(projectId: number) {
    setSelectedProjectId(projectId)
    localStorage.setItem('soy_writing_project_id', String(projectId))
    setShowProjectSelector(false)
  }

  async function handleCreateProject() {
    if (!newProjectName.trim()) return
    setCreatingProject(true)
    try {
      const res = await fetch('/api/writing/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newProjectName.trim() }),
      })
      if (res.ok) {
        const proj = await res.json()
        setProjects(prev => [{ ...proj, draft_count: 0, total_words: 0, open_feedback: 0, last_writing_activity: null }, ...prev])
        setSelectedProjectId(proj.id)
        localStorage.setItem('soy_writing_project_id', String(proj.id))
        setNewProjectName('')
        setShowProjectSelector(false)
      }
    } finally {
      setCreatingProject(false)
    }
  }

  function loadDraft(id: number) {
    setLoadingDraft(true)
    setMode('read') // Reset to read mode when switching drafts
    fetch(`/api/writing/drafts/${id}`)
      .then(res => res.json())
      .then(data => { setActiveDraft(data); setLoadingDraft(false) })
      .catch(() => setLoadingDraft(false))
    // Close mobile overlay after selection
    setMobileOutlineOpen(false)
  }

  function loadVersion(versionNumber: number) {
    if (!activeDraft) return
    setLoadingDraft(true)
    fetch(`/api/writing/drafts/${activeDraft.id}?version=${versionNumber}`)
      .then(res => res.json())
      .then(data => { setActiveDraft(data); setLoadingDraft(false) })
      .catch(() => setLoadingDraft(false))
    setVersionMenuOpen(false)
    setMode('read')
  }

  function handleNewFeedback(fb: Feedback) {
    if (activeDraft) {
      setActiveDraft({
        ...activeDraft,
        feedback: [fb, ...activeDraft.feedback],
      })
      setDrafts(prev => prev.map(d =>
        d.id === activeDraft.id ? { ...d, open_feedback: d.open_feedback + 1 } : d
      ))
    }
  }

  function handleUpdateFeedback(updated: Feedback) {
    if (activeDraft) {
      setActiveDraft({
        ...activeDraft,
        feedback: activeDraft.feedback.map(f => f.id === updated.id ? updated : f),
      })
    }
  }

  async function handleProcessFeedback() {
    if (!activeDraft) return
    setProcessingFeedback(true)
    try {
      const res = await fetch('/api/writing/process-feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: activeDraft.project_id }),
      })
      if (res.ok) {
        // Reload the draft to get updated feedback with resolutions
        const data = await fetch(`/api/writing/drafts/${activeDraft.id}`).then(r => r.json())
        setActiveDraft(data)
        // Update draft list open_feedback counts
        setDrafts(prev => prev.map(d => {
          const openCount = data.feedback?.filter((f: Feedback) => f.status === 'open').length || 0
          return d.id === activeDraft.id ? { ...d, open_feedback: openCount } : d
        }))
      }
    } catch (e) {
      console.error('Failed to process feedback', e)
    }
    setProcessingFeedback(false)
  }

  function handleDeleteFeedback(feedbackId: number) {
    fetch(`/api/writing/feedback/${feedbackId}`, { method: 'DELETE' })
      .then(res => {
        if (res.ok && activeDraft) {
          const wasOpen = activeDraft.feedback.find(f => f.id === feedbackId)?.status === 'open'
          setActiveDraft({
            ...activeDraft,
            feedback: activeDraft.feedback.filter(f => f.id !== feedbackId),
          })
          if (wasOpen) {
            setDrafts(prev => prev.map(d =>
              d.id === activeDraft.id ? { ...d, open_feedback: Math.max(0, d.open_feedback - 1) } : d
            ))
          }
        }
      })
  }

  function handleSaved(newVersion: number, newContent: string) {
    if (activeDraft) {
      const newWordCount = newContent.replace(/<[^>]*>/g, ' ').split(/\s+/).filter(Boolean).length
      setActiveDraft({
        ...activeDraft,
        current_version: newVersion,
        word_count: newWordCount,
        content: activeDraft.content ? {
          ...activeDraft.content,
          content: newContent,
          version_number: newVersion,
          word_count: newWordCount,
        } : null,
      })
      setDrafts(prev => prev.map(d =>
        d.id === activeDraft.id ? { ...d, current_version: newVersion, word_count: newWordCount } : d
      ))
      setMode('read')
    }
  }

  function handleLoreLinkCreated(link: LoreLink) {
    if (activeDraft) {
      setActiveDraft({
        ...activeDraft,
        lore_links: [...activeDraft.lore_links, link],
      })
    }
  }

  function handleLoreLinkRemoved(linkId: number) {
    if (activeDraft) {
      setActiveDraft({
        ...activeDraft,
        lore_links: activeDraft.lore_links.filter(l => l.id !== linkId),
      })
    }
  }

  // Build tree structure
  const chapters = drafts.filter(d => !d.parent_id)
  const getChildren = (parentId: number) => drafts.filter(d => d.parent_id === parentId)

  // Separate writing samples (no children, standalone) from parts/chapters (have children)
  const standaloneDrafts = chapters.filter(ch => {
    const children = getChildren(ch.id)
    return children.length === 0 && ch.draft_type !== 'chapter'
  })
  const structuredChapters = chapters.filter(ch => {
    const children = getChildren(ch.id)
    return children.length > 0 || ch.draft_type === 'chapter'
  })

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
  const openFeedbackCount = feedback.filter(f => f.status === 'open').length

  const selectedProject = projects.find(p => p.id === selectedProjectId)

  // Outline panel content (shared between desktop sidebar and mobile overlay)
  const outlineContent = (
    <>
      <div className="p-3 border-b border-zinc-200 dark:border-zinc-700">
        {/* Project selector */}
        <div className="relative">
          <button
            onClick={() => setShowProjectSelector(!showProjectSelector)}
            className="flex items-center gap-2 w-full text-left"
          >
            <PenTool className="w-4 h-4 text-zinc-600 dark:text-zinc-400 shrink-0" />
            <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 truncate flex-1">
              {selectedProject?.name || 'Select Project'}
            </h2>
            <ChevronDown className={`w-3.5 h-3.5 text-zinc-400 dark:text-zinc-500 shrink-0 transition-transform ${showProjectSelector ? 'rotate-180' : ''}`} />
          </button>
          {showProjectSelector && (
            <>
              <div className="fixed inset-0 z-20" onClick={() => setShowProjectSelector(false)} />
              <div className="absolute left-0 right-0 top-full mt-1 z-30 bg-white dark:bg-[#222226] border border-zinc-200 dark:border-zinc-700 rounded-lg shadow-xl max-h-64 overflow-y-auto">
                {projects.map(p => (
                  <button
                    key={p.id}
                    onClick={() => handleSelectProject(p.id)}
                    className={`w-full text-left px-3 py-2 hover:bg-zinc-50 dark:hover:bg-zinc-700/50 transition-colors ${
                      p.id === selectedProjectId ? 'bg-blue-50 dark:bg-blue-900/20' : ''
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-[0.8125rem] font-medium text-zinc-800 dark:text-zinc-200 truncate">{p.name}</span>
                      {p.draft_count > 0 && (
                        <span className="text-[0.625rem] text-zinc-400 dark:text-zinc-500 shrink-0 ml-2">
                          {p.draft_count} drafts · {wordCount(p.total_words)}
                        </span>
                      )}
                    </div>
                  </button>
                ))}
                <div className="border-t border-zinc-200 dark:border-zinc-700 p-2">
                  <div className="flex items-center gap-1.5">
                    <input
                      type="text"
                      value={newProjectName}
                      onChange={e => setNewProjectName(e.target.value)}
                      placeholder="New project name..."
                      className="flex-1 px-2 py-1 text-xs bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 rounded text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 outline-none focus:border-blue-400"
                      onKeyDown={e => { if (e.key === 'Enter') handleCreateProject() }}
                    />
                    <button
                      onClick={handleCreateProject}
                      disabled={!newProjectName.trim() || creatingProject}
                      className="p-1 rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
                      title="Create project"
                    >
                      <Plus className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            </>
          )}
        </div>
        <div className="flex gap-3 mt-1.5 text-[0.6875rem] text-zinc-500 dark:text-zinc-400">
          <span>{totalScenes} scenes</span>
          <span>{wordCount(totalWords)} words</span>
          {totalFeedback > 0 && <span className="text-amber-600 dark:text-amber-400">{totalFeedback} notes</span>}
        </div>
      </div>
      <nav className="p-2 overflow-y-auto" style={{ maxHeight: 'calc(100vh - 80px)' }}>
        {/* Standalone items (writing samples) */}
        {standaloneDrafts.length > 0 && (
          <>
            {standaloneDrafts.map(d => (
              <OutlineItem
                key={d.id}
                draft={d}
                children={[]}
                activeDraftId={activeDraft?.id ?? null}
                onSelect={loadDraft}
              />
            ))}
            {/* Separator between samples and structured content (#8) */}
            {structuredChapters.length > 0 && (
              <div className="mx-2 my-2 border-b border-zinc-200 dark:border-zinc-700" />
            )}
          </>
        )}
        {/* Structured chapters/parts */}
        {structuredChapters.map(ch => (
          <OutlineItem
            key={ch.id}
            draft={ch}
            children={getChildren(ch.id)}
            activeDraftId={activeDraft?.id ?? null}
            onSelect={loadDraft}
          />
        ))}
      </nav>
    </>
  )

  return (
    <main className="flex-1 min-h-screen flex flex-col lg:flex-row">
      {/* ── Mobile Outline Overlay ── */}
      {mobileOutlineOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div
            className="absolute inset-0 bg-black/40 backdrop-blur-sm"
            onClick={() => setMobileOutlineOpen(false)}
          />
          <div className="absolute inset-y-0 left-0 w-72 bg-zinc-50 dark:bg-[#1c1c20] border-r border-zinc-200 dark:border-zinc-700 shadow-xl overflow-y-auto slide-in-from-left">
            <div className="flex items-center justify-between p-3 border-b border-zinc-200 dark:border-zinc-700">
              <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Outline</span>
              <button
                onClick={() => setMobileOutlineOpen(false)}
                className="p-1 rounded-md text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            {outlineContent}
          </div>
        </div>
      )}

      {/* ── Mobile Feedback Overlay ── */}
      {mobileFeedbackOpen && activeDraft && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div
            className="absolute inset-0 bg-black/40 backdrop-blur-sm"
            onClick={() => setMobileFeedbackOpen(false)}
          />
          <div className="absolute inset-y-0 right-0 w-80 bg-zinc-50 dark:bg-[#1c1c20] border-l border-zinc-200 dark:border-zinc-700 shadow-xl overflow-y-auto slide-in-from-right">
            <div className="flex items-center justify-between p-3 border-b border-zinc-200 dark:border-zinc-700">
              <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Notes</span>
              <button
                onClick={() => setMobileFeedbackOpen(false)}
                className="p-1 rounded-md text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="p-3">
              {feedback.length === 0 ? (
                <div className="text-center py-8">
                  <StickyNote className="w-10 h-10 text-zinc-300 dark:text-zinc-600 mx-auto mb-3" />
                  <p className="text-sm text-zinc-500 dark:text-zinc-400">No notes yet</p>
                  <p className="text-[0.6875rem] text-zinc-400 dark:text-zinc-500 mt-1">Highlight text to annotate.</p>
                </div>
              ) : (
                <FeedbackPanel items={feedback} onDelete={handleDeleteFeedback} onProcess={handleProcessFeedback} processing={processingFeedback} />
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── Desktop Outline Panel ── */}
      <div className={`hidden lg:block ${outlineOpen ? 'w-64' : 'w-0'} shrink-0 border-r border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-[#1c1c20] overflow-hidden transition-all`}>
        {outlineContent}
      </div>

      {/* ── Main Reading/Editing Area ── */}
      <div className="flex-1 min-w-0 flex flex-col">
        {/* Toolbar */}
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-zinc-200 dark:border-zinc-700 bg-white dark:bg-[#18181c] shrink-0">
          {/* Desktop outline toggle */}
          <button
            onClick={() => setOutlineOpen(!outlineOpen)}
            className="hidden lg:block p-1.5 rounded-md text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800"
            title={outlineOpen ? 'Hide outline' : 'Show outline'}
          >
            <BookOpen className="w-4 h-4" />
          </button>
          {/* Mobile outline toggle */}
          <button
            onClick={() => setMobileOutlineOpen(true)}
            className="lg:hidden p-1.5 rounded-md text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800"
            title="Show outline"
          >
            <Menu className="w-4 h-4" />
          </button>

          {activeDraft && (
            <>
              <h1 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 truncate">
                {activeDraft.title}
              </h1>
              {activeDraft.pov_character && (
                <span className="text-[0.6875rem] px-2 py-0.5 rounded-full bg-indigo-50 dark:bg-indigo-900/20 text-indigo-700 dark:text-indigo-400 shrink-0 hidden sm:inline-flex items-center">
                  <Eye className="w-3 h-3 inline -mt-0.5 mr-0.5" />
                  {activeDraft.pov_character}
                </span>
              )}
              <span className={`text-[0.625rem] px-2 py-0.5 rounded-full font-medium shrink-0 hidden sm:inline ${statusColors[activeDraft.status]}`}>
                {activeDraft.status}
              </span>

              {/* Mode toggle */}
              {activeDraft.content && (
                <div className="flex items-center bg-zinc-100 dark:bg-zinc-800 rounded-full p-0.5 ml-2 shrink-0">
                  {(['read', 'edit', 'diff'] as const).map(m => (
                    <button
                      key={m}
                      onClick={() => setMode(m)}
                      className={`px-3 py-1 rounded-full text-[0.6875rem] font-medium transition-colors flex items-center gap-1
                        ${mode === m
                          ? 'bg-white dark:bg-zinc-700 text-zinc-900 dark:text-zinc-100 shadow-sm'
                          : 'text-zinc-500 dark:text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200'
                        }`}
                    >
                      {m === 'diff' && <GitCompare className="w-3 h-3" />}
                      {m.charAt(0).toUpperCase() + m.slice(1)}
                    </button>
                  ))}
                </div>
              )}

              {/* Focus mode toggle (#4/#5) */}
              {mode === 'read' && activeDraft.content && (
                <button
                  onClick={() => setFocusState(nextFocusState(focusState))}
                  className={`p-1.5 rounded-md shrink-0 transition-colors hidden sm:block ${
                    focusState !== 'off'
                      ? 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20'
                      : 'text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800'
                  }`}
                  title={focusState === 'off' ? 'Focus mode' : focusState === 'focus' ? 'Typewriter mode' : 'Disable focus'}
                >
                  <Eye className="w-4 h-4" />
                  {focusState !== 'off' && (
                    <span className="sr-only">{focusState === 'focus' ? 'Focus' : 'Typewriter'}</span>
                  )}
                </button>
              )}

              <div className="relative ml-auto shrink-0 hidden sm:block">
                <button
                  onClick={() => setVersionMenuOpen(!versionMenuOpen)}
                  className="text-[0.6875rem] text-zinc-400 dark:text-zinc-500 hover:text-zinc-600 dark:hover:text-zinc-300 transition-colors"
                >
                  {wordCount(activeDraft.content?.word_count || activeDraft.word_count)} words
                  {activeDraft.content && ` \u00B7 v${activeDraft.content.version_number}`}
                  {activeDraft.versions.length > 1 && (
                    <ChevronDown className="w-3 h-3 inline ml-0.5 -mt-0.5" />
                  )}
                </button>
                {versionMenuOpen && activeDraft.versions.length > 1 && (
                  <>
                    <div className="fixed inset-0 z-30" onClick={() => setVersionMenuOpen(false)} />
                    <div className="absolute right-0 top-full mt-1 z-40 w-64 bg-white dark:bg-[#222226] border border-zinc-200 dark:border-zinc-700 rounded-lg shadow-xl py-1 max-h-60 overflow-y-auto">
                      <div className="px-3 py-1.5 text-[0.625rem] text-zinc-400 dark:text-zinc-500 font-medium uppercase tracking-wider">Version History</div>
                      {activeDraft.versions.map(v => (
                        <button
                          key={v.version_number}
                          onClick={() => loadVersion(v.version_number)}
                          className={`w-full text-left px-3 py-2 hover:bg-zinc-50 dark:hover:bg-zinc-700/50 transition-colors ${
                            activeDraft.content?.version_number === v.version_number ? 'bg-blue-50 dark:bg-blue-900/20' : ''
                          }`}
                        >
                          <div className="flex items-center justify-between">
                            <span className="text-[0.8125rem] font-medium text-zinc-800 dark:text-zinc-200">
                              v{v.version_number}
                              {v.version_number === activeDraft.current_version && (
                                <span className="ml-1.5 text-[0.5625rem] px-1.5 py-0.5 rounded-full bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400">latest</span>
                              )}
                            </span>
                            <span className="text-[0.625rem] text-zinc-400 dark:text-zinc-500">{wordCount(v.word_count)} words</span>
                          </div>
                          {v.change_summary && (
                            <p className="text-[0.6875rem] text-zinc-500 dark:text-zinc-400 mt-0.5 line-clamp-2">{v.change_summary}</p>
                          )}
                          <p className="text-[0.5625rem] text-zinc-400 dark:text-zinc-500 mt-0.5">{timeAgo(v.created_at)}</p>
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>
              {/* Desktop feedback panel toggle */}
              <button
                onClick={() => setRightPanelOpen(!rightPanelOpen)}
                className="hidden lg:block p-1.5 rounded-md text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 shrink-0"
                title={rightPanelOpen ? 'Hide panel' : 'Show panel'}
              >
                <MessageSquare className="w-4 h-4" />
              </button>
            </>
          )}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto bg-zinc-50 dark:bg-[#1a1a1e]">
          {loadingDraft ? (
            <div className="flex items-center justify-center py-20">
              <p className="text-sm text-zinc-500 dark:text-zinc-400">Loading...</p>
            </div>
          ) : activeDraft?.content ? (
            <div className="max-w-full md:max-w-prose mx-auto px-5 lg:px-10 py-8">
              {/* Synopsis/Chapter Header (#7) */}
              {(activeDraft.synopsis || activeDraft.title) && (
                <div className={`mb-8 pb-6 border-b border-zinc-100 dark:border-zinc-800 ${activeDraft.pov_character ? 'border-l-2 border-l-indigo-400 dark:border-l-indigo-500 pl-4' : ''}`}>
                  <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100 mb-2">
                    {activeDraft.title}
                  </h2>
                  <div className="flex items-center gap-2 mb-2">
                    {activeDraft.pov_character && (
                      <span className="text-[0.6875rem] px-2 py-0.5 rounded-full bg-indigo-50 dark:bg-indigo-900/20 text-indigo-700 dark:text-indigo-400 inline-flex items-center">
                        <Eye className="w-3 h-3 mr-0.5" />
                        {activeDraft.pov_character}
                      </span>
                    )}
                    <span className="text-[0.6875rem] text-zinc-400 dark:text-zinc-500">
                      {wordCount(activeDraft.word_count)} words
                    </span>
                  </div>
                  {activeDraft.synopsis && (
                    <p className="text-[0.8125rem] text-zinc-500 dark:text-zinc-400 italic leading-relaxed">
                      {activeDraft.synopsis}
                    </p>
                  )}
                </div>
              )}
              {mode === 'read' ? (
                <ProseReader
                  content={activeDraft.content.content}
                  feedback={feedback}
                  draftId={activeDraft.id}
                  onNewFeedback={handleNewFeedback}
                  onUpdateFeedback={handleUpdateFeedback}
                  focusState={focusState}
                />
              ) : mode === 'edit' ? (
                <ProseEditor
                  key={`${activeDraft.id}-${activeDraft.current_version}`}
                  initialContent={activeDraft.content.content}
                  draftId={activeDraft.id}
                  currentVersion={activeDraft.current_version}
                  onSaved={handleSaved}
                />
              ) : (
                <DiffViewer
                  draftId={activeDraft.id}
                  versions={activeDraft.versions}
                  currentVersionNumber={activeDraft.current_version}
                />
              )}
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

        {/* ── Mobile Bottom Bar (#3b) ── */}
        {activeDraft && (
          <div
            className={`lg:hidden fixed bottom-0 inset-x-0 h-14 bg-[#18181c]/95 backdrop-blur-sm border-t border-zinc-700 flex items-center justify-around px-4 z-30 transition-transform duration-200 ${
              bottomBarVisible ? 'translate-y-0' : 'translate-y-full'
            }`}
            style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
          >
            <button
              onClick={() => setMobileOutlineOpen(true)}
              className="flex flex-col items-center gap-0.5 text-zinc-400 hover:text-zinc-200 transition-colors"
            >
              <AlignLeft className="w-5 h-5" />
              <span className="text-[0.5625rem]">Outline</span>
            </button>
            <div className="flex flex-col items-center gap-0.5 text-zinc-500">
              <Hash className="w-5 h-5" />
              <span className="text-[0.5625rem]">{wordCount(activeDraft.word_count)}</span>
            </div>
            {mode === 'read' && (
              <button
                onClick={() => setFocusState(nextFocusState(focusState))}
                className={`flex flex-col items-center gap-0.5 transition-colors ${
                  focusState !== 'off' ? 'text-blue-400' : 'text-zinc-400 hover:text-zinc-200'
                }`}
              >
                <Eye className="w-5 h-5" />
                <span className="text-[0.5625rem]">{focusState === 'off' ? 'Focus' : focusState === 'focus' ? 'Focus' : 'Typewriter'}</span>
              </button>
            )}
            <button
              onClick={() => setMobileFeedbackOpen(true)}
              className="flex flex-col items-center gap-0.5 text-zinc-400 hover:text-zinc-200 transition-colors relative"
            >
              <MessageSquare className="w-5 h-5" />
              <span className="text-[0.5625rem]">Notes</span>
              {openFeedbackCount > 0 && (
                <span className="absolute -top-1 -right-1 text-[0.5rem] w-4 h-4 rounded-full bg-amber-500 text-white flex items-center justify-center font-medium">
                  {openFeedbackCount}
                </span>
              )}
            </button>
          </div>
        )}
      </div>

      {/* ── Right Panel: Feedback / Lore / Characters (desktop) ── */}
      {activeDraft && (
        <div className={`hidden lg:flex ${rightPanelOpen ? 'w-72' : 'w-0'} shrink-0 border-l border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-[#1c1c20] flex-col overflow-hidden transition-all`}>
          {/* Tab bar — pill-style (#9) */}
          <div className="flex gap-1 p-2 shrink-0">
            {[
              { key: 'feedback' as const, icon: MessageSquare, label: 'Notes', count: openFeedbackCount },
              { key: 'lore' as const, icon: Link2, label: 'Lore', count: loreLinks.length },
              { key: 'characters' as const, icon: Users, label: 'Cast', count: characterList.length },
            ].map(tab => (
              <button
                key={tab.key}
                onClick={() => setPanelTab(tab.key)}
                className={`flex-1 flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-full text-[0.6875rem] font-medium transition-colors
                  ${panelTab === tab.key
                    ? 'bg-zinc-200 dark:bg-zinc-700 text-zinc-900 dark:text-zinc-100'
                    : 'text-zinc-500 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 hover:text-zinc-700 dark:hover:text-zinc-300'
                  }`}
              >
                <tab.icon className="w-3.5 h-3.5" />
                {tab.label}
                {tab.count > 0 && (
                  <span className="text-[0.5625rem] px-1 rounded-full bg-zinc-300 dark:bg-zinc-600">{tab.count}</span>
                )}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <div className="flex-1 overflow-y-auto p-3">
            {panelTab === 'feedback' && (
              feedback.length === 0 ? (
                <div className="text-center py-8">
                  <StickyNote className="w-10 h-10 text-zinc-300 dark:text-zinc-600 mx-auto mb-3" />
                  <p className="text-sm text-zinc-500 dark:text-zinc-400">No notes yet</p>
                  <p className="text-[0.6875rem] text-zinc-400 dark:text-zinc-500 mt-1">Highlight text to annotate.</p>
                </div>
              ) : (
                <FeedbackPanel items={feedback} onDelete={handleDeleteFeedback} onProcess={handleProcessFeedback} processing={processingFeedback} />
              )
            )}

            {panelTab === 'lore' && activeDraft && (
              <LoreLinkPanel
                draftId={activeDraft.id}
                links={loreLinks}
                projectId={activeDraft.project_id}
                onLinkCreated={handleLoreLinkCreated}
                onLinkRemoved={handleLoreLinkRemoved}
              />
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
                    <div key={c.id} className="flex items-center gap-2 bg-white dark:bg-[#222226] border border-zinc-200 dark:border-zinc-700 rounded-lg px-3 py-2">
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

      {/* Slide-in animation styles */}
      <style>{`
        @keyframes slideInFromLeft {
          from { transform: translateX(-100%); }
          to { transform: translateX(0); }
        }
        @keyframes slideInFromRight {
          from { transform: translateX(100%); }
          to { transform: translateX(0); }
        }
        .slide-in-from-left { animation: slideInFromLeft 200ms ease-out; }
        .slide-in-from-right { animation: slideInFromRight 200ms ease-out; }
      `}</style>
    </main>
  )
}

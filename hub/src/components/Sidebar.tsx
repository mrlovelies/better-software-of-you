import {
  Home,
  LayoutDashboard,
  Users,
  Share2,
  Mail,
  Calendar,
  MessageSquare,
  Scale,
  BookOpen,
  StickyNote,
  ClipboardList,
  Bell,
  Clock,
  Search,
  Mic,
  FolderOpen,
  DollarSign,
  ChevronRight,
  Menu,
  X,
  Moon,
  Sun,
  FileText,
  BarChart3,
  Lightbulb,
  User,
  PenTool,
} from 'lucide-react'
import { useState, useRef, useEffect } from 'react'
import type { NavigationData, ContentRoute } from '../types'

interface SidebarProps {
  nav: NavigationData | null
  currentPage: string | null
  onNavigate: (route: ContentRoute) => void
  theme: 'light' | 'dark'
  onToggleTheme: () => void
}

function SidebarItem({
  icon: Icon,
  label,
  badge,
  badgeAlert,
  active,
  onClick,
}: {
  icon: React.ComponentType<{ className?: string }>
  label: string
  badge?: number
  badgeAlert?: boolean
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2 px-3 py-1.5 rounded-md text-[0.8125rem] transition-all cursor-pointer w-full text-left
        ${active
          ? 'bg-blue-600/15 text-blue-600 dark:text-blue-400 font-semibold'
          : 'text-zinc-600 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 hover:text-zinc-900 dark:hover:text-zinc-100'
        }`}
    >
      <Icon className="w-4 h-4 shrink-0" />
      <span className="truncate">{label}</span>
      {badge != null && badge > 0 && (
        <span
          className={`ml-auto text-[0.6875rem] px-1.5 rounded-full font-medium shrink-0
            ${badgeAlert
              ? (active ? 'bg-red-600 text-white' : 'bg-red-500/15 text-red-600 dark:text-red-400')
              : (active ? 'bg-blue-600 text-white' : 'bg-zinc-200 dark:bg-zinc-700 text-zinc-600 dark:text-zinc-300')
            }`}
        >
          {badge}
        </span>
      )}
    </button>
  )
}

function SidebarSection({
  id,
  label,
  forceOpen,
  children,
}: {
  id: string
  label: string
  forceOpen?: boolean
  children: React.ReactNode
}) {
  const [manualOpen, setManualOpen] = useState(false)
  const open = forceOpen || manualOpen

  return (
    <div className="mt-2" id={id}>
      <button
        className="flex items-center justify-between w-full px-3 py-1.5 text-[0.6875rem] font-semibold text-zinc-500 dark:text-zinc-400 uppercase tracking-wider hover:text-zinc-700 dark:hover:text-zinc-300 transition-colors"
        onClick={() => setManualOpen(!manualOpen)}
      >
        <span>{label}</span>
        <ChevronRight className={`w-3.5 h-3.5 transition-transform ${open ? 'rotate-90' : ''}`} />
      </button>
      {open && <div className="pt-0.5">{children}</div>}
    </div>
  )
}

function EntityLink({
  label,
  active,
  indent,
  icon: Icon,
  onClick,
}: {
  label: string
  active: boolean
  indent?: boolean
  icon?: React.ComponentType<{ className?: string }>
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2 w-full text-left px-3 py-1 ${indent ? 'pl-10 text-[0.6875rem]' : 'pl-7 text-[0.8125rem]'} truncate rounded-md transition-all
        ${active
          ? 'bg-blue-600/15 text-blue-600 dark:text-blue-400 font-semibold'
          : `${indent ? 'text-zinc-500 dark:text-zinc-400' : 'text-zinc-600 dark:text-zinc-300'} hover:bg-zinc-100 dark:hover:bg-zinc-800 hover:text-zinc-900 dark:hover:text-zinc-100`
        }`}
    >
      {Icon && <Icon className="w-3.5 h-3.5 shrink-0" />}
      <span className="truncate">{label}</span>
    </button>
  )
}

const subViewIcons: Record<string, React.ComponentType<{ className?: string }>> = {
  pm_report: BarChart3,
  project_analysis: Lightbulb,
  prep_page: ClipboardList,
  module_view: FileText,
}

export default function Sidebar({ nav, currentPage, onNavigate, theme, onToggleTheme }: SidebarProps) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const [query, setQuery] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)

  // Cmd+K / Ctrl+K to focus search
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        searchRef.current?.focus()
      }
      if (e.key === 'Escape' && document.activeElement === searchRef.current) {
        setQuery('')
        searchRef.current?.blur()
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [])

  if (!nav) {
    return (
      <aside className="fixed top-0 left-0 h-screen w-60 bg-white dark:bg-zinc-900 border-r border-zinc-200 dark:border-zinc-700 hidden lg:flex flex-col">
        <div className="p-4 border-b border-zinc-100 dark:border-zinc-800">
          <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Software of You</span>
        </div>
        <div className="flex-1 flex items-center justify-center">
          <span className="text-xs text-zinc-500 dark:text-zinc-400">Loading...</span>
        </div>
      </aside>
    )
  }

  const modules = new Set(nav.modules.map(m => m.toLowerCase()))
  const viewFilenames = new Set(nav.views.map(v => v.filename))
  for (const v of nav.views) {
    if (v.children) {
      for (const c of v.children) {
        viewFilenames.add(c.filename)
      }
    }
  }

  const hasModule = (name: string) => modules.has(name)
  const hasView = (filename: string) => viewFilenames.has(filename)

  const q = query.toLowerCase().trim()
  const searching = q.length > 0

  // Filter contacts and projects by search query
  const filteredContacts = searching
    ? nav.contacts.filter(c => c.name.toLowerCase().includes(q) || (c.company && c.company.toLowerCase().includes(q)))
    : nav.contacts

  const filteredProjects = searching
    ? nav.projects.filter(p => p.name.toLowerCase().includes(q) || (p.client_name && p.client_name.toLowerCase().includes(q)))
    : nav.projects

  // Filter view items by label match
  const viewMatch = (label: string) => !searching || label.toLowerCase().includes(q)

  function nav_(r: ContentRoute) {
    onNavigate(r)
    setMobileOpen(false)
    setQuery('')
  }

  const sidebar = (
    <>
      {/* Header */}
      <div className="p-4 border-b border-zinc-100 dark:border-zinc-800 shrink-0">
        <div className="flex items-center justify-between">
          <button
            onClick={() => nav_({ type: 'home' })}
            className="flex items-center gap-2 text-sm font-semibold text-zinc-900 dark:text-zinc-100 hover:text-blue-600 dark:hover:text-blue-400 transition-colors"
          >
            <Home className="w-4 h-4" />
            Software of You
          </button>
          <button
            onClick={onToggleTheme}
            className="p-1.5 rounded-md text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
            title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          >
            {theme === 'dark' ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
          </button>
        </div>

        {/* Search */}
        <div className="relative mt-3">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-500 dark:text-zinc-400 pointer-events-none" />
          <input
            ref={searchRef}
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search..."
            className="w-full pl-8 pr-8 py-1.5 text-xs bg-zinc-100 dark:bg-zinc-800 border border-transparent focus:border-blue-400 dark:focus:border-blue-500 rounded-md text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 dark:placeholder-zinc-500 outline-none transition-colors"
          />
          {query ? (
            <button
              onClick={() => { setQuery(''); searchRef.current?.focus() }}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-600 dark:hover:text-zinc-300"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          ) : (
            <kbd className="absolute right-2 top-1/2 -translate-y-1/2 text-[0.5625rem] text-zinc-500 dark:text-zinc-400 border border-zinc-200 dark:border-zinc-700 rounded px-1 py-0.5 leading-none pointer-events-none">
              {navigator.platform.includes('Mac') ? '\u2318' : 'Ctrl'}K
            </kbd>
          )}
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-2 py-2">
        {/* Dashboard — only show if generated */}
        {hasView('dashboard.html') && viewMatch('Dashboard') && (
          <SidebarItem
            icon={LayoutDashboard}
            label="Dashboard"
            active={currentPage === 'dashboard.html'}
            onClick={() => nav_({ type: 'page', filename: 'dashboard.html' })}
          />
        )}

        {/* People section */}
        {hasModule('crm') && (filteredContacts.length > 0 || !searching) && (
          <SidebarSection id="section-people" label="People" forceOpen={searching}>
            {!searching && (
              <SidebarItem
                icon={Users}
                label="All Contacts"
                badge={nav.badges.contacts}
                active={currentPage === 'contacts.html'}
                onClick={() => nav_({ type: 'page', filename: 'contacts.html' })}
              />
            )}
            {!searching && hasView('network-map.html') && (
              <SidebarItem
                icon={Share2}
                label="Network Map"
                active={currentPage === 'network-map.html'}
                onClick={() => nav_({ type: 'page', filename: 'network-map.html' })}
              />
            )}
            {!searching && (
              <div className="h-px bg-zinc-100 dark:bg-zinc-800 mx-3 my-1" />
            )}
            {filteredContacts.map(c => (
              <EntityLink
                key={c.id}
                label={c.name}
                icon={User}
                active={currentPage === (c.page_filename || `contact:${c.id}`)}
                onClick={() => c.page_filename
                  ? nav_({ type: 'page', filename: c.page_filename })
                  : nav_({ type: 'contact', id: c.id })
                }
              />
            ))}
          </SidebarSection>
        )}

        {/* Projects section */}
        {hasModule('project-tracker') && filteredProjects.length > 0 && (
          <SidebarSection id="section-projects" label="Projects" forceOpen={searching}>
            {filteredProjects.map(p => (
              <div key={p.id}>
                <EntityLink
                  label={p.name}
                  icon={FolderOpen}
                  active={currentPage === (p.page_filename || `project:${p.id}`)}
                  onClick={() => p.page_filename
                    ? nav_({ type: 'page', filename: p.page_filename })
                    : nav_({ type: 'project', id: p.id })
                  }
                />
                {!searching && p.children && p.children.length > 0 && p.children.map(child => (
                  <EntityLink
                    key={child.id}
                    label={child.entity_name || child.view_type.replace('_', ' ')}
                    icon={subViewIcons[child.view_type] || FileText}
                    indent
                    active={currentPage === child.filename}
                    onClick={() => nav_({ type: 'page', filename: child.filename })}
                  />
                ))}
              </div>
            ))}
          </SidebarSection>
        )}

        {/* Communications — show when module installed, regardless of view existence */}
        {!searching && (hasModule('gmail') || hasModule('calendar')) && (
          <SidebarSection id="section-comms" label="Communications">
            {hasModule('gmail') && (
              <SidebarItem
                icon={Mail}
                label="Email"
                badge={nav.badges.emails}
                active={currentPage === 'emails'}
                onClick={() => nav_({ type: 'emails' })}
              />
            )}
            {hasModule('calendar') && (
              <SidebarItem
                icon={Calendar}
                label="Calendar"
                badge={nav.badges.calendar}
                active={currentPage === 'calendar'}
                onClick={() => nav_({ type: 'calendar' })}
              />
            )}
          </SidebarSection>
        )}

        {/* Creative — show when creative_identity or writing module installed */}
        {!searching && (hasModule('creative_identity') || hasModule('writing')) && (
          <SidebarSection id="section-creative" label="Creative">
            {hasModule('creative_identity') && (
              <SidebarItem
                icon={BookOpen}
                label="Creative Dashboard"
                active={currentPage === 'creative-dashboard.html'}
                onClick={() => nav_({ type: 'page', filename: 'creative-dashboard.html' })}
              />
            )}
            {hasModule('writing') && (
              <SidebarItem
                icon={PenTool}
                label="Writing"
                badge={nav.badges.writing}
                active={currentPage === 'writing'}
                onClick={() => nav_({ type: 'writing' } as ContentRoute)}
              />
            )}
          </SidebarSection>
        )}

        {/* Intelligence — show when module installed, regardless of view existence */}
        {!searching && (() => {
          const items: Array<{ mod: string; routeType: ContentRoute['type']; icon: React.ComponentType<{ className?: string }>; label: string; badgeKey: string }> = [
            { mod: 'conversation-intelligence', routeType: 'page', icon: MessageSquare, label: 'Conversations', badgeKey: 'transcripts' },
            { mod: 'decision-log', routeType: 'decisions', icon: Scale, label: 'Decisions', badgeKey: 'decisions' },
            { mod: 'journal', routeType: 'journal', icon: BookOpen, label: 'Journal', badgeKey: 'journal' },
            { mod: 'notes', routeType: 'notes', icon: StickyNote, label: 'Notes', badgeKey: 'notes' },
          ]
          const visible = items.filter(i => hasModule(i.mod))
          if (visible.length === 0) return null
          return (
            <SidebarSection id="section-intelligence" label="Intelligence">
              {visible.map(i => {
                if (i.routeType === 'page') {
                  // Conversations still uses iframe for now
                  return (
                    <SidebarItem
                      key={i.label}
                      icon={i.icon}
                      label={i.label}
                      badge={nav.badges[i.badgeKey]}
                      active={currentPage === 'conversations.html'}
                      onClick={() => nav_({ type: 'page', filename: 'conversations.html' })}
                    />
                  )
                }
                return (
                  <SidebarItem
                    key={i.label}
                    icon={i.icon}
                    label={i.label}
                    badge={nav.badges[i.badgeKey]}
                    active={currentPage === i.routeType}
                    onClick={() => nav_({ type: i.routeType } as ContentRoute)}
                  />
                )
              })}
            </SidebarSection>
          )
        })()}

        {/* Tools */}
        {!searching && (
          <SidebarSection id="section-tools" label="Tools">
            {hasModule('auditions') && (
              <SidebarItem
                icon={Mic}
                label="Auditions"
                active={currentPage === 'auditions' || currentPage === 'audition-board.html'}
                onClick={() => nav_({ type: 'auditions' })}
              />
            )}
            {hasModule('income_tracking') && (
              <SidebarItem
                icon={DollarSign}
                label="Income"
                active={currentPage === 'income' || currentPage === 'income.html'}
                onClick={() => nav_({ type: 'income' })}
              />
            )}
            <SidebarItem
              icon={ClipboardList}
              label="Weekly Review"
              active={currentPage === 'weekly-review.html'}
              onClick={() => nav_({ type: 'page', filename: 'weekly-review.html' })}
            />
            <SidebarItem
              icon={Bell}
              label="Nudges"
              badge={nav.urgent_count > 0 ? nav.urgent_count : undefined}
              badgeAlert={nav.urgent_count > 0}
              active={currentPage === 'nudges'}
              onClick={() => nav_({ type: 'nudges' })}
            />
            {hasView('timeline.html') && (
              <SidebarItem
                icon={Clock}
                label="Timeline"
                active={currentPage === 'timeline.html'}
                onClick={() => nav_({ type: 'page', filename: 'timeline.html' })}
              />
            )}
          </SidebarSection>
        )}

        {/* No results */}
        {searching && filteredContacts.length === 0 && filteredProjects.length === 0 && (
          <div className="px-3 py-6 text-center">
            <p className="text-xs text-zinc-500 dark:text-zinc-400">No matches for "{query}"</p>
          </div>
        )}
      </nav>
    </>
  )

  return (
    <>
      {/* Mobile toggle */}
      <button
        className="fixed top-4 left-4 z-50 flex items-center justify-center bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-lg p-2 shadow-sm lg:hidden"
        onClick={() => setMobileOpen(!mobileOpen)}
      >
        {mobileOpen ? <X className="w-5 h-5 text-zinc-600 dark:text-zinc-300" /> : <Menu className="w-5 h-5 text-zinc-600 dark:text-zinc-300" />}
      </button>

      {/* Mobile backdrop */}
      {mobileOpen && (
        <div
          className="fixed inset-0 bg-black/30 dark:bg-black/50 z-30 lg:hidden"
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`fixed top-0 left-0 h-screen w-60 bg-white dark:bg-zinc-900 border-r border-zinc-200 dark:border-zinc-700 flex flex-col z-40 transition-transform
          ${mobileOpen ? 'translate-x-0' : '-translate-x-full'} lg:translate-x-0`}
      >
        {sidebar}
      </aside>

      {/* Spacer for desktop layout */}
      <div className="hidden lg:block w-60 shrink-0" />
    </>
  )
}

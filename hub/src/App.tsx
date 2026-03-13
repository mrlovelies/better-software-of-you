import { useEffect, useCallback, useRef, useState } from 'react'
import { Routes, Route, useNavigate, useLocation } from 'react-router-dom'
import Sidebar from './components/Sidebar'
import ContentArea from './components/ContentArea'
import HubHome from './components/HubHome'
import ContactView from './components/ContactView'
import ProjectView from './components/ProjectView'
import AuditionsView from './components/AuditionsView'
import IncomeView from './components/IncomeView'
import NudgesView from './components/NudgesView'
import EmailView from './components/EmailView'
import CalendarView from './components/CalendarView'
import DecisionsView from './components/DecisionsView'
import JournalView from './components/JournalView'
import NotesView from './components/NotesView'
import WritingView from './components/WritingView'
import { useTheme } from './hooks/useTheme'
import type { NavigationData } from './types'

function PageRoute({ theme }: { theme: 'light' | 'dark' }) {
  const location = useLocation()
  // Extract filename from /pages/foo.html
  const filename = location.pathname.replace(/^\/pages\//, '')
  return <ContentArea currentPage={filename} theme={theme} />
}

function ContactRoute({ onNavigate }: { onNavigate: (path: string) => void }) {
  const location = useLocation()
  const id = parseInt(location.pathname.split('/').pop() || '0')
  return <ContactView contactId={id} onNavigate={(r) => onNavigate(routeToPath(r))} />
}

function ProjectRoute({ onNavigate }: { onNavigate: (path: string) => void }) {
  const location = useLocation()
  const id = parseInt(location.pathname.split('/').pop() || '0')
  return <ProjectView projectId={id} onNavigate={(r) => onNavigate(routeToPath(r))} />
}

// Convert legacy ContentRoute to URL path
import type { ContentRoute } from './types'
function routeToPath(r: ContentRoute): string {
  switch (r.type) {
    case 'home': return '/'
    case 'page': return `/pages/${r.filename}`
    case 'contact': return `/contacts/${r.id}`
    case 'project': return `/projects/${r.id}`
    case 'auditions': return '/auditions'
    case 'income': return '/income'
    case 'nudges': return '/nudges'
    case 'emails': return '/emails'
    case 'calendar': return '/calendar'
    case 'decisions': return '/decisions'
    case 'journal': return '/journal'
    case 'notes': return '/notes'
    case 'writing': return '/writing'
  }
}

export default function App() {
  const [nav, setNav] = useState<NavigationData | null>(null)
  const { theme, toggle } = useTheme()
  const navHash = useRef('')
  const navigate = useNavigate()
  const location = useLocation()

  const fetchNav = useCallback(() => {
    fetch('/api/navigation')
      .then(res => res.json())
      .then((data: NavigationData) => {
        const hash = JSON.stringify(data)
        if (hash !== navHash.current) {
          navHash.current = hash
          setNav(data)
        }
      })
      .catch(err => console.error('Failed to load navigation:', err))
  }, [])

  // Initial fetch + poll every 30s
  useEffect(() => {
    fetchNav()
    const interval = setInterval(fetchNav, 30000)
    return () => clearInterval(interval)
  }, [fetchNav])

  // Derive currentPage for sidebar active state from URL
  const path = location.pathname
  const simpleRoutes: Record<string, string> = {
    '/auditions': 'auditions', '/income': 'income', '/nudges': 'nudges',
    '/emails': 'emails', '/calendar': 'calendar', '/decisions': 'decisions',
    '/journal': 'journal', '/notes': 'notes',
    '/writing': 'writing',
  }
  const currentPage = path.startsWith('/pages/')
    ? path.replace(/^\/pages\//, '')
    : path.startsWith('/contacts/')
      ? `contact:${path.split('/').pop()}`
      : path.startsWith('/projects/')
        ? `project:${path.split('/').pop()}`
        : simpleRoutes[path] ?? null

  function handleNavigate(r: ContentRoute) {
    navigate(routeToPath(r))
    fetchNav()
  }

  function handlePathNavigate(p: string) {
    navigate(p)
    fetchNav()
  }

  return (
    <div className="flex min-h-screen bg-zinc-50 dark:bg-zinc-950 text-zinc-900 dark:text-zinc-100">
      <Sidebar nav={nav} currentPage={currentPage} onNavigate={handleNavigate} theme={theme} onToggleTheme={toggle} />
      <Routes>
        <Route path="/" element={<HubHome onNavigate={handleNavigate} />} />
        <Route path="/pages/:filename" element={<PageRoute theme={theme} />} />
        <Route path="/contacts/:id" element={<ContactRoute onNavigate={handlePathNavigate} />} />
        <Route path="/projects/:id" element={<ProjectRoute onNavigate={handlePathNavigate} />} />
        <Route path="/auditions" element={<AuditionsView />} />
        <Route path="/income" element={<IncomeView onNavigate={handleNavigate} />} />
        <Route path="/nudges" element={<NudgesView onNavigate={handleNavigate} />} />
        <Route path="/emails" element={<EmailView onNavigate={handleNavigate} />} />
        <Route path="/calendar" element={<CalendarView onNavigate={handleNavigate} />} />
        <Route path="/decisions" element={<DecisionsView onNavigate={handleNavigate} />} />
        <Route path="/journal" element={<JournalView />} />
        <Route path="/notes" element={<NotesView />} />
        <Route path="/writing" element={<WritingView />} />
        {/* Fallback — treat unknown paths as home */}
        <Route path="*" element={<HubHome onNavigate={handleNavigate} />} />
      </Routes>
    </div>
  )
}

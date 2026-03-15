export interface GeneratedView {
  id: number
  view_type: string
  entity_type: string | null
  entity_id: number | null
  entity_name: string | null
  filename: string
  parent_page_id: number | null
  parent_filename: string | null
  children?: GeneratedView[]
}

export interface NavContact {
  id: number
  name: string
  company: string | null
  role: string | null
  page_filename: string | null
}

export interface NavProject {
  id: number
  name: string
  status: string
  client_id: number | null
  client_name: string | null
  completion_pct: number
  total_tasks: number
  done_tasks: number
  page_filename: string | null
  children: GeneratedView[]
}

export interface NavigationData {
  modules: string[]
  badges: Record<string, number>
  views: GeneratedView[]
  urgent_count: number
  contacts: NavContact[]
  projects: NavProject[]
}

// Content routing — what to show in the main area
export type ContentRoute =
  | { type: 'home' }
  | { type: 'page'; filename: string }
  | { type: 'contact'; id: number }
  | { type: 'project'; id: number }
  | { type: 'auditions' }
  | { type: 'income' }
  | { type: 'nudges' }
  | { type: 'emails' }
  | { type: 'calendar' }
  | { type: 'decisions' }
  | { type: 'journal' }
  | { type: 'notes' }
  | { type: 'writing' }
  | { type: 'learning' }
  | { type: 'health' }

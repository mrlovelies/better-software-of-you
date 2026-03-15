import { useState, useEffect } from 'react'
import { DollarSign, TrendingUp, User, FolderOpen } from 'lucide-react'
import type { ContentRoute } from '../types'

interface IncomeData {
  summary: Array<{ total_records: number; total_gross: number; total_net: number; total_agent_fees: number; currency: string }>
  records: Array<{
    id: number; amount: number; currency: string; source: string; category: string
    description: string | null; received_date: string | null; net_amount: number | null
    agent_fee_amount: number | null; agent_fee_pct: number | null
    contact_name: string | null; project_name: string | null
    contact_id: number | null; project_id: number | null
  }>
  page_filename: string | null
}

function formatCurrency(amount: number, currency: string): string {
  return new Intl.NumberFormat('en-CA', { style: 'currency', currency }).format(amount)
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

const categoryLabels: Record<string, string> = {
  vo_commercial: 'VO Commercial',
  vo_narration: 'VO Narration',
  vo_animation: 'VO Animation',
  acting: 'Acting',
  freelance: 'Freelance',
  other: 'Other',
}

export default function IncomeView({ onNavigate }: { onNavigate: (r: ContentRoute) => void }) {
  const [data, setData] = useState<IncomeData | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/income/summary')
      .then(res => res.json())
      .then(d => { setData(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  if (loading) return <main className="flex-1 flex items-center justify-center min-h-screen"><p className="text-sm text-zinc-500 dark:text-zinc-400">Loading...</p></main>
  if (!data) return <main className="flex-1 flex items-center justify-center min-h-screen"><p className="text-sm text-zinc-500">Failed to load income data.</p></main>

  const s = data.summary[0]

  return (
    <main className="flex-1 min-h-screen p-6 lg:p-10">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center gap-3 mb-6">
          <DollarSign className="w-6 h-6 text-zinc-500" />
          <h1 className="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">Income</h1>
          {data.page_filename && (
            <button
              onClick={() => onNavigate({ type: 'page', filename: data.page_filename! })}
              className="text-xs text-blue-600 dark:text-blue-400 hover:underline ml-auto"
            >
              Full Report →
            </button>
          )}
        </div>

        {/* Summary cards */}
        {s && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-8">
            <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl p-4">
              <p className="text-xs text-zinc-500 dark:text-zinc-400 mb-1">Gross Income</p>
              <p className="text-xl font-semibold text-zinc-900 dark:text-zinc-100 tabular-nums">{formatCurrency(s.total_gross, s.currency)}</p>
            </div>
            <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl p-4">
              <p className="text-xs text-zinc-500 dark:text-zinc-400 mb-1">Net Income</p>
              <p className="text-xl font-semibold text-emerald-600 dark:text-emerald-400 tabular-nums">{formatCurrency(s.total_net, s.currency)}</p>
            </div>
            <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl p-4">
              <p className="text-xs text-zinc-500 dark:text-zinc-400 mb-1">Agent Fees</p>
              <p className="text-xl font-semibold text-zinc-900 dark:text-zinc-100 tabular-nums">{formatCurrency(s.total_agent_fees, s.currency)}</p>
            </div>
            <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl p-4">
              <p className="text-xs text-zinc-500 dark:text-zinc-400 mb-1">Records</p>
              <p className="text-xl font-semibold text-zinc-900 dark:text-zinc-100 tabular-nums flex items-center gap-2">
                {s.total_records}
                <TrendingUp className="w-5 h-5 text-emerald-500" />
              </p>
            </div>
          </div>
        )}

        {/* Records */}
        {data.records.length > 0 && (
          <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-xl">
            <div className="px-5 py-4 border-b border-zinc-100 dark:border-zinc-800">
              <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">All Records</h2>
            </div>
            <div className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {data.records.map(r => (
                <div key={r.id} className="px-5 py-4">
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-zinc-900 dark:text-zinc-100">{r.source}</p>
                      <div className="flex flex-wrap items-center gap-2 mt-1">
                        <span className="text-xs px-2 py-0.5 rounded-full bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 font-medium">
                          {categoryLabels[r.category] || r.category}
                        </span>
                        {r.description && <span className="text-xs text-zinc-500 dark:text-zinc-400">{r.description}</span>}
                      </div>
                      <div className="flex flex-wrap items-center gap-3 mt-1.5">
                        {r.contact_name && (
                          <button
                            onClick={() => r.contact_id && onNavigate({ type: 'contact', id: r.contact_id })}
                            className="text-xs text-blue-600 dark:text-blue-400 hover:underline flex items-center gap-1"
                          >
                            <User className="w-3 h-3" /> {r.contact_name}
                          </button>
                        )}
                        {r.project_name && (
                          <button
                            onClick={() => r.project_id && onNavigate({ type: 'project', id: r.project_id })}
                            className="text-xs text-blue-600 dark:text-blue-400 hover:underline flex items-center gap-1"
                          >
                            <FolderOpen className="w-3 h-3" /> {r.project_name}
                          </button>
                        )}
                      </div>
                    </div>
                    <div className="text-right shrink-0">
                      <p className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 tabular-nums">{formatCurrency(r.amount, r.currency)}</p>
                      {r.net_amount != null && r.net_amount !== r.amount && (
                        <p className="text-xs text-emerald-600 dark:text-emerald-400 tabular-nums">net {formatCurrency(r.net_amount, r.currency)}</p>
                      )}
                      {r.agent_fee_amount != null && r.agent_fee_amount > 0 && (
                        <p className="text-xs text-zinc-500 dark:text-zinc-400 tabular-nums">
                          agent: {formatCurrency(r.agent_fee_amount, r.currency)}
                          {r.agent_fee_pct ? ` (${r.agent_fee_pct}%)` : ''}
                        </p>
                      )}
                      {r.received_date && (
                        <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1">{formatDate(r.received_date)}</p>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </main>
  )
}

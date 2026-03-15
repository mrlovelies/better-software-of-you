import { useState, useEffect } from 'react'
import { Activity, Server, CheckCircle, AlertTriangle, XCircle, Clock, Wrench } from 'lucide-react'

interface HealthCheck {
  check_type: string
  machine: string
  status: string
  details: string | null
  auto_fixed: number
  last_check_at: string
  errors_24h: number
  warnings_24h: number
}

interface HealthSweep {
  id: number
  sweep_type: string
  machine: string
  total_checks: number
  passed: number
  warnings: number
  errors: number
  auto_fixed: number
  summary: string | null
  created_at: string
}

interface HealthData {
  latest_sweep: HealthSweep | null
  checks: HealthCheck[]
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
  return `${days}d ago`
}

const statusConfig: Record<string, { icon: React.ReactNode; color: string; bg: string }> = {
  ok: {
    icon: <CheckCircle className="w-4 h-4" />,
    color: 'text-emerald-600 dark:text-emerald-400',
    bg: 'bg-emerald-50 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-800',
  },
  warning: {
    icon: <AlertTriangle className="w-4 h-4" />,
    color: 'text-amber-600 dark:text-amber-400',
    bg: 'bg-amber-50 dark:bg-amber-900/20 border-amber-200 dark:border-amber-800',
  },
  error: {
    icon: <XCircle className="w-4 h-4" />,
    color: 'text-red-600 dark:text-red-400',
    bg: 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800',
  },
}

const machineNames: Record<string, string> = {
  razer: 'Razer Blade Pro',
  lucy: 'Lucy',
  macbook: 'MacBook Air',
  unknown: 'Unknown',
}

function MachineCard({ machine, checks }: { machine: string; checks: HealthCheck[] }) {
  const hasErrors = checks.some(c => c.status === 'error')
  const hasWarnings = checks.some(c => c.status === 'warning')
  const overallStatus = hasErrors ? 'error' : hasWarnings ? 'warning' : 'ok'
  const cfg = statusConfig[overallStatus]
  const latestCheck = checks.reduce((latest, c) =>
    !latest || c.last_check_at > latest.last_check_at ? c : latest, checks[0])

  return (
    <div className={`border rounded-xl p-4 ${cfg.bg}`}>
      <div className="flex items-center gap-2 mb-3">
        <Server className={`w-5 h-5 ${cfg.color}`} />
        <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          {machineNames[machine] || machine}
        </h3>
        <span className={`ml-auto flex items-center gap-1 text-[0.6875rem] font-medium ${cfg.color}`}>
          {cfg.icon}
          {overallStatus === 'ok' ? 'Healthy' : overallStatus === 'warning' ? 'Warning' : 'Error'}
        </span>
      </div>

      <div className="space-y-1.5">
        {checks.map(c => {
          const cCfg = statusConfig[c.status] || statusConfig.ok
          return (
            <div key={c.check_type} className="flex items-center gap-2 text-[0.8125rem]">
              <span className={cCfg.color}>{cCfg.icon}</span>
              <span className="text-zinc-700 dark:text-zinc-300 flex-1">{c.check_type.replace(/_/g, ' ')}</span>
              {c.auto_fixed > 0 && (
                <span className="flex items-center gap-0.5 text-[0.625rem] px-1.5 py-0.5 rounded-full bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400">
                  <Wrench className="w-3 h-3" /> fixed
                </span>
              )}
              {c.errors_24h > 0 && (
                <span className="text-[0.625rem] text-red-500 dark:text-red-400">
                  {c.errors_24h} err/24h
                </span>
              )}
            </div>
          )
        })}
      </div>

      {latestCheck && (
        <p className="text-[0.625rem] text-zinc-400 dark:text-zinc-500 mt-2">
          <Clock className="w-3 h-3 inline -mt-0.5 mr-0.5" />
          Last check: {timeAgo(latestCheck.last_check_at)}
        </p>
      )}
    </div>
  )
}

export default function HealthView() {
  const [data, setData] = useState<HealthData | null>(null)
  const [history, setHistory] = useState<HealthSweep[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      fetch('/api/health/status').then(r => r.json()),
      fetch('/api/health/history?days=7').then(r => r.json()),
    ])
      .then(([status, hist]) => {
        setData(status)
        setHistory(hist)
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <main className="flex-1 flex items-center justify-center min-h-screen">
        <p className="text-sm text-zinc-500 dark:text-zinc-400">Loading...</p>
      </main>
    )
  }

  if (!data || (data.checks.length === 0 && !data.latest_sweep)) {
    return (
      <main className="flex-1 min-h-screen p-6 lg:p-10">
        <div className="max-w-3xl mx-auto">
          <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-2 mb-6">
            <Activity className="w-5 h-5" /> Platform Health
          </h1>
          <div className="text-center py-16">
            <Activity className="w-10 h-10 text-zinc-300 dark:text-zinc-600 mx-auto mb-3" />
            <p className="text-sm text-zinc-500 dark:text-zinc-400">No health data yet.</p>
            <p className="text-xs text-zinc-400 dark:text-zinc-500 mt-1">Health checks run automatically on the Razer every 15 minutes.</p>
          </div>
        </div>
      </main>
    )
  }

  // Group checks by machine
  const byMachine: Record<string, HealthCheck[]> = {}
  for (const c of data.checks) {
    if (!byMachine[c.machine]) byMachine[c.machine] = []
    byMachine[c.machine].push(c)
  }

  return (
    <main className="flex-1 min-h-screen p-6 lg:p-10">
      <div className="max-w-3xl mx-auto">
        <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-2 mb-1">
          <Activity className="w-5 h-5" /> Platform Health
        </h1>
        {data.latest_sweep && (
          <p className="text-sm text-zinc-500 dark:text-zinc-400 mb-6">
            Last sweep: {data.latest_sweep.sweep_type} — {data.latest_sweep.summary}
            <span className="ml-2 text-zinc-400 dark:text-zinc-500">{timeAgo(data.latest_sweep.created_at)}</span>
          </p>
        )}

        {/* Machine status cards */}
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3 mb-8">
          {Object.entries(byMachine).map(([machine, checks]) => (
            <MachineCard key={machine} machine={machine} checks={checks} />
          ))}
        </div>

        {/* Sweep history timeline */}
        {history.length > 0 && (
          <div>
            <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 mb-3">Recent Sweeps</h2>
            <div className="space-y-2">
              {history.map(sweep => {
                const cfg = sweep.errors > 0 ? statusConfig.error : sweep.warnings > 0 ? statusConfig.warning : statusConfig.ok

                return (
                  <div key={sweep.id} className="flex items-center gap-3 bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-lg px-4 py-2.5">
                    <span className={cfg.color}>{cfg.icon}</span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
                          {sweep.sweep_type === 'sweep' ? 'Full Sweep' : 'Quick Check'}
                        </span>
                        <span className="text-[0.6875rem] text-zinc-500 dark:text-zinc-400">{sweep.machine}</span>
                      </div>
                      <p className="text-[0.75rem] text-zinc-500 dark:text-zinc-400">{sweep.summary}</p>
                    </div>
                    {sweep.auto_fixed > 0 && (
                      <span className="flex items-center gap-0.5 text-[0.625rem] px-1.5 py-0.5 rounded-full bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400">
                        <Wrench className="w-3 h-3" /> {sweep.auto_fixed} fixed
                      </span>
                    )}
                    <span className="text-[0.6875rem] text-zinc-400 dark:text-zinc-500 shrink-0">
                      {timeAgo(sweep.created_at)}
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>
    </main>
  )
}

import { useState, useEffect } from 'react';
import { api } from '../lib/api.js';
import StatCard from '../components/StatCard.jsx';

export default function Overview() {
  const [data, setData] = useState(null);

  useEffect(() => {
    api.getOverview().then(setData);
  }, []);

  if (!data) return <div className="animate-pulse text-stone-400">Loading pipeline data...</div>;

  const { funnel, pending_review, competitive, forecasts, triage_accuracy, top_subreddits, top_industries, last_run } = data;

  return (
    <div className="space-y-6">
      {/* Pipeline Funnel */}
      <section>
        <h2 className="text-lg font-semibold text-stone-900 mb-3">Pipeline Funnel</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
          <StatCard label="Harvested" value={funnel.harvested} />
          <StatCard label="Passed T1" value={funnel.passed_t1} sub={funnel.harvested > 0 ? `${Math.round(funnel.passed_t1 / funnel.harvested * 100)}%` : ''} />
          <StatCard label="Scored" value={funnel.scored} />
          <StatCard label="Approved" value={funnel.approved} color="green" />
          <StatCard label="Built" value={funnel.built} color="green" />
          <StatCard label="Shipped" value={funnel.shipped} color="green" />
          <StatCard label="Revenue" value={`$${funnel.revenue.toFixed(0)}`} color="green" />
        </div>
      </section>

      {/* Action Items */}
      <section>
        <h2 className="text-lg font-semibold text-stone-900 mb-3">Needs Attention</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <StatCard
            label="Signals to Review"
            value={pending_review}
            color={pending_review > 0 ? 'orange' : 'stone'}
            alert={pending_review > 0}
            sub={pending_review > 0 ? 'Go to Signals →' : 'All clear'}
          />
          <StatCard
            label="Competitive to Review"
            value={competitive.pending}
            color={competitive.pending > 0 ? 'orange' : 'stone'}
            alert={competitive.pending > 0}
            sub={`${competitive.targets} products tracked`}
          />
          <StatCard
            label="Forecast Ideas"
            value={forecasts.ideas}
            color="purple"
            sub={`${forecasts.approved} approved`}
          />
        </div>
      </section>

      {/* Triage Accuracy */}
      {triage_accuracy.total > 0 && (
        <section>
          <h2 className="text-lg font-semibold text-stone-900 mb-3">Triage Calibration</h2>
          <div className="bg-white rounded-lg border border-stone-200 p-4">
            <div className="flex items-center gap-4">
              <div className="text-3xl font-bold text-stone-900">
                {triage_accuracy.rate !== null ? `${Math.round(triage_accuracy.rate * 100)}%` : '—'}
              </div>
              <div>
                <div className="text-sm font-medium text-stone-700">LLM ↔ Human Agreement</div>
                <div className="text-xs text-stone-400">{triage_accuracy.correct}/{triage_accuracy.total} decisions aligned</div>
              </div>
            </div>
          </div>
        </section>
      )}

      {/* Top Subreddits + Industries */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <section>
          <h2 className="text-lg font-semibold text-stone-900 mb-3">Top Subreddits</h2>
          <div className="bg-white rounded-lg border border-stone-200 divide-y divide-stone-100">
            {top_subreddits.slice(0, 8).map(sub => (
              <div key={sub.subreddit} className="flex items-center justify-between px-4 py-2.5">
                <span className="text-sm text-stone-700">r/{sub.subreddit}</span>
                <div className="flex items-center gap-3 text-xs text-stone-400">
                  <span>{sub.signals_harvested} signals</span>
                  <span className={sub.yield_rate > 0 ? 'text-harvest-600 font-medium' : ''}>
                    {(sub.yield_rate * 100).toFixed(0)}% yield
                  </span>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-stone-900 mb-3">Industries</h2>
          <div className="bg-white rounded-lg border border-stone-200 divide-y divide-stone-100">
            {top_industries.slice(0, 8).map(ind => (
              <div key={ind.industry} className="flex items-center justify-between px-4 py-2.5">
                <span className="text-sm text-stone-700">{ind.industry}</span>
                <div className="flex items-center gap-3 text-xs text-stone-400">
                  <span>{ind.signals_found} found</span>
                  <span className="text-harvest-600 font-medium">{ind.signals_approved} approved</span>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>

      {/* Last Run */}
      {last_run && (
        <div className="text-xs text-stone-400 text-center">
          Last pipeline run: {new Date(last_run).toLocaleString()}
        </div>
      )}
    </div>
  );
}

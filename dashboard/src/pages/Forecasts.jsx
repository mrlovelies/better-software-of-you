import { useState, useEffect } from 'react';
import { api } from '../lib/api.js';
import ScoreBadge from '../components/ScoreBadge.jsx';

export default function Forecasts() {
  const [forecasts, setForecasts] = useState([]);
  const [filter, setFilter] = useState('idea');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api.getForecasts({ status: filter }).then(data => {
      setForecasts(data?.forecasts || []);
      setLoading(false);
    });
  }, [filter]);

  const handleApprove = async (id) => {
    await api.approveForecast(id);
    setForecasts(f => f.filter(x => x.id !== id));
  };

  const handleKill = async (id) => {
    const reason = prompt('Reason? (leave blank and the system will figure it out)');
    if (reason === null) return;
    await api.killForecast(id, reason?.trim() || undefined);
    setForecasts(f => f.filter(x => x.id !== id));
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-stone-900">Forecasts</h2>
        <div className="flex gap-2">
          {['idea', 'approved', 'building', 'shipped', 'killed'].map(s => (
            <button
              key={s}
              onClick={() => setFilter(s)}
              className={`px-3 py-1.5 text-xs font-medium rounded-md ${
                filter === s
                  ? 'bg-harvest-500 text-white'
                  : 'bg-white border border-stone-200 text-stone-600 hover:bg-stone-50'
              }`}
            >
              {s.charAt(0).toUpperCase() + s.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="animate-pulse text-stone-400">Loading...</div>
      ) : forecasts.length === 0 ? (
        <div className="bg-white rounded-lg border border-stone-200 p-8 text-center text-stone-400">
          No {filter} forecasts.
        </div>
      ) : (
        <div className="space-y-3">
          {forecasts.map(f => {
            let autonomy = null;
            try { autonomy = typeof f.autonomy_breakdown === 'string' ? JSON.parse(f.autonomy_breakdown) : f.autonomy_breakdown; } catch {}

            return (
              <div key={f.id} className="bg-white rounded-lg border border-stone-200 p-4">
                <div className="flex items-start gap-3">
                  <div className="flex flex-col items-center gap-1">
                    <ScoreBadge score={f.composite_score || 0} size="md" />
                    <span className="text-[10px] text-stone-400">composite</span>
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <h3 className="font-semibold text-stone-900 text-sm">{f.title}</h3>
                      <span className="text-[10px] bg-purple-50 text-purple-600 px-1.5 py-0.5 rounded">
                        {f.origin_type}
                      </span>
                      <span className="text-[10px] bg-stone-100 text-stone-500 px-1.5 py-0.5 rounded">
                        {f.build_type}
                      </span>
                      {f.soy_leaf_fit_score >= 7 && (
                        <span className="text-[10px] bg-harvest-50 text-harvest-600 px-1.5 py-0.5 rounded font-medium">
                          SoY Leaf
                        </span>
                      )}
                      {f.requires_physical === 1 && (
                        <span className="text-[10px] bg-amber-50 text-amber-700 px-1.5 py-0.5 rounded font-medium">
                          📦 Physical
                        </span>
                      )}
                    </div>

                    <p className="text-sm text-stone-600 mb-2">{f.description}</p>

                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-stone-400">
                      <span>Autonomy: <strong className="text-stone-600">{f.autonomy_score}/10</strong></span>
                      <span>Revenue: <strong className="text-stone-600">{f.revenue_model}</strong></span>
                      <span>Build: <strong className="text-stone-600">~{f.estimated_build_days}d</strong></span>
                      <span>MRR: <strong className="text-stone-600">${f.estimated_mrr_low || 0}-${f.estimated_mrr_high || 0}</strong></span>
                      {f.industry && <span>Industry: {f.industry}</span>}
                    </div>

                    {autonomy && (
                      <div className="flex gap-3 mt-2 text-[10px] text-stone-400">
                        <span>Setup: {autonomy.setup}/10</span>
                        <span>Ops: {autonomy.operation}/10</span>
                        <span>Support: {autonomy.support}/10</span>
                        <span>Maint: {autonomy.maintenance}/10</span>
                      </div>
                    )}

                    {f.requires_physical === 1 && f.physical_complexity_notes && (
                      <div className="text-xs text-amber-600 mt-2 bg-amber-50 rounded px-2 py-1">
                        📦 Physical: {f.physical_complexity_notes}
                      </div>
                    )}

                    {(() => {
                      let strategy = null;
                      try { strategy = typeof f.monetization_strategy === 'string' ? JSON.parse(f.monetization_strategy) : f.monetization_strategy; } catch {}
                      if (!strategy) return null;
                      return (
                        <div className="mt-3 border-t border-stone-100 pt-3">
                          <div className="text-xs font-semibold text-stone-500 mb-1.5">Monetization Plan</div>
                          <div className="space-y-1.5">
                            {(strategy.channels || []).map((ch, i) => (
                              <div key={i} className="text-xs flex items-start gap-2">
                                <span className="text-harvest-600 font-medium shrink-0">{ch.name}:</span>
                                <span className="text-stone-500">{ch.pricing} — {ch.estimated_monthly}</span>
                                <span className={`text-[10px] px-1 py-0.5 rounded shrink-0 ${
                                  ch.effort_to_launch === 'low' ? 'bg-green-50 text-green-600'
                                  : ch.effort_to_launch === 'high' ? 'bg-red-50 text-red-600'
                                  : 'bg-stone-50 text-stone-500'
                                }`}>{ch.effort_to_launch}</span>
                              </div>
                            ))}
                          </div>
                          {strategy.path_to_mrr && (
                            <p className="text-[11px] text-stone-400 mt-2 leading-relaxed">{strategy.path_to_mrr}</p>
                          )}
                          {strategy.key_assumption && (
                            <p className="text-[11px] text-orange-500 mt-1">Key assumption: {strategy.key_assumption}</p>
                          )}
                        </div>
                      );
                    })()}

                    {f.origin_reasoning && (
                      <p className="text-xs text-stone-400 italic mt-2">{f.origin_reasoning}</p>
                    )}
                  </div>

                  {filter === 'idea' && (
                    <div className="flex flex-col gap-1.5 shrink-0">
                      <button onClick={() => handleApprove(f.id)}
                        className="px-3 py-1 text-xs font-medium rounded bg-harvest-500 text-white hover:bg-harvest-600">
                        Approve
                      </button>
                      <button onClick={() => handleKill(f.id)}
                        className="px-3 py-1 text-xs font-medium rounded bg-stone-200 text-stone-600 hover:bg-stone-300">
                        Kill
                      </button>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

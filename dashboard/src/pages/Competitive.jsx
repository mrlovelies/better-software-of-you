import { useState, useEffect } from 'react';
import { api } from '../lib/api.js';
import SignalCard from '../components/SignalCard.jsx';

export default function Competitive() {
  const [signals, setSignals] = useState([]);
  const [targets, setTargets] = useState([]);
  const [filter, setFilter] = useState('pending');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getTargets().then(data => setTargets(data?.targets || []));
  }, []);

  useEffect(() => {
    setLoading(true);
    api.getCompetitive({ status: filter, limit: 50 }).then(data => {
      setSignals(data?.signals || []);
      setLoading(false);
    });
  }, [filter]);

  return (
    <div className="space-y-6">
      {/* Targets board */}
      <section>
        <h2 className="text-lg font-semibold text-stone-900 mb-3">Competitive Targets</h2>
        {targets.length === 0 ? (
          <p className="text-sm text-stone-400">No targets tracked yet.</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {targets.map(t => (
              <div key={t.id} className="bg-white rounded-lg border border-stone-200 p-4">
                <div className="flex items-center justify-between mb-1">
                  <h3 className="font-semibold text-stone-900 text-sm">{t.product_name}</h3>
                  {t.avg_composite && (
                    <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${
                      t.avg_composite >= 7 ? 'bg-red-100 text-red-700'
                      : t.avg_composite >= 5 ? 'bg-orange-100 text-orange-700'
                      : 'bg-stone-100 text-stone-600'
                    }`}>
                      {t.avg_composite.toFixed(1)}
                    </span>
                  )}
                </div>
                <div className="text-xs text-stone-400">
                  {t.company && <span>{t.company} · </span>}
                  {t.category && <span>{t.category} · </span>}
                  <span>{t.signal_count} complaints</span>
                </div>
                {t.avg_sentiment && (
                  <div className="mt-2 text-xs">
                    <span className="text-stone-400">Anger: </span>
                    <span className={t.avg_sentiment >= 8 ? 'text-red-600 font-medium' : 'text-stone-600'}>
                      {t.avg_sentiment.toFixed(1)}/10
                    </span>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Signals list */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-stone-900">Competitive Signals</h2>
          <div className="flex gap-2">
            {['pending', 'opportunity', 'rejected'].map(s => (
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
        ) : signals.length === 0 ? (
          <div className="bg-white rounded-lg border border-stone-200 p-8 text-center text-stone-400">
            No {filter} competitive signals.
          </div>
        ) : (
          <div className="space-y-3">
            {signals.map(signal => (
              <SignalCard
                key={signal.id}
                signal={signal}
                type="competitive"
                onApprove={api.approveCompetitive}
                onReject={api.rejectCompetitive}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

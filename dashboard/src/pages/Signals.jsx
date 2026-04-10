import { useState, useEffect } from 'react';
import { api } from '../lib/api.js';
import SignalCard from '../components/SignalCard.jsx';

export default function Signals() {
  const [signals, setSignals] = useState([]);
  const [total, setTotal] = useState(0);
  const [filter, setFilter] = useState('pending');
  const [industries, setIndustries] = useState([]);
  const [industry, setIndustry] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getIndustries().then(setIndustries);
  }, []);

  useEffect(() => {
    setLoading(true);
    const params = { status: filter, limit: 50 };
    if (industry) params.industry = industry;
    api.getSignals(params).then(data => {
      setSignals(data?.signals || []);
      setTotal(data?.total || 0);
      setLoading(false);
    });
  }, [filter, industry]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-stone-900">
          Signals <span className="text-stone-400 font-normal">({total})</span>
        </h2>
        <div className="flex gap-2">
          {['pending', 'approved', 'rejected'].map(s => (
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
          <select
            value={industry}
            onChange={e => setIndustry(e.target.value)}
            className="text-xs border border-stone-200 rounded-md px-2 py-1.5 bg-white text-stone-600"
          >
            <option value="">All industries</option>
            {industries.map(i => <option key={i} value={i}>{i}</option>)}
          </select>
        </div>
      </div>

      {loading ? (
        <div className="animate-pulse text-stone-400">Loading signals...</div>
      ) : signals.length === 0 ? (
        <div className="bg-white rounded-lg border border-stone-200 p-8 text-center text-stone-400">
          {filter === 'pending' ? 'No signals awaiting review. Pipeline is running — check back later.' : `No ${filter} signals.`}
        </div>
      ) : (
        <div className="space-y-3">
          {signals.map(signal => (
            <SignalCard
              key={signal.id}
              signal={signal}
              onApprove={api.approveSignal}
              onReject={api.rejectSignal}
              onDefer={filter === 'pending' ? api.deferSignal : undefined}
            />
          ))}
        </div>
      )}
    </div>
  );
}

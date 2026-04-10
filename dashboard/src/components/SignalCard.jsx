import { useState } from 'react';
import ScoreBadge from './ScoreBadge.jsx';

export default function SignalCard({ signal, onApprove, onReject, onDefer, type = 'signal' }) {
  const [acting, setActing] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  if (dismissed) return null;

  const handleAction = async (action, { askReason = false, promptLabel = 'Reason (optional):' } = {}) => {
    let input = null;
    if (askReason) {
      input = prompt(promptLabel);
      if (input === null) return; // cancelled
    }
    setActing(true);
    await action(signal.id, input?.trim() || undefined);
    setDismissed(true);
  };

  const isCompetitive = type === 'competitive';

  return (
    <div className="bg-white rounded-lg border border-stone-200 p-4 hover:border-stone-300 transition-colors">
      <div className="flex items-start gap-3">
        {/* Score */}
        <ScoreBadge score={signal.composite_score || 0} size="md" />

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 text-xs text-stone-400 mb-1">
            {signal.subreddit && <span>r/{signal.subreddit}</span>}
            {signal.industry && <span className="bg-stone-100 px-1.5 py-0.5 rounded">{signal.industry}</span>}
            {isCompetitive && signal.target_product && (
              <span className="bg-red-50 text-red-600 px-1.5 py-0.5 rounded font-medium">{signal.target_product}</span>
            )}
            {signal.soy_leaf_fit_score >= 7 && (
              <span className="bg-harvest-50 text-harvest-600 px-1.5 py-0.5 rounded font-medium">SoY Leaf</span>
            )}
            {signal.upvotes > 0 && <span>{signal.upvotes}↑</span>}
            {signal.comment_count > 0 && <span>{signal.comment_count}💬</span>}
          </div>

          <p className="text-sm text-stone-900 font-medium leading-snug">
            {isCompetitive ? signal.complaint_summary : (signal.extracted_pain || signal.raw_text?.slice(0, 200))}
          </p>

          {/* Score breakdown */}
          <div className="flex gap-3 mt-2">
            {signal.market_size_score && (
              <span className="text-xs text-stone-400">Mkt: {signal.market_size_score}</span>
            )}
            {signal.monetization_score && (
              <span className="text-xs text-stone-400">$$: {signal.monetization_score}</span>
            )}
            {signal.existing_solutions_score && (
              <span className="text-xs text-stone-400">Gap: {signal.existing_solutions_score}</span>
            )}
            {signal.switchability_score && (
              <span className="text-xs text-stone-400">Switch: {signal.switchability_score}</span>
            )}
            {signal.build_advantage_score && (
              <span className="text-xs text-stone-400">Adv: {signal.build_advantage_score}</span>
            )}
          </div>

          {signal.source_url && (
            <a href={signal.source_url} target="_blank" rel="noopener"
               className="text-xs text-harvest-600 hover:underline mt-1 inline-block">
              View source →
            </a>
          )}
        </div>

        {/* Actions */}
        <div className="flex flex-col gap-1.5 shrink-0">
          <button
            onClick={() => handleAction(onApprove, { promptLabel: 'Notes (optional):' })}
            disabled={acting}
            className="px-3 py-1 text-xs font-medium rounded bg-harvest-500 text-white hover:bg-harvest-600 disabled:opacity-50"
          >
            Approve
          </button>
          <button
            onClick={() => handleAction(onReject, { askReason: true, promptLabel: 'Reason? (leave blank and the system will figure it out)' })}
            disabled={acting}
            className="px-3 py-1 text-xs font-medium rounded bg-stone-200 text-stone-600 hover:bg-stone-300 disabled:opacity-50"
          >
            Reject
          </button>
          {onDefer && (
            <button
              onClick={() => handleAction(onDefer, { promptLabel: 'Notes (optional):' })}
              disabled={acting}
              className="px-3 py-1 text-xs font-medium rounded bg-stone-100 text-stone-400 hover:bg-stone-200 disabled:opacity-50"
            >
              Defer
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

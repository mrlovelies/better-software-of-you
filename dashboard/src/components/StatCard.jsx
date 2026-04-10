export default function StatCard({ label, value, sub, color = 'stone', alert }) {
  const colors = {
    stone: 'text-stone-900',
    green: 'text-harvest-600',
    orange: 'text-orange-600',
    red: 'text-red-600',
    purple: 'text-purple-600',
  };

  return (
    <div className={`bg-white rounded-lg border p-4 ${alert ? 'border-orange-300 bg-orange-50/30' : 'border-stone-200'}`}>
      <div className="text-xs font-medium text-stone-500 uppercase tracking-wide">{label}</div>
      <div className={`text-2xl font-bold mt-1 ${colors[color] || colors.stone}`}>{value}</div>
      {sub && <div className="text-xs text-stone-400 mt-0.5">{sub}</div>}
    </div>
  );
}

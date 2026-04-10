export default function ScoreBadge({ score, label, size = 'sm' }) {
  const num = parseFloat(score);
  const color = num >= 7 ? 'bg-harvest-500 text-white'
    : num >= 5 ? 'bg-orange-500 text-white'
    : 'bg-stone-400 text-white';

  const sizes = {
    sm: 'w-8 h-8 text-xs',
    md: 'w-10 h-10 text-sm',
    lg: 'w-12 h-12 text-base',
  };

  return (
    <div className="flex flex-col items-center gap-0.5">
      <div className={`${sizes[size]} ${color} rounded-full flex items-center justify-center font-bold`}>
        {num.toFixed(1)}
      </div>
      {label && <span className="text-[10px] text-stone-400">{label}</span>}
    </div>
  );
}

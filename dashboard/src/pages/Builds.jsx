import { useState, useEffect } from 'react';
import { api } from '../lib/api.js';

function ProgressBar({ percent, color = 'harvest' }) {
  const colors = { harvest: 'bg-harvest-500', orange: 'bg-orange-500', red: 'bg-red-500' };
  return (
    <div className="w-full bg-stone-200 rounded-full h-2">
      <div
        className={`${colors[color] || colors.harvest} h-2 rounded-full transition-all duration-500`}
        style={{ width: `${Math.min(100, percent)}%` }}
      />
    </div>
  );
}

function StatusBadge({ status, isActive }) {
  const styles = {
    prepared: 'bg-stone-100 text-stone-600',
    building: isActive ? 'bg-blue-100 text-blue-700 animate-pulse' : 'bg-orange-100 text-orange-700',
    success: 'bg-harvest-100 text-harvest-700',
    error: 'bg-red-100 text-red-700',
    blocked: 'bg-orange-100 text-orange-700',
  };

  const labels = {
    prepared: 'Prepared',
    building: isActive ? 'Building...' : 'Stalled',
    success: 'Complete',
    error: 'Failed',
    blocked: 'Blocked',
  };

  return (
    <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${styles[status] || styles.prepared}`}>
      {labels[status] || status}
    </span>
  );
}

function BuildCard({ build, onSelect }) {
  const progress = build.gsdState?.progress || 0;
  const sliceInfo = build.gsdState
    ? `${build.gsdState.doneSlices}/${build.gsdState.totalSlices} slices`
    : '';

  return (
    <div
      onClick={() => onSelect(build.id)}
      className="bg-white rounded-lg border border-stone-200 p-4 hover:border-harvest-300 cursor-pointer transition-colors"
    >
      <div className="flex items-start justify-between mb-2">
        <div>
          <h3 className="font-semibold text-stone-900">
            {build.source_type === 'forecast' ? `Forecast #${build.source_id}` : `Signal #${build.source_id}`}
          </h3>
          {build.variantLabel && (
            <span className="inline-block text-xs font-medium px-2 py-0.5 rounded bg-indigo-100 text-indigo-700 mt-0.5">
              {build.variantLabel}
            </span>
          )}
          <p className="text-xs text-stone-400">{build.id}</p>
        </div>
        <StatusBadge status={build.status} isActive={build.isActive} />
      </div>

      {build.gsdState && (
        <div className="mb-2">
          <div className="flex justify-between text-xs text-stone-500 mb-1">
            <span>{sliceInfo}</span>
            <span>{progress}%</span>
          </div>
          <ProgressBar percent={progress} color={build.isActive ? 'harvest' : 'stone'} />
        </div>
      )}

      <div className="flex gap-4 text-xs text-stone-400">
        <span>{build.sourceFiles || 0} files</span>
        {build.cost?.total && <span>${build.cost.total.toFixed(2)}</span>}
        {build.deploy_url && (
          <a href={build.deploy_url} target="_blank" rel="noopener"
             className="text-harvest-600 hover:underline"
             onClick={e => e.stopPropagation()}>
            View deploy →
          </a>
        )}
      </div>
    </div>
  );
}

function BuildDetail({ buildId, onBack }) {
  const [build, setBuild] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchBuild = () => {
      api.getBuild(buildId).then(data => {
        setBuild(data);
        setLoading(false);
      });
    };
    fetchBuild();
    // Poll while building
    const interval = setInterval(fetchBuild, 10000);
    return () => clearInterval(interval);
  }, [buildId]);

  if (loading) return <div className="animate-pulse text-stone-400">Loading build...</div>;
  if (!build) return <div className="text-stone-400">Build not found.</div>;

  const progress = build.gsdState?.progress || 0;

  return (
    <div className="space-y-4">
      <button onClick={onBack} className="text-sm text-stone-500 hover:text-stone-700">
        ← Back to builds
      </button>

      {/* Header */}
      <div className="bg-white rounded-lg border border-stone-200 p-5">
        <div className="flex items-start justify-between mb-3">
          <div>
            <h2 className="text-lg font-bold text-stone-900">
              {build.source_type === 'forecast' ? `Forecast #${build.source_id}` : `Signal #${build.source_id}`}
            </h2>
            {build.variant && (
              <span className="inline-block text-sm font-medium px-2 py-0.5 rounded bg-indigo-100 text-indigo-700 mt-1">
                {build.variantLabel || build.variant}
              </span>
            )}
            <p className="text-sm text-stone-400">{buildId}</p>
          </div>
          <StatusBadge status={build.status} isActive={build.isActive} />
        </div>

        {build.gsdState && (
          <div className="mb-3">
            <div className="flex justify-between text-sm text-stone-500 mb-1">
              <span>{build.gsdState.doneSlices}/{build.gsdState.totalSlices} slices complete</span>
              <span>{progress}%</span>
            </div>
            <ProgressBar percent={progress} />
          </div>
        )}

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
          <div>
            <div className="text-xs text-stone-400">Source Files</div>
            <div className="font-semibold text-stone-900">{build.sourceFiles?.length || 0}</div>
          </div>
          <div>
            <div className="text-xs text-stone-400">Status</div>
            <div className="font-semibold text-stone-900">{build.status}</div>
          </div>
          <div>
            <div className="text-xs text-stone-400">Started</div>
            <div className="font-semibold text-stone-900">
              {build.build_started_at ? new Date(build.build_started_at).toLocaleTimeString() : '—'}
            </div>
          </div>
          <div>
            <div className="text-xs text-stone-400">Cost</div>
            <div className="font-semibold text-stone-900">
              {build.cost?.total ? `$${build.cost.total.toFixed(2)}` : '—'}
            </div>
          </div>
        </div>

        {build.deploy_url && (
          <div className="mt-3 p-2 bg-harvest-50 rounded text-sm">
            Deployed: <a href={build.deploy_url} target="_blank" rel="noopener"
                         className="text-harvest-600 font-medium hover:underline">{build.deploy_url}</a>
          </div>
        )}
      </div>

      {/* Source Files */}
      {build.sourceFiles?.length > 0 && (
        <div className="bg-white rounded-lg border border-stone-200 p-4">
          <h3 className="font-semibold text-stone-900 mb-2">Source Files ({build.sourceFiles.length})</h3>
          <div className="max-h-48 overflow-y-auto">
            {build.sourceFiles.map((f, i) => (
              <div key={i} className="text-xs text-stone-500 font-mono py-0.5">{f}</div>
            ))}
          </div>
        </div>
      )}

      {/* Recent Activity */}
      {build.recentActivity?.length > 0 && (
        <div className="bg-white rounded-lg border border-stone-200 p-4">
          <h3 className="font-semibold text-stone-900 mb-2">Recent Activity</h3>
          <div className="space-y-2 max-h-64 overflow-y-auto">
            {build.recentActivity.map((a, i) => (
              <div key={i} className="text-sm text-stone-600">
                {a.type === 'turn_end' ? (
                  <span className="text-harvest-600 font-medium">Turn completed</span>
                ) : (
                  <span className="text-stone-500">{a.preview}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Services */}
      {build.credentials?.length > 0 && (
        <div className="bg-white rounded-lg border border-stone-200 p-4">
          <h3 className="font-semibold text-stone-900 mb-2">Provisioned Services</h3>
          {build.credentials.map((c, i) => (
            <div key={i} className="flex items-center gap-2 text-sm py-1">
              <span className="text-stone-400">{c.service}</span>
              <span className="text-stone-600 font-medium">{c.key}</span>
            </div>
          ))}
        </div>
      )}

      {/* Roadmap */}
      {build.roadmap && (
        <div className="bg-white rounded-lg border border-stone-200 p-4">
          <h3 className="font-semibold text-stone-900 mb-2">Build Roadmap</h3>
          <div className="prose prose-sm prose-stone max-h-96 overflow-y-auto">
            <pre className="text-xs whitespace-pre-wrap text-stone-600">{build.roadmap}</pre>
          </div>
        </div>
      )}
    </div>
  );
}

export default function Builds() {
  const [builds, setBuilds] = useState([]);
  const [selectedBuild, setSelectedBuild] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchBuilds = () => {
      api.getBuilds().then(data => {
        setBuilds(data?.builds || []);
        setLoading(false);
      });
    };
    fetchBuilds();
    const interval = setInterval(fetchBuilds, 15000);
    return () => clearInterval(interval);
  }, []);

  if (selectedBuild) {
    return <BuildDetail buildId={selectedBuild} onBack={() => setSelectedBuild(null)} />;
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-stone-900">
        Builds <span className="text-stone-400 font-normal">({builds.length})</span>
      </h2>

      {loading ? (
        <div className="animate-pulse text-stone-400">Loading builds...</div>
      ) : builds.length === 0 ? (
        <div className="bg-white rounded-lg border border-stone-200 p-8 text-center text-stone-400">
          No builds yet. Approve a signal or forecast to trigger a build.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {builds.map(build => (
            <BuildCard key={build.id} build={build} onSelect={setSelectedBuild} />
          ))}
        </div>
      )}
    </div>
  );
}

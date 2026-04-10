import { Outlet, NavLink, Navigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth.jsx';
import { usePendingCounts } from '../hooks/usePendingCounts.jsx';

export default function Layout() {
  const { user, loading, logout } = useAuth();
  const { counts } = usePendingCounts();

  const NAV_ITEMS = [
    { path: '/', label: 'Overview', icon: '📊', count: 0 },
    { path: '/signals', label: 'Signals', icon: '🔍', count: counts.signals },
    { path: '/competitive', label: 'Competitive', icon: '⚔️', count: counts.competitive },
    { path: '/forecasts', label: 'Forecasts', icon: '💡', count: counts.forecasts },
    { path: '/builds', label: 'Builds', icon: '🔨', count: counts.builds },
  ];

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-stone-50">
        <div className="animate-pulse text-stone-400">Loading...</div>
      </div>
    );
  }

  if (!user) return <Navigate to="/login" />;

  return (
    <div className="min-h-screen bg-stone-50">
      {/* Header */}
      <header className="bg-harvest-500 text-white shadow-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-14">
            <div className="flex items-center gap-6">
              <h1 className="text-lg font-bold tracking-tight">
                🌿 Harvest Dashboard
              </h1>
              <nav className="hidden md:flex gap-1">
                {NAV_ITEMS.map(item => (
                  <NavLink
                    key={item.path}
                    to={item.path}
                    end={item.path === '/'}
                    className={({ isActive }) =>
                      `px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                        isActive
                          ? 'bg-white/20 text-white'
                          : 'text-white/70 hover:text-white hover:bg-white/10'
                      }`
                    }
                  >
                    <span className="mr-1.5">{item.icon}</span>
                    {item.label}
                    {item.count > 0 && (
                      <span className="ml-1.5 bg-white/30 text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full">
                        {item.count}
                      </span>
                    )}
                  </NavLink>
                ))}
              </nav>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-sm text-white/70">{user.name}</span>
              {user.picture && (
                <img src={user.picture} alt="" className="w-7 h-7 rounded-full" />
              )}
              <button
                onClick={logout}
                className="text-xs text-white/50 hover:text-white"
              >
                Logout
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* Mobile nav */}
      <nav className="md:hidden bg-white border-b border-stone-200 px-4 py-2 flex gap-2 overflow-x-auto">
        {NAV_ITEMS.map(item => (
          <NavLink
            key={item.path}
            to={item.path}
            end={item.path === '/'}
            className={({ isActive }) =>
              `px-3 py-1.5 rounded-md text-sm font-medium whitespace-nowrap ${
                isActive
                  ? 'bg-harvest-50 text-harvest-700'
                  : 'text-stone-500 hover:bg-stone-100'
              }`
            }
          >
            <span className="mr-1">{item.icon}</span>
            {item.label}
          </NavLink>
        ))}
      </nav>

      {/* Content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <Outlet />
      </main>
    </div>
  );
}

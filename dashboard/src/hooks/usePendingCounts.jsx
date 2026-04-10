import { createContext, useContext, useState, useEffect } from 'react';
import { api } from '../lib/api.js';

const PendingContext = createContext({});

export function PendingProvider({ children }) {
  const [counts, setCounts] = useState({ signals: 0, competitive: 0, forecasts: 0, builds: 0 });

  const refresh = () => {
    api.getOverview().then(data => {
      if (data) {
        setCounts(prev => ({
          signals: data.pending_review || 0,
          competitive: data.competitive?.pending || 0,
          forecasts: data.forecasts?.ideas || 0,
          builds: prev.builds,
        }));
      }
    });
    api.getBuilds().then(data => {
      if (data?.builds) {
        const active = data.builds.filter(b => b.isActive || b.status === 'building').length;
        setCounts(prev => ({ ...prev, builds: active }));
      }
    });
  };

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 60000); // refresh every minute
    return () => clearInterval(interval);
  }, []);

  return (
    <PendingContext.Provider value={{ counts, refresh }}>
      {children}
    </PendingContext.Provider>
  );
}

export function usePendingCounts() {
  return useContext(PendingContext);
}

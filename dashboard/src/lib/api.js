const API = '/api';

async function fetchApi(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (res.status === 401) {
    if (!window.location.pathname.startsWith('/login')) {
      window.location.href = '/login';
    }
    return null;
  }
  return res.json();
}

export const api = {
  // Auth
  getUser: () => fetchApi('/auth/me'),
  login: () => fetchApi('/auth/login'),
  logout: () => fetchApi('/auth/logout', { method: 'POST' }),
  invite: (email, role) => fetchApi('/auth/invite', {
    method: 'POST', body: JSON.stringify({ email, role }),
  }),

  // Pipeline
  getOverview: () => fetchApi('/pipeline/overview'),

  // Signals
  getSignals: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return fetchApi(`/signals?${qs}`);
  },
  getIndustries: () => fetchApi('/signals/industries'),
  approveSignal: (id, notes) => fetchApi(`/signals/${id}/approve`, {
    method: 'POST', body: JSON.stringify({ notes }),
  }),
  rejectSignal: (id, reason) => fetchApi(`/signals/${id}/reject`, {
    method: 'POST', body: JSON.stringify({ reason }),
  }),
  deferSignal: (id, notes) => fetchApi(`/signals/${id}/defer`, {
    method: 'POST', body: JSON.stringify({ notes }),
  }),

  // Competitive
  getCompetitive: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return fetchApi(`/competitive?${qs}`);
  },
  getTargets: () => fetchApi('/competitive/targets'),
  approveCompetitive: (id, notes) => fetchApi(`/competitive/${id}/approve`, {
    method: 'POST', body: JSON.stringify({ notes }),
  }),
  rejectCompetitive: (id, reason) => fetchApi(`/competitive/${id}/reject`, {
    method: 'POST', body: JSON.stringify({ reason }),
  }),

  // Builds
  getBuilds: () => fetchApi('/builds'),
  getBuild: (id) => fetchApi(`/builds/${id}`),

  // Forecasts
  getForecasts: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return fetchApi(`/forecasts?${qs}`);
  },
  approveForecast: (id, notes) => fetchApi(`/forecasts/${id}/approve`, {
    method: 'POST', body: JSON.stringify({ notes }),
  }),
  killForecast: (id, reason) => fetchApi(`/forecasts/${id}/kill`, {
    method: 'POST', body: JSON.stringify({ reason }),
  }),
};

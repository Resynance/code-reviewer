// Thin client for the ReviewBot REST API. All calls are same-origin in
// production (FastAPI serves this bundle) and proxied to :1500 in dev.

async function request(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })

  if (!res.ok) {
    // FastAPI returns errors as { detail: "..." }.
    let message = `Request failed (${res.status})`
    try {
      const data = await res.json()
      if (data && data.detail) message = data.detail
    } catch {
      // Non-JSON error body — keep the status-based message.
    }
    throw new Error(message)
  }

  if (res.status === 204) return null
  return res.json()
}

export const api = {
  review: (body) => request('/api/review', { method: 'POST', body }),

  listDecisions: (k = 20, repo = '') =>
    request(`/api/decisions?k=${encodeURIComponent(k)}${repo ? `&repo=${encodeURIComponent(repo)}` : ''}`),

  searchDecisions: (query, k = 10, repo = '') =>
    request('/api/decisions/search', { method: 'POST', body: { query, k, repo: repo || undefined } }),

  addDecision: (decision) =>
    request('/api/decisions', { method: 'POST', body: decision }),

  deleteDecision: (docId) =>
    request(`/api/decisions/${encodeURIComponent(docId)}`, { method: 'DELETE' }),

  stats: () => request('/api/stats'),

  balance: () => request('/api/balance'),

  getSettings: () => request('/api/settings'),

  saveSettings: (body) => request('/api/settings', { method: 'PUT', body }),

  listRepos: () => request('/api/repos'),

  addRepo: (repo) => request('/api/repos', { method: 'POST', body: { repo } }),

  removeRepo: (repo) =>
    request(`/api/repos?repo=${encodeURIComponent(repo)}`, { method: 'DELETE' }),

  backfill: (repo, pages) =>
    request('/api/backfill', { method: 'POST', body: { repo, pages } }),

  openPrs: (repo) =>
    request(`/api/repos/open-prs?repo=${encodeURIComponent(repo)}`),
}

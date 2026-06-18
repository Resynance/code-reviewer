// Thin client for the ReviewBot REST API. All calls are same-origin in
// production (FastAPI serves this bundle) and proxied to :1500 in dev.

import { accessToken } from './auth.js'

async function request(path, { method = 'GET', body } = {}) {
  const headers = {}
  if (body) headers['Content-Type'] = 'application/json'
  const token = accessToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(path, {
    method,
    headers: Object.keys(headers).length ? headers : undefined,
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

  githubOwners: () => request('/api/github/owners'),

  githubRepos: (owner, type = 'org') =>
    request(`/api/github/repos?owner=${encodeURIComponent(owner)}&type=${encodeURIComponent(type)}`),

  openPrs: (repo) =>
    request(`/api/repos/open-prs?repo=${encodeURIComponent(repo)}`),

  repoPrs: (repo) =>
    request(`/api/repos/prs?repo=${encodeURIComponent(repo)}`),

  repoPr: (repo, number) =>
    request(`/api/repos/pr?repo=${encodeURIComponent(repo)}&number=${encodeURIComponent(number)}`),

  reviews: ({ repo = '', prNumber, limit = 50 } = {}) => {
    const q = new URLSearchParams()
    if (repo) q.set('repo', repo)
    if (prNumber != null && prNumber !== '') q.set('pr_number', prNumber)
    q.set('limit', String(limit))
    return request(`/api/reviews?${q.toString()}`)
  },

  listAccess: () => request('/api/access'),

  addAccess: (email) => request('/api/access', { method: 'POST', body: { email } }),

  removeAccess: (email) =>
    request(`/api/access?email=${encodeURIComponent(email)}`, { method: 'DELETE' }),
}

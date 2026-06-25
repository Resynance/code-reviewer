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
  // Async review: enqueue, kick off the run, then poll the job for the result.
  createReview: (body) => request('/api/review', { method: 'POST', body }),
  runReview: (id) => request(`/api/review/${encodeURIComponent(id)}/run`, { method: 'POST' }),
  getReviewJob: (id) => request(`/api/review/${encodeURIComponent(id)}`),

  postPrComment: ({ repo, pr_number, body }) =>
    request('/api/pr-comment', { method: 'POST', body: { repo, pr_number, body } }),

  createIssue: ({ repo, title, body }) =>
    request('/api/issue', { method: 'POST', body: { repo, title, body } }),

  createComplianceIssue: (analysisId, body = {}) =>
    request(`/api/compliance/analyses/${encodeURIComponent(analysisId)}/issue`, { method: 'POST', body }),

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

  testLlmEndpoint: (body) => request('/api/llm/test', { method: 'POST', body }),

  listRepos: () => request('/api/repos'),

  addRepo: (repo) => request('/api/repos', { method: 'POST', body: { repo } }),

  removeRepo: (repo) =>
    request(`/api/repos?repo=${encodeURIComponent(repo)}`, { method: 'DELETE' }),

  backfill: (repo, pages) =>
    request('/api/backfill', { method: 'POST', body: { repo, pages } }),

  addGithubToken: (token) =>
    request('/api/github/tokens', { method: 'POST', body: { token } }),

  removeGithubToken: (username) =>
    request(`/api/github/tokens?username=${encodeURIComponent(username)}`, { method: 'DELETE' }),

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

  createAssessment: (body) => request('/api/assessments', { method: 'POST', body }),
  runAssessment: (id) => request(`/api/assessments/${encodeURIComponent(id)}/run`, { method: 'POST' }),
  getAssessmentJob: (id) => request(`/api/assessments/${encodeURIComponent(id)}`),
  listAssessments: ({ repo = '', limit = 20 } = {}) => {
    const q = new URLSearchParams()
    if (repo) q.set('repo', repo)
    q.set('limit', String(limit))
    return request(`/api/assessments?${q.toString()}`)
  },

  listQueueJobs: ({ limit = 100, status = '', jobType = '' } = {}) => {
    const q = new URLSearchParams()
    q.set('limit', String(limit))
    if (status) q.set('status', status)
    if (jobType) q.set('job_type', jobType)
    return request(`/api/queue?${q.toString()}`)
  },

  complianceDashboard: (repo, limit = 50) => {
    const q = new URLSearchParams()
    q.set('repo', repo)
    q.set('limit', String(limit))
    return request(`/api/compliance/dashboard?${q.toString()}`)
  },

  complianceHealth: (repo) =>
    request(`/api/compliance/health?repo=${encodeURIComponent(repo)}`),

  complianceCoverage: (repo, limit = 50) => {
    const q = new URLSearchParams()
    q.set('repo', repo)
    q.set('limit', String(limit))
    return request(`/api/compliance/coverage?${q.toString()}`)
  },

  complianceSuggestions: (repo) =>
    request(`/api/compliance/suggestions?repo=${encodeURIComponent(repo)}`),

  applyComplianceSuggestion: (repo, suggestion) =>
    request('/api/compliance/suggestions/apply', { method: 'POST', body: { repo, suggestion } }),

  analyzeCompliance: (repo, limit = 50) =>
    request('/api/compliance/analyze', { method: 'POST', body: { repo, limit } }),

  listComplianceAnalyses: ({ repo = '', limit = 20 } = {}) => {
    const q = new URLSearchParams()
    if (repo) q.set('repo', repo)
    q.set('limit', String(limit))
    return request(`/api/compliance/analyses?${q.toString()}`)
  },

  getComplianceAnalysis: (id) =>
    request(`/api/compliance/analyses/${encodeURIComponent(id)}`),

  reanalyzeCompliance: (id) =>
    request(`/api/compliance/analyses/${encodeURIComponent(id)}/reanalyze`, { method: 'POST' }),

  listAccess: () => request('/api/access'),

  addAccess: (email) => request('/api/access', { method: 'POST', body: { email } }),

  removeAccess: (email) =>
    request(`/api/access?email=${encodeURIComponent(email)}`, { method: 'DELETE' }),
}

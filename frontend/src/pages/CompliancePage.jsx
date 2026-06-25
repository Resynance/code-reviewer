import { useState, useEffect, useCallback } from 'react'
import { api } from '../lib/api.js'
import { useMediaQuery } from '../lib/useMediaQuery.js'

export default function CompliancePage() {
  const isMobile = useMediaQuery('(max-width: 860px)')
  const [repos, setRepos] = useState([])
  const [selectedRepo, setSelectedRepo] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [dashboard, setDashboard] = useState(null)
  const [currentAnalysisId, setCurrentAnalysisId] = useState(null)
  const [analyses, setAnalyses] = useState([])
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [applying, setApplying] = useState(null)
  const [applyMsg, setApplyMsg] = useState(null)
  const [executionMode, setExecutionMode] = useState('inline')
  const [localAgenticTargets, setLocalAgenticTargets] = useState([])
  const [selectedAgenticTarget, setSelectedAgenticTarget] = useState('')
  const [creatingIssue, setCreatingIssue] = useState(false)
  const [issueMsg, setIssueMsg] = useState(null)
  const [issueUrl, setIssueUrl] = useState(null)
  const [followupJobId, setFollowupJobId] = useState(null)
  const [handoffAfterIssue, setHandoffAfterIssue] = useState(false)

  useEffect(() => {
    api.listRepos().then(r => {
      const list = r.repos || []
      setRepos(list)
      if (list.length > 0 && !selectedRepo) setSelectedRepo(list[0])
    }).catch(() => {})
    api.getSettings().then(s => {
      setExecutionMode(s.llm_execution_mode || 'inline')
      const targets = (s.local_agentic_targets || []).filter(t => t.enabled)
      setLocalAgenticTargets(targets)
      setSelectedAgenticTarget(targets[0]?.id || '')
    }).catch(() => {})
  }, [])

  const loadHistory = useCallback(async (repo) => {
    if (!repo) return
    setLoadingHistory(true)
    try {
      const data = await api.listComplianceAnalyses({ repo, limit: 20 })
      setAnalyses(data.analyses || [])
    } catch (e) {
      setAnalyses([])
    } finally {
      setLoadingHistory(false)
    }
  }, [])

  useEffect(() => {
    loadHistory(selectedRepo)
    setDashboard(null)
    setCurrentAnalysisId(null)
    setError(null)
    setApplyMsg(null)
    setIssueMsg(null)
    setIssueUrl(null)
    setFollowupJobId(null)
    setHandoffAfterIssue(false)
  }, [selectedRepo, loadHistory])

  async function runAnalysis() {
    if (!selectedRepo) return
    setLoading(true)
    setError(null)
    setApplyMsg(null)
    setIssueMsg(null)
    try {
      const data = await api.analyzeCompliance(selectedRepo)
      setDashboard(data)
      setCurrentAnalysisId(data.id)
      await loadHistory(selectedRepo)
    } catch (e) {
      setError(e.message)
      setDashboard(null)
      setCurrentAnalysisId(null)
    } finally {
      setLoading(false)
    }
  }

  async function loadAnalysis(id) {
    setLoading(true)
    setError(null)
    setApplyMsg(null)
    setIssueMsg(null)
    setIssueUrl(null)
    setFollowupJobId(null)
    try {
      const data = await api.getComplianceAnalysis(id)
      setDashboard(data)
      setCurrentAnalysisId(data.id)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function reanalyze() {
    if (!currentAnalysisId) return
    setLoading(true)
    setError(null)
    setApplyMsg(null)
    setIssueMsg(null)
    setIssueUrl(null)
    setFollowupJobId(null)
    try {
      const data = await api.reanalyzeCompliance(currentAnalysisId)
      setDashboard(data)
      setCurrentAnalysisId(data.id)
      await loadHistory(selectedRepo)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function applySuggestion(suggestion) {
    setApplying(suggestion.id)
    setApplyMsg(null)
    try {
      await api.applyComplianceSuggestion(selectedRepo, suggestion)
      setApplyMsg('Applied ✓')
    } catch (e) {
      setApplyMsg(e.message)
    } finally {
      setApplying(null)
    }
  }

  async function createIssueFromAnalysis() {
    if (!currentAnalysisId) return
    setCreatingIssue(true)
    setIssueMsg(null)
    setIssueUrl(null)
    setFollowupJobId(null)
    try {
      const payload = {}
      if (handoffAfterIssue && executionMode === 'local_queue' && selectedAgenticTarget) {
        payload.agentic_target = selectedAgenticTarget
      }
      const res = await api.createComplianceIssue(currentAnalysisId, payload)
      setIssueUrl(res.html_url || null)
      setFollowupJobId(res.job_id || null)
      setIssueMsg(res.job_id ? 'Issue created and local follow-up queued ✓' : 'Issue created ✓')
    } catch (e) {
      setIssueMsg(e.message)
    } finally {
      setCreatingIssue(false)
    }
  }

  const health = dashboard?.health
  const coverage = dashboard?.coverage
  const suggestions = dashboard?.suggestions || []
  const createdAt = dashboard?.created_at
  const canQueueFollowup = executionMode === 'local_queue' && localAgenticTargets.length > 0

  return (
    <div style={{ maxWidth: 900, width: '100%' }}>
      <h1 style={{ fontSize: 22, fontWeight: 600, marginBottom: 4 }}>Compliance</h1>
      <p style={{ color: 'var(--text-2)', marginBottom: 24 }}>
        Analyze how compliance checks are performed, save results, and keep policies up to date.
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 320px', gap: 16, alignItems: 'start' }}>
        <div>
          <Card title="Repository">
            <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexDirection: isMobile ? 'column' : 'row' }}>
              <div style={{ flex: 1, width: '100%' }}>
                <label style={labelStyle}>Repo</label>
                <select
                  value={selectedRepo}
                  onChange={e => setSelectedRepo(e.target.value)}
                  style={inputStyle}
                >
                  <option value="">Select a repo…</option>
                  {repos.map(r => <option key={r} value={r}>{r}</option>)}
                </select>
              </div>
              <button onClick={runAnalysis} disabled={loading || !selectedRepo} style={{ ...primaryBtn(loading), height: 38, width: isMobile ? '100%' : 'auto' }}>
                {loading ? 'Analyzing…' : 'Analyze now'}
              </button>
            </div>
            {error && <div style={{ color: 'var(--red)', fontSize: 13, marginTop: 10 }}>⚠ {error}</div>}
          </Card>

          {!dashboard && !loading && !error && (
            <div style={{ color: 'var(--text-3)', fontSize: 14, padding: '20px 0' }}>
              Select a repository and run <strong>Analyze now</strong> to generate a saved compliance report.
            </div>
          )}

          {dashboard && createdAt && (
            <Card title="Analysis Result">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10, marginBottom: 12 }}>
                <div style={{ fontSize: 13, color: 'var(--text-2)' }}>
                  {currentAnalysisId ? `Saved analysis #${currentAnalysisId}` : 'Live preview'} · {new Date(createdAt).toLocaleString()}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                  {canQueueFollowup && (
                    <>
                      <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-2)' }}>
                        <input type="checkbox" checked={handoffAfterIssue} onChange={e => setHandoffAfterIssue(e.target.checked)} />
                        Hand off locally
                      </label>
                      <select value={selectedAgenticTarget} onChange={e => setSelectedAgenticTarget(e.target.value)} disabled={!handoffAfterIssue} style={{ ...inputStyle, width: 180, padding: '6px 10px', fontSize: 12, opacity: handoffAfterIssue ? 1 : 0.6 }}>
                        {localAgenticTargets.map(target => <option key={target.id} value={target.id}>{target.label}</option>)}
                      </select>
                    </>
                  )}
                  {currentAnalysisId && (
                    <button onClick={createIssueFromAnalysis} disabled={creatingIssue || (handoffAfterIssue && canQueueFollowup && !selectedAgenticTarget)} style={{ ...smallBtn, opacity: (creatingIssue || (handoffAfterIssue && canQueueFollowup && !selectedAgenticTarget)) ? 0.6 : 1 }}>
                      {creatingIssue ? 'Creating issue…' : (handoffAfterIssue && canQueueFollowup ? 'Create issue + hand off' : 'Create GitHub issue')}
                    </button>
                  )}
                  {currentAnalysisId && (
                    <button onClick={reanalyze} disabled={loading} style={{ ...smallBtn, opacity: loading ? 0.6 : 1 }}>
                      {loading ? 'Re-analyzing…' : '↻ Re-analyze'}
                    </button>
                  )}
                </div>
              </div>
              {canQueueFollowup && (
                <div style={{ fontSize: 12, color: 'var(--text-3)', marginBottom: 12 }}>
                  Local queue mode is enabled, so issue creation can optionally enqueue a configured agentic target to assess the findings, make changes, and open a PR tied to the issue.
                </div>
              )}
              {issueMsg && (
                <div style={{ fontSize: 13, color: issueMsg.includes('✓') ? 'var(--green)' : 'var(--red)', marginBottom: 12 }}>
                  {issueMsg}
                  {issueUrl && <> {' '}<a href={issueUrl} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>View issue</a></>}
                  {followupJobId && <> {' '}· queued job #{followupJobId}</>}
                </div>
              )}
              <DashboardView health={health} coverage={coverage} suggestions={suggestions} applying={applying} applyMsg={applyMsg} onApply={applySuggestion} />
            </Card>
          )}
        </div>

        <Card title="History">
          {loadingHistory ? (
            <div style={{ color: 'var(--text-3)', fontSize: 13 }}>Loading…</div>
          ) : analyses.length === 0 ? (
            <div style={{ color: 'var(--text-3)', fontSize: 13 }}>No saved analyses yet.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {analyses.map(a => (
                <button
                  key={a.id}
                  onClick={() => loadAnalysis(a.id)}
                  style={{
                    textAlign: 'left',
                    background: currentAnalysisId === a.id ? 'var(--accent-glow)' : 'var(--surface2)',
                    border: '1px solid var(--border)',
                    borderRadius: 8,
                    padding: '10px 12px',
                    cursor: 'pointer',
                    color: 'var(--text)',
                  }}
                >
                  <div style={{ fontSize: 12, fontWeight: 500 }}>#{a.id} · {new Date(a.created_at).toLocaleString()}</div>
                  <div style={{ fontSize: 12, color: 'var(--text-2)', marginTop: 4 }}>
                    Health {a.health?.score ?? '—'} · Coverage {a.coverage?.coverage_score ?? '—'}
                  </div>
                </button>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}

function DashboardView({ health, coverage, suggestions, applying, applyMsg, onApply }) {
  return (
    <>
      {health && (
        <div style={{ marginBottom: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 12 }}>
            <ScoreRing score={health.score} />
            <div>
              <div style={{ fontSize: 13, color: 'var(--text-2)' }}>Policy enabled</div>
              <div style={{ fontSize: 15, fontWeight: 500, color: health.policy_enabled ? 'var(--green)' : 'var(--text-3)' }}>
                {health.policy_enabled ? 'Yes' : 'No'}
              </div>
            </div>
          </div>
          {health.findings?.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {health.findings.map((f, i) => (
                <div key={i} style={{ background: 'var(--surface2)', borderRadius: 8, padding: '10px 12px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                    <SeverityBadge severity={f.severity} />
                    <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--text)' }}>{f.title}</span>
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text-2)', marginBottom: 2 }}>{f.evidence}</div>
                  <div style={{ fontSize: 12, color: 'var(--text-3)' }}>→ {f.recommendation}</div>
                </div>
              ))}
            </div>
          )}
          {health.vendors?.unlisted?.length > 0 && <DetailList label="Unlisted vendors" items={health.vendors.unlisted} />}
          {health.signals?.required_signals_not_observed?.length > 0 && <DetailList label="Required signals not observed" items={health.signals.required_signals_not_observed} />}
          {health.phi_patterns?.missing_from_policy?.length > 0 && <DetailList label="PHI terms missing from policy" items={health.phi_patterns.missing_from_policy} />}
        </div>
      )}

      {coverage && (
        <div style={{ marginBottom: 20, paddingTop: 16, borderTop: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 12 }}>
            <ScoreRing score={coverage.coverage_score} />
            <div>
              <div style={{ fontSize: 13, color: 'var(--text-2)' }}>
                {coverage.review_count} review(s), {coverage.assessment_count} assessment(s)
              </div>
              <div style={{ fontSize: 13, color: 'var(--text-2)' }}>
                {coverage.deterministic_count} deterministic · {coverage.llm_count} LLM findings
              </div>
            </div>
          </div>
          <div style={{ fontSize: 13, color: 'var(--text-2)', marginBottom: 12 }}>{coverage.summary}</div>
          {Object.keys(coverage.categories || {}).length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Findings by category</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {Object.entries(coverage.categories).map(([cat, counts]) => (
                  <div key={cat} style={{ display: 'flex', alignItems: 'center', gap: 10, background: 'var(--surface2)', borderRadius: 6, padding: '7px 10px' }}>
                    <span style={{ flex: 1, fontSize: 12, textTransform: 'capitalize', color: 'var(--text)' }}>{cat.replace(/_/g, ' ')}</span>
                    <span style={{ fontSize: 12, color: 'var(--text-3)' }}>det {counts.deterministic}</span>
                    <span style={{ fontSize: 12, color: 'var(--text-3)' }}>LLM {counts.llm}</span>
                    <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--text)', minWidth: 24, textAlign: 'right' }}>{counts.total}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          {coverage.blind_spots?.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div style={{ fontSize: 11, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Potential blind spots</div>
              {coverage.blind_spots.map((spot, i) => (
                <div key={i} style={{ background: 'var(--surface2)', borderRadius: 8, padding: '10px 12px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                    <SeverityBadge severity={spot.severity} />
                    <span style={{ fontSize: 13, fontWeight: 500, textTransform: 'capitalize', color: 'var(--text)' }}>{spot.category.replace(/_/g, ' ')}</span>
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text-2)' }}>LLM {spot.llm_count} · deterministic {spot.deterministic_count}</div>
                  <div style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 2 }}>→ {spot.suggestion}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div style={{ paddingTop: 16, borderTop: '1px solid var(--border)' }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', marginBottom: 12 }}>Policy Update Suggestions</div>
        {suggestions.length === 0 ? (
          <div style={{ color: 'var(--text-3)', fontSize: 13 }}>No policy update suggestions at this time.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {suggestions.map((s, i) => (
              <div key={i} style={{ background: 'var(--surface2)', borderRadius: 8, padding: '10px 12px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <SeverityBadge severity={s.severity} />
                  <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--text)' }}>{_suggestionTitle(s)}</span>
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-2)', marginBottom: 2 }}>{s.reason}</div>
                <div style={{ fontSize: 12, color: 'var(--text-3)', marginBottom: 6 }}>Evidence: {s.evidence}</div>
                {s.action !== 'review' && (
                  <button onClick={() => onApply(s)} disabled={applying === s.id} style={{ ...smallBtn, opacity: applying === s.id ? 0.6 : 1 }}>
                    {applying === s.id ? 'Applying…' : 'Apply'}
                  </button>
                )}
              </div>
            ))}
            {applyMsg && (
              <div style={{ fontSize: 13, color: applyMsg === 'Applied ✓' ? 'var(--green)' : 'var(--red)', marginTop: 4 }}>
                {applyMsg}
              </div>
            )}
          </div>
        )}
      </div>
    </>
  )
}

function _suggestionTitle(s) {
  if (s.type === 'enable_compliance') return 'Enable HIPAA / HL7 compliance review'
  if (s.type === 'add_vendor') return `Add “${s.value}” to approved vendors`
  if (s.type === 'remove_vendor') return `Remove “${s.value}” from approved vendors`
  if (s.type === 'review_vendor') return `Review vendor “${s.value}”`
  if (s.type === 'disallowed_vendor_present') return `Disallowed vendor “${s.value}” still present`
  if (s.type === 'add_phi_pattern') return `Add PHI pattern “${s.value}”`
  if (s.type === 'signal_present') return `Signal “${s.value}” confirmed in code`
  return `Update ${s.field}`
}

function ScoreRing({ score }) {
  const color = score >= 80 ? 'var(--green)' : score >= 50 ? 'var(--yellow)' : 'var(--red)'
  return (
    <div style={{
      width: 56, height: 56, borderRadius: '50%', border: `3px solid ${color}`,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontSize: 16, fontWeight: 600, color,
    }}>
      {score}
    </div>
  )
}

function SeverityBadge({ severity }) {
  const color = severity === 'high' ? 'var(--red)' : severity === 'medium' ? 'var(--yellow)' : 'var(--green)'
  return (
    <span style={{
      fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em',
      color, border: `1px solid ${color}`, borderRadius: 4, padding: '1px 5px',
    }}>
      {severity}
    </span>
  )
}

function DetailList({ label, items }) {
  return (
    <div style={{ marginTop: 12 }}>
      <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {items.map((item, i) => (
          <span key={i} style={{ fontSize: 12, color: 'var(--text)', background: 'var(--surface2)', borderRadius: 4, padding: '3px 8px' }}>
            {item}
          </span>
        ))}
      </div>
    </div>
  )
}

function Card({ title, children }) {
  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10,
      padding: '20px 24px', marginBottom: 16,
    }}>
      <div style={{ fontSize: 11, fontWeight: 600, letterSpacing: '0.06em', color: 'var(--text-3)', textTransform: 'uppercase', marginBottom: 16 }}>
        {title}
      </div>
      {children}
    </div>
  )
}

const labelStyle = { display: 'block', fontSize: 11, color: 'var(--text-3)', marginBottom: 5, letterSpacing: '0.04em', textTransform: 'uppercase' }
const inputStyle = { display: 'block', width: '100%', background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 7, padding: '9px 12px', color: 'var(--text)', fontSize: 13, outline: 'none' }
const primaryBtn = (busy) => ({
  background: busy ? 'var(--surface2)' : 'var(--accent)', color: busy ? 'var(--text-3)' : '#fff',
  border: 'none', borderRadius: 8, padding: '9px 20px', fontSize: 13, fontWeight: 500,
})
const smallBtn = {
  background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)',
  borderRadius: 7, padding: '5px 12px', fontSize: 12,
}

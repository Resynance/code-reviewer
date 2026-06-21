import { useState, useEffect, useRef } from 'react'
import { api } from '../lib/api.js'

// Map a stored history record to the live result shape the panel renders.
// (History persists `past_decisions`; a fresh run returns `past_decisions_applied`.)
function historyToResult(r) {
  return {
    pr_number: r.pr_number,
    summary: r.summary,
    approved: r.approved,
    confidence: r.confidence,
    issues: r.issues || [],
    suggestions: r.suggestions || [],
    past_decisions_applied: r.past_decisions || [],
    hipaa_review: r.hipaa_review || {},
    model: r.model || '',
  }
}

export default function ReviewPage() {
  const [form, setForm] = useState({
    pr_number: '',
    repo: '',
    title: '',
    description: '',
    diff: '',
    author: '',
    base_branch: '',
    files_changed: [],
  })
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [repos, setRepos] = useState([])
  const [prList, setPrList] = useState([])
  const [loadingPrs, setLoadingPrs] = useState(false)
  const [loadingPr, setLoadingPr] = useState(false)
  const [prError, setPrError] = useState(null)
  const [selectedPr, setSelectedPr] = useState('')
  // Available model slots from settings (null if not yet loaded).
  const [models, setModels] = useState(null) // [{label, model, provider}]
  // Which model slot the user picked; null = model 1 (default).
  const [selectedModel, setSelectedModel] = useState(null)
  const [executionMode, setExecutionMode] = useState('inline')
  const [hipaaPolicies, setHipaaPolicies] = useState({ default: {}, repos: {} })
  // Tracks the in-flight review job so a superseded/unmounted poll stops.
  const jobRef = useRef(null)
  useEffect(() => () => { jobRef.current = null }, [])
  // When the shown result is the last *saved* review (vs. a fresh run), holds
  // its timestamp so the panel can say so.
  const [savedAt, setSavedAt] = useState(null)

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  // Load configured repos and model slots on mount.
  useEffect(() => {
    api.listRepos().then(r => {
      const list = r.repos || []
      setRepos(list)
      if (list.length) {
        setForm(f => (f.repo ? f : { ...f, repo: list[0] }))
      }
    }).catch(() => {})

    api.getSettings().then(s => {
      const slots = s.openrouter_models || []
      setModels(slots)
      setSelectedModel(slots[0] || null)
      setExecutionMode(s.llm_execution_mode || 'inline')
      setHipaaPolicies(s.hipaa_policies || { default: {}, repos: {} })
    }).catch(() => {})
  }, [])

  const hipaaEnabled = !!hipaaPolicies?.repos?.[form.repo]?.enabled

  // Whenever the selected repo changes, load its PRs (open first) for the picker.
  useEffect(() => {
    if (!form.repo || !repos.includes(form.repo)) { setPrList([]); return }
    let active = true
    setLoadingPrs(true); setPrError(null); setSelectedPr('')
    api.repoPrs(form.repo)
      .then(res => { if (active) setPrList(res.prs || []) })
      .catch(() => { if (active) { setPrList([]); setPrError('Could not load PRs — check the GitHub token in Settings.') } })
      .finally(() => active && setLoadingPrs(false))
    return () => { active = false }
  }, [form.repo, repos])

  // Load a selected PR's metadata + diff into the form.
  async function loadPr(number) {
    setSelectedPr(number)
    if (!number) return
    setLoadingPr(true); setPrError(null)
    try {
      const data = await api.repoPr(form.repo, number)
      setForm(f => ({
        ...f,
        pr_number: data.pr_number,
        title: data.title,
        description: data.description,
        author: data.author,
        base_branch: data.base_branch,
        diff: data.diff,
        files_changed: data.files_changed || [],
      }))
      // Surface the most recent saved review for this PR, so it's not "lost"
      // after a refresh — a fresh run replaces it.
      try {
        const hist = await api.reviews({ repo: form.repo, prNumber: number, limit: 1 })
        const last = (hist.reviews || [])[0]
        setResult(last ? historyToResult(last) : null)
        setSavedAt(last ? last.created_at : null)
        setError(null)
      } catch { /* history is best-effort */ }
    } catch (e) {
      setPrError(e.message)
    } finally {
      setLoadingPr(false)
    }
  }

  async function submit() {
    setLoading(true)
    setResult(null)
    setSavedAt(null)
    setError(null)
    try {
      const { id } = await api.createReview({
        ...form,
        files_changed: form.files_changed.filter(Boolean),
        model: selectedModel?.model || undefined,
        provider: selectedModel?.provider || undefined,
      })
      jobRef.current = id
      // Drive the work in the background; the result is read via polling, so a
      // dropped run connection still recovers once the job finishes.
      if (executionMode === 'inline') api.runReview(id).catch(() => {})
      poll(id)
    } catch (e) {
      setError(e.message)
      setLoading(false)
    }
  }

  // Poll a review job until it finishes, ignoring results for superseded jobs.
  async function poll(id) {
    if (jobRef.current !== id) return
    let job
    try {
      job = await api.getReviewJob(id)
    } catch (e) {
      if (jobRef.current === id) { setError(e.message); setLoading(false) }
      return
    }
    if (jobRef.current !== id) return
    if (job.status === 'done') {
      setResult(job.result)
      setLoading(false)
    } else if (job.status === 'error') {
      setError(job.error || 'Review failed')
      setLoading(false)
    } else {
      setTimeout(() => poll(id), 1500)
    }
  }

  return (
    <div style={{ maxWidth: 900 }}>
      <h1 style={{ fontSize: 22, fontWeight: 600, marginBottom: 4 }}>Review a Pull Request</h1>
      <p style={{ color: 'var(--text-2)', marginBottom: 28 }}>
        Select a pull request to load it from GitHub, or fill the fields manually,
        then run an AI review with historical context.
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
        <Field label="Repository">
          {repos.length > 0 ? (
            <select value={form.repo} onChange={e => set('repo', e.target.value)} style={inputStyle}>
              {!repos.includes(form.repo) && <option value={form.repo}>{form.repo || 'Select a repo'}</option>}
              {repos.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
          ) : (
            <input value={form.repo} onChange={e => set('repo', e.target.value)} style={inputStyle} placeholder="owner/repo" />
          )}
        </Field>
        <Field label="PR Number" >
          <input value={form.pr_number} onChange={e => set('pr_number', Number(e.target.value))}
            type="number" style={inputStyle} />
        </Field>
        {repos.length > 0 && (
          <Field label="Load a pull request" style={{ gridColumn: '1 / -1' }}>
            <div style={{ display: 'flex', gap: 8 }}>
              <select value={selectedPr} onChange={e => loadPr(e.target.value)} style={{ ...inputStyle, flex: 1 }} disabled={loadingPrs || loadingPr}>
                <option value="">
                  {loadingPrs ? 'Loading pull requests…' : loadingPr ? 'Loading PR…' : 'Select a pull request…'}
                </option>
                {prList.map(pr => (
                  <option key={pr.number} value={pr.number}>
                    #{pr.number}  {pr.state === 'open' ? '●' : '○'} {pr.state}{pr.draft ? ' · draft' : ''}  —  {pr.title}
                  </option>
                ))}
              </select>
              {selectedPr && (
                <button
                  onClick={() => loadPr(selectedPr)}
                  disabled={loadingPr}
                  title="Re-fetch this PR from GitHub"
                  style={{
                    background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8,
                    padding: '0 14px', fontSize: 13, color: loadingPr ? 'var(--text-3)' : 'var(--text)',
                    cursor: loadingPr ? 'default' : 'pointer', whiteSpace: 'nowrap', flexShrink: 0,
                  }}
                >
                  {loadingPr ? '⟳' : '↻ Refresh PR'}
                </button>
              )}
            </div>
            {prError && <div style={{ fontSize: 12, color: 'var(--red)', marginTop: 4 }}>⚠ {prError}</div>}
          </Field>
        )}
        <Field label="PR Title" style={{ gridColumn: '1 / -1' }}>
          <input value={form.title} onChange={e => set('title', e.target.value)} style={inputStyle} />
        </Field>
        <Field label="Description" style={{ gridColumn: '1 / -1' }}>
          <textarea value={form.description} onChange={e => set('description', e.target.value)}
            rows={2} style={{ ...inputStyle, resize: 'vertical' }} />
        </Field>
        <Field label="Author">
          <input value={form.author} onChange={e => set('author', e.target.value)} style={inputStyle} />
        </Field>
        <Field label="Base Branch">
          <input value={form.base_branch} onChange={e => set('base_branch', e.target.value)} style={inputStyle} />
        </Field>
      </div>

      <Field label="Diff" style={{ marginBottom: 16 }}>
        <textarea value={form.diff} onChange={e => set('diff', e.target.value)}
          rows={14} style={{ ...inputStyle, fontFamily: 'var(--font-mono)', fontSize: 12, resize: 'vertical' }} />
      </Field>

      {models && models.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
          <span style={{ fontSize: 11, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: '0.05em', whiteSpace: 'nowrap' }}>Model</span>
          <select
            value={models.findIndex(m => m.model === selectedModel?.model && m.provider === selectedModel?.provider)}
            onChange={e => setSelectedModel(models[+e.target.value])}
            style={{ ...inputStyle, width: 'auto', minWidth: 260, fontFamily: 'var(--font-mono)', fontSize: 12 }}
          >
            {models.map((m, i) => (
              <option key={i} value={i}>{m.label ? `${m.label} — ${m.model}` : m.model}</option>
            ))}
          </select>
        </div>
      )}

      <div style={{ marginBottom: 14, fontSize: 13, color: hipaaEnabled ? 'var(--text)' : 'var(--text-2)' }}>
        {hipaaEnabled
          ? 'HIPAA-focused review is enabled for this repository in Settings.'
          : 'HIPAA-focused review is not enabled for this repository.'}
      </div>

      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 28 }}>
        <button onClick={submit} disabled={loading} style={btnStyle(loading)}>
          {loading ? '⟳ Reviewing…' : '▶ Run Review'}
        </button>
        {error && <span style={{ color: 'var(--red)', fontSize: 13 }}>⚠ {error}</span>}
      </div>

      {result && savedAt && (
        <div style={{ fontSize: 12, color: 'var(--text-3)', marginBottom: 10 }}>
          Showing the most recent saved review ({new Date(savedAt).toLocaleString()}). Run Review to refresh.
        </div>
      )}
      {result && <ReviewResult result={result} repo={form.repo} />}
    </div>
  )
}

function ReviewResult({ result, repo }) {
  const statusColor = result.approved ? 'var(--green)' : 'var(--yellow)'
  const statusLabel = result.approved ? 'Approved' : 'Changes Requested'

  const [selIssues, setSelIssues] = useState(new Set())
  const [selSugg, setSelSugg] = useState(new Set())
  const [commenting, setCommenting] = useState(false)
  const [commentUrl, setCommentUrl] = useState(null)
  const [commentErr, setCommentErr] = useState(null)
  const [issuing, setIssuing] = useState(false)
  const [issueUrl, setIssueUrl] = useState(null)
  const [issueErr, setIssueErr] = useState(null)

  const toggle = (set, setter, i) => {
    const n = new Set(set)
    n.has(i) ? n.delete(i) : n.add(i)
    setter(n)
  }
  const selCount = selIssues.size + selSugg.size
  const hasFindings = (result.issues?.length || 0) + (result.suggestions?.length || 0) > 0

  function buildBody() {
    const issues = [...selIssues].sort((a, b) => a - b).map(i => result.issues[i]).filter(Boolean)
    const suggs = [...selSugg].sort((a, b) => a - b).map(i => result.suggestions[i]).filter(Boolean)
    if (!issues.length && !suggs.length) return ''
    const lines = ['## 🤖 ReviewBot', '']
    if (issues.length) {
      lines.push('**Issues**')
      issues.forEach(it => {
        lines.push(`- **[${(it.severity || '').toUpperCase()}]** \`${it.file}\` — ${it.description}`)
        if (it.suggestion) lines.push(`  - 💡 ${it.suggestion}`)
      })
      lines.push('')
    }
    if (suggs.length) {
      lines.push('**Suggestions**')
      suggs.forEach(s => lines.push(`- _${(s.type || '').replace('_', ' ')}_ — ${s.description}`))
    }
    return lines.join('\n')
  }

  async function postComment() {
    const body = buildBody()
    if (!body) return
    setCommenting(true); setCommentErr(null); setCommentUrl(null)
    try {
      const res = await api.postPrComment({ repo, pr_number: result.pr_number, body })
      setCommentUrl(res.html_url)
    } catch (e) {
      setCommentErr(e.message)
    } finally {
      setCommenting(false)
    }
  }

  // Title for an issue built from the selection: the single finding's text if
  // exactly one is picked, else a summary referencing the PR.
  function issueTitle() {
    const issues = [...selIssues].sort((a, b) => a - b).map(i => result.issues[i]).filter(Boolean)
    if (issues.length === 1 && selSugg.size === 0) {
      const it = issues[0]
      return `[${(it.severity || '').toUpperCase()}] ${it.file ? `${it.file}: ` : ''}${it.description}`.slice(0, 120)
    }
    return `Code review findings — ${repo}#${result.pr_number} (${selCount})`
  }

  async function postIssue() {
    const body = buildBody()
    if (!body) return
    setIssuing(true); setIssueErr(null); setIssueUrl(null)
    try {
      const ref = `\n\n—\nFrom review of ${repo}#${result.pr_number}`
      const res = await api.createIssue({ repo, title: issueTitle(), body: body + ref })
      setIssueUrl(res.html_url)
    } catch (e) {
      setIssueErr(e.message)
    } finally {
      setIssuing(false)
    }
  }

  return (
    <div style={{ animation: 'fadeIn 0.3s ease' }}>
      <style>{`@keyframes fadeIn { from { opacity:0; transform:translateY(8px) } to { opacity:1; transform:none } }`}</style>

      {/* Header */}
      <div style={{
        background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10,
        padding: '20px 24px', marginBottom: 16, display: 'flex', alignItems: 'center', gap: 16,
      }}>
        <div style={{
          width: 44, height: 44, borderRadius: '50%', border: `2px solid ${statusColor}`,
          display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 20,
        }}>{result.approved ? '✓' : '!'}</div>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span style={{ fontWeight: 600, fontSize: 15, color: statusColor }}>{statusLabel}</span>
            {result.model && (
              <span style={{
                fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--text-3)',
                background: 'var(--surface2)', border: '1px solid var(--border)',
                borderRadius: 5, padding: '2px 7px',
              }}>{result.model}</span>
            )}
          </div>
          <div style={{ color: 'var(--text-2)', fontSize: 13, marginTop: 2 }}>{result.summary}</div>
        </div>
        <ConfidencePill value={result.confidence} />
      </div>

      {/* Comment-on-PR bar */}
      {hasFindings && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, padding: '10px 16px',
          background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10,
        }}>
          <span style={{ fontSize: 13, color: 'var(--text-2)' }}>
            {selCount} selected — tick issues/suggestions, then comment or open an issue on <code style={{ fontFamily: 'var(--font-mono)' }}>{repo}#{result.pr_number}</code>
          </span>
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            <button onClick={postComment} disabled={!selCount || commenting} style={{
              background: (!selCount || commenting) ? 'var(--surface2)' : 'var(--accent)',
              color: (!selCount || commenting) ? 'var(--text-3)' : '#fff', border: 'none', borderRadius: 8,
              padding: '8px 16px', fontSize: 13, fontWeight: 500,
            }}>{commenting ? 'Posting…' : '💬 Comment on PR'}</button>
            {commentUrl && <a href={commentUrl} target="_blank" rel="noreferrer" style={{ fontSize: 12, color: 'var(--green)' }}>✓ Posted ↗</a>}
            {commentErr && <span style={{ fontSize: 12, color: 'var(--red)' }}>⚠ {commentErr}</span>}
            <button onClick={postIssue} disabled={!selCount || issuing} style={{
              background: 'transparent', color: (!selCount || issuing) ? 'var(--text-3)' : 'var(--text)',
              border: '1px solid var(--border2)', borderRadius: 8,
              padding: '8px 16px', fontSize: 13, fontWeight: 500,
            }}>{issuing ? 'Creating…' : '🐛 Create issue'}</button>
            {issueUrl && <a href={issueUrl} target="_blank" rel="noreferrer" style={{ fontSize: 12, color: 'var(--green)' }}>✓ Created ↗</a>}
            {issueErr && <span style={{ fontSize: 12, color: 'var(--red)' }}>⚠ {issueErr}</span>}
          </div>
        </div>
      )}

      {/* Decision trail */}
      {result.past_decisions_applied?.length > 0 && (
        <Section title="📚 Past Decisions Applied" accent>
          {result.past_decisions_applied.map((d, i) => (
            <div key={i} style={{
              padding: '12px 16px', borderLeft: '3px solid var(--accent)',
              background: 'var(--accent-glow)', borderRadius: '0 6px 6px 0', marginBottom: 8,
            }}>
              <div style={{ fontWeight: 500, fontSize: 13, marginBottom: 2 }}>
                <span style={{ color: 'var(--accent)' }}>{d.ref}</span> — {d.summary}
              </div>
              <div style={{ color: 'var(--text-2)', fontSize: 12 }}>{d.how_applied}</div>
            </div>
          ))}
        </Section>
      )}

      {result.hipaa_review?.enabled && (
        <Section title="🏥 HIPAA Review">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 10, marginBottom: 14 }}>
            <InfoPill label="Relevant" value={result.hipaa_review.hipaa_relevant ? 'Yes' : 'No'} />
            <InfoPill label="Manual Review" value={result.hipaa_review.requires_manual_compliance_review ? 'Required' : 'Not flagged'} />
            <InfoPill label="Findings" value={String(result.hipaa_review.hipaa_findings?.length || 0)} />
          </div>
          {result.hipaa_review.summary && (
            <div style={{ fontSize: 13, color: 'var(--text)', marginBottom: 12 }}>{result.hipaa_review.summary}</div>
          )}
          {result.hipaa_review.hipaa_findings?.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 12 }}>
              {result.hipaa_review.hipaa_findings.map((item, i) => (
                <IssueCard
                  key={`hipaa-${i}`}
                  issue={{
                    severity: item.severity,
                    file: item.file || item.category,
                    description: item.title,
                    suggestion: item.recommendation,
                    past_decision_ref: item.manual_review ? 'manual review' : item.source,
                  }}
                  selected={false}
                  onToggle={() => {}}
                  readOnly
                  extra={item.evidence}
                />
              ))}
            </div>
          )}
          <HipaaGapGrid review={result.hipaa_review} />
        </Section>
      )}

      {/* Issues */}
      {result.issues?.length > 0 && (
        <Section
          title={`🔍 Issues (${result.issues.length})`}
          allSelected={selIssues.size === result.issues.length}
          onSelectAll={() => {
            if (selIssues.size === result.issues.length) setSelIssues(new Set())
            else setSelIssues(new Set(result.issues.map((_, i) => i)))
          }}
        >
          {result.issues.map((issue, i) => (
            <IssueCard key={i} issue={issue} selected={selIssues.has(i)} onToggle={() => toggle(selIssues, setSelIssues, i)} />
          ))}
        </Section>
      )}

      {/* Suggestions */}
      {result.suggestions?.length > 0 && (
        <Section
          title="💭 Suggestions"
          allSelected={selSugg.size === result.suggestions.length}
          onSelectAll={() => {
            if (selSugg.size === result.suggestions.length) setSelSugg(new Set())
            else setSelSugg(new Set(result.suggestions.map((_, i) => i)))
          }}
        >
          {result.suggestions.map((s, i) => (
            <div key={i} style={{
              padding: '10px 14px', background: 'var(--surface2)', borderRadius: 6, marginBottom: 6,
              display: 'flex', gap: 10, alignItems: 'flex-start',
            }}>
              <input type="checkbox" checked={selSugg.has(i)} onChange={() => toggle(selSugg, setSelSugg, i)}
                style={{ marginTop: 2, accentColor: 'var(--accent)', cursor: 'pointer' }} />
              <TypeBadge type={s.type} />
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, color: 'var(--text)' }}>{s.description}</div>
                {s.past_decision_ref && (
                  <div style={{ fontSize: 11, color: 'var(--accent)', marginTop: 3 }}>ref: {s.past_decision_ref}</div>
                )}
              </div>
            </div>
          ))}
        </Section>
      )}
    </div>
  )
}

function IssueCard({ issue, selected, onToggle, readOnly = false, extra = '' }) {
  const sevColors = { critical: '#EF4444', high: '#F97316', medium: '#EAB308', low: '#6366F1' }
  const color = sevColors[issue.severity] || 'var(--text-2)'
  return (
    <div style={{
      border: `1px solid ${color}33`, borderLeft: `3px solid ${color}`,
      borderRadius: '0 8px 8px 0', padding: '12px 16px', marginBottom: 10,
      background: `${color}08`,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        {!readOnly && (
          <input type="checkbox" checked={selected} onChange={onToggle}
            style={{ accentColor: 'var(--accent)', cursor: 'pointer' }} />
        )}
        <span style={{
          fontSize: 10, fontWeight: 600, letterSpacing: '0.05em',
          color, background: `${color}20`, padding: '2px 6px', borderRadius: 4,
        }}>{issue.severity.toUpperCase()}</span>
        <code style={{ fontSize: 12, color: 'var(--text-2)' }}>{issue.file}</code>
        {issue.past_decision_ref && (
          <span style={{ fontSize: 11, color: 'var(--accent)', marginLeft: 'auto' }}>
            ref: {issue.past_decision_ref}
          </span>
        )}
      </div>
      <div style={{ fontSize: 13, color: 'var(--text)', marginBottom: 6 }}>{issue.description}</div>
      {extra && (
        <div style={{ fontSize: 12, color: 'var(--text-2)', marginBottom: 6 }}>{extra}</div>
      )}
      <div style={{ fontSize: 12, color: 'var(--text-2)', paddingLeft: 10, borderLeft: '2px solid var(--border2)' }}>
        💡 {issue.suggestion}
      </div>
    </div>
  )
}

function HipaaGapGrid({ review }) {
  const groups = [
    ['PHI Exposure', review.phi_exposure_risk],
    ['Encryption', review.encryption_gaps],
    ['Access Control', review.access_control_gaps],
    ['Audit Trail', review.audit_trail_gaps],
    ['Minimum Necessary', review.minimum_necessary_gaps],
    ['Third-Party / BAA', review.third_party_baa_risks],
  ].filter(([, items]) => (items || []).length > 0)

  if (!groups.length) return null

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10 }}>
      {groups.map(([label, items]) => (
        <div key={label} style={{ background: 'var(--surface2)', borderRadius: 8, padding: '10px 12px' }}>
          <div style={{ fontSize: 11, color: 'var(--text-3)', textTransform: 'uppercase', marginBottom: 8 }}>{label}</div>
          {items.map((item, i) => (
            <div key={i} style={{ fontSize: 12, color: 'var(--text)', marginBottom: i === items.length - 1 ? 0 : 8 }}>
              <div>{item.summary}</div>
              {item.details && <div style={{ color: 'var(--text-2)', marginTop: 2 }}>{item.details}</div>}
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

function InfoPill({ label, value }) {
  return (
    <div style={{ background: 'var(--surface2)', borderRadius: 8, padding: '10px 12px' }}>
      <div style={{ fontSize: 11, color: 'var(--text-3)', textTransform: 'uppercase', marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 13, color: 'var(--text)' }}>{value}</div>
    </div>
  )
}

function Section({ title, children, accent, onSelectAll, allSelected }) {
  return (
    <div style={{
      background: 'var(--surface)', border: `1px solid ${accent ? 'var(--accent)' : 'var(--border)'}`,
      borderRadius: 10, padding: '16px 20px', marginBottom: 16,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12 }}>
        <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: '0.04em', color: 'var(--text-2)', textTransform: 'uppercase', flex: 1 }}>
          {title}
        </div>
        {onSelectAll && (
          <button onClick={onSelectAll} style={{
            background: 'transparent', border: 'none', cursor: 'pointer',
            fontSize: 11, color: 'var(--accent)', padding: '2px 4px',
            fontWeight: 500, letterSpacing: '0.03em',
          }}>
            {allSelected ? 'Deselect all' : 'Select all'}
          </button>
        )}
      </div>
      {children}
    </div>
  )
}

function ConfidencePill({ value }) {
  const pct = Math.round(value * 100)
  const color = pct >= 80 ? 'var(--green)' : pct >= 60 ? 'var(--yellow)' : 'var(--red)'
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{ fontSize: 22, fontWeight: 700, color, fontFamily: 'var(--font-mono)' }}>{pct}%</div>
      <div style={{ fontSize: 10, color: 'var(--text-3)', letterSpacing: '0.05em' }}>CONFIDENCE</div>
    </div>
  )
}

function TypeBadge({ type }) {
  const colors = {
    security: '#EF4444', performance: '#F97316', architecture: '#6366F1',
    style: '#9AA3B8', test_coverage: '#22C55E',
  }
  const color = colors[type] || 'var(--text-3)'
  return (
    <span style={{
      fontSize: 10, fontWeight: 600, color, background: `${color}20`,
      padding: '2px 6px', borderRadius: 4, whiteSpace: 'nowrap', letterSpacing: '0.04em',
    }}>{type?.replace('_', ' ').toUpperCase()}</span>
  )
}

function Field({ label, children, style }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, ...style }}>
      <label style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-2)', letterSpacing: '0.03em' }}>
        {label.toUpperCase()}
      </label>
      {children}
    </div>
  )
}

const inputStyle = {
  background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8,
  padding: '9px 12px', color: 'var(--text)', fontSize: 13, width: '100%',
  outline: 'none', transition: 'border-color 0.15s',
}

const btnStyle = (loading) => ({
  background: loading ? 'var(--surface2)' : 'var(--accent)',
  color: loading ? 'var(--text-3)' : '#fff',
  border: 'none', borderRadius: 8, padding: '10px 24px',
  fontSize: 13, fontWeight: 500, transition: 'background 0.15s',
  opacity: loading ? 0.7 : 1,
})

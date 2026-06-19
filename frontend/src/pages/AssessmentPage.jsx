import { useState, useEffect, useRef } from 'react'
import { api } from '../lib/api.js'

const SEV_COLORS = { critical: '#EF4444', high: '#F97316', medium: '#EAB308', low: '#6366F1' }

export default function AssessmentPage() {
  const [repos, setRepos] = useState([])
  const [repo, setRepo] = useState('')
  const [models, setModels] = useState(null)
  const [selectedModel, setSelectedModel] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const [history, setHistory] = useState([])
  const jobRef = useRef(null)
  useEffect(() => () => { jobRef.current = null }, [])

  useEffect(() => {
    api.listRepos().then(r => {
      const list = r.repos || []
      setRepos(list)
      if (list.length) setRepo(r => r || list[0])
    }).catch(() => {})

    api.getSettings().then(s => {
      const slots = s.openrouter_models || []
      setModels(slots)
      setSelectedModel(slots[0] || null)
    }).catch(() => {})
  }, [])

  // Reload history whenever the selected repo changes.
  useEffect(() => {
    if (!repo) return
    api.listAssessments({ repo, limit: 10 })
      .then(r => setHistory(r.assessments || []))
      .catch(() => {})
  }, [repo])

  async function submit() {
    if (!repo) return
    setLoading(true); setError(null); setResult(null)
    try {
      const { id } = await api.createAssessment({
        repo,
        model: selectedModel?.model || undefined,
        provider: selectedModel?.provider || undefined,
      })
      jobRef.current = id
      api.runAssessment(id).catch(() => {})
      poll(id)
    } catch (e) {
      setError(e.message)
      setLoading(false)
    }
  }

  async function poll(id) {
    if (jobRef.current !== id) return
    let job
    try {
      job = await api.getAssessmentJob(id)
    } catch (e) {
      if (jobRef.current === id) { setError(e.message); setLoading(false) }
      return
    }
    if (jobRef.current !== id) return
    if (job.status === 'done') {
      setResult(job.result)
      setHistory(h => [job.result, ...h].slice(0, 10))
      setLoading(false)
    } else if (job.status === 'error') {
      setError(job.error || 'Assessment failed')
      setLoading(false)
    } else {
      setTimeout(() => poll(id), 1500)
    }
  }

  return (
    <div style={{ maxWidth: 900 }}>
      <h1 style={{ fontSize: 22, fontWeight: 600, marginBottom: 4 }}>Project Assessment</h1>
      <p style={{ color: 'var(--text-2)', marginBottom: 28 }}>
        Select a repository to generate a high-level analysis: what it does, its architecture, key
        components, and any notable security concerns.
      </p>

      {/* Controls */}
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', marginBottom: 16, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 260 }}>
          <label style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-2)', letterSpacing: '0.03em' }}>
            REPOSITORY
          </label>
          {repos.length > 0 ? (
            <select value={repo} onChange={e => setRepo(e.target.value)} style={inputStyle}>
              {!repos.includes(repo) && <option value={repo}>{repo || 'Select a repo'}</option>}
              {repos.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
          ) : (
            <input
              value={repo}
              onChange={e => setRepo(e.target.value)}
              placeholder="owner/repo"
              style={inputStyle}
            />
          )}
        </div>

        <button onClick={submit} disabled={loading || !repo} style={btnStyle(loading || !repo)}>
          {loading ? '⟳ Analysing…' : '▶ Run Assessment'}
        </button>

        {error && <span style={{ fontSize: 13, color: 'var(--red)' }}>⚠ {error}</span>}
      </div>

      {/* Model picker */}
      {models && models.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 20 }}>
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

      {/* Loading pulse */}
      {loading && (
        <div style={{
          padding: '24px', background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: 10, marginBottom: 20, color: 'var(--text-2)', fontSize: 13,
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <span style={{ animation: 'spin 1.2s linear infinite', display: 'inline-block' }}>⟳</span>
          <style>{`@keyframes spin { to { transform: rotate(360deg) } }`}</style>
          Fetching repository files and running analysis — this takes 20–60 seconds…
        </div>
      )}

      {/* Result */}
      {result && <AssessmentResult key={result.id ?? result.created_at ?? result.repo} result={result} />}

      {/* History */}
      {history.length > 0 && !loading && (
        <div style={{ marginTop: 32 }}>
          <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: '0.04em', color: 'var(--text-3)', marginBottom: 10, textTransform: 'uppercase' }}>
            Past Assessments for {repo}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {history.map((h, i) => (
              <button
                key={i}
                onClick={() => setResult(h)}
                style={{
                  background: result === h ? 'var(--accent-glow)' : 'var(--surface)',
                  border: `1px solid ${result === h ? 'var(--accent)' : 'var(--border)'}`,
                  borderRadius: 8, padding: '10px 16px', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', gap: 12, textAlign: 'left',
                }}
              >
                <span style={{ fontSize: 11, color: 'var(--text-3)', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap' }}>
                  {h.created_at ? new Date(h.created_at).toLocaleString() : '—'}
                </span>
                <span style={{ fontSize: 13, color: 'var(--text-2)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {h.summary}
                </span>
                {h.model && (
                  <span style={{
                    fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--text-3)',
                    background: 'var(--surface2)', border: '1px solid var(--border)',
                    borderRadius: 4, padding: '2px 6px', whiteSpace: 'nowrap',
                  }}>{h.model}</span>
                )}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function AssessmentResult({ result }) {
  const vulnCount = result.vulnerabilities?.length || 0
  const criticalCount = result.vulnerabilities?.filter(v => v.severity === 'critical' || v.severity === 'high').length || 0

  const [selVulns, setSelVulns] = useState(new Set())
  const [issuing, setIssuing] = useState(false)
  const [issueUrl, setIssueUrl] = useState(null)
  const [issueErr, setIssueErr] = useState(null)

  const toggleVuln = (i) => {
    const n = new Set(selVulns)
    n.has(i) ? n.delete(i) : n.add(i)
    setSelVulns(n)
  }

  function buildIssueBody() {
    const vulns = [...selVulns].sort((a, b) => a - b)
      .map(i => result.vulnerabilities[i]).filter(Boolean)
    const lines = ['## 🔍 Assessment Findings', '']
    vulns.forEach(v => {
      lines.push(`### [${(v.severity || '').toUpperCase()}] ${v.title}`)
      lines.push(v.description)
      if (v.recommendation) lines.push(`\n> 💡 ${v.recommendation}`)
      lines.push('')
    })
    lines.push(`—\nFrom assessment of \`${result.repo}\``)
    return lines.join('\n')
  }

  function issueTitle() {
    const vulns = [...selVulns].sort((a, b) => a - b)
      .map(i => result.vulnerabilities[i]).filter(Boolean)
    if (vulns.length === 1) {
      return `[${(vulns[0].severity || '').toUpperCase()}] ${vulns[0].title}`.slice(0, 250)
    }
    return `Security findings — ${result.repo} (${vulns.length})`
  }

  async function postIssue() {
    const body = buildIssueBody()
    if (!selVulns.size) return
    setIssuing(true); setIssueErr(null); setIssueUrl(null)
    try {
      const res = await api.createIssue({ repo: result.repo, title: issueTitle(), body })
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
        padding: '20px 24px', marginBottom: 14,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 600, fontSize: 15, color: 'var(--text)' }}>{result.repo}</span>
          {result.model && (
            <span style={{
              fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--text-3)',
              background: 'var(--surface2)', border: '1px solid var(--border)',
              borderRadius: 5, padding: '2px 7px',
            }}>{result.model}</span>
          )}
          {vulnCount > 0 && (
            <span style={{
              fontSize: 11, fontWeight: 600, color: criticalCount > 0 ? '#EF4444' : '#EAB308',
              background: criticalCount > 0 ? '#EF444420' : '#EAB30820',
              borderRadius: 5, padding: '2px 7px',
            }}>{criticalCount > 0 ? `${criticalCount} high/critical` : `${vulnCount} vuln${vulnCount > 1 ? 's' : ''}`}</span>
          )}
        </div>
        <p style={{ margin: 0, fontSize: 14, color: 'var(--text)', lineHeight: 1.6 }}>{result.summary}</p>
        {result.purpose && (
          <p style={{ margin: '8px 0 0', fontSize: 13, color: 'var(--text-2)', lineHeight: 1.5 }}>{result.purpose}</p>
        )}
      </div>

      {/* Tech stack */}
      {result.tech_stack?.length > 0 && (
        <Section title="Tech Stack">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {result.tech_stack.map((t, i) => (
              <span key={i} style={{
                fontSize: 12, color: 'var(--accent)', background: 'var(--accent-glow)',
                border: '1px solid var(--accent)', borderRadius: 20,
                padding: '4px 12px', fontWeight: 500,
              }}>{t}</span>
            ))}
          </div>
        </Section>
      )}

      {/* Key components */}
      {result.key_components?.length > 0 && (
        <Section title={`Key Components (${result.key_components.length})`}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {result.key_components.map((c, i) => (
              <div key={i} style={{
                padding: '12px 16px', background: 'var(--surface2)',
                borderRadius: 8, borderLeft: '3px solid var(--accent)',
              }}>
                <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4, color: 'var(--text)' }}>{c.name}</div>
                <div style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.5, marginBottom: c.files?.length ? 8 : 0 }}>
                  {c.role}
                </div>
                {c.files?.length > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {c.files.map((f, j) => (
                      <code key={j} style={{
                        fontSize: 11, color: 'var(--text-3)', background: 'var(--surface)',
                        border: '1px solid var(--border)', borderRadius: 4, padding: '2px 6px',
                        fontFamily: 'var(--font-mono)',
                      }}>{f}</code>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Vulnerabilities */}
      {result.vulnerabilities?.length > 0 ? (
        <>
          {/* Action bar */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14,
            padding: '10px 16px', background: 'var(--surface)',
            border: '1px solid var(--border)', borderRadius: 10,
          }}>
            <span style={{ fontSize: 13, color: 'var(--text-2)' }}>
              {selVulns.size} selected — tick findings to create a GitHub issue on <code style={{ fontFamily: 'var(--font-mono)' }}>{result.repo}</code>
            </span>
            <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
              <button onClick={postIssue} disabled={!selVulns.size || issuing} style={{
                background: (!selVulns.size || issuing) ? 'var(--surface2)' : 'var(--accent)',
                color: (!selVulns.size || issuing) ? 'var(--text-3)' : '#fff',
                border: 'none', borderRadius: 8, padding: '8px 16px',
                fontSize: 13, fontWeight: 500, cursor: (!selVulns.size || issuing) ? 'default' : 'pointer',
              }}>{issuing ? 'Creating…' : '🐛 Create issue'}</button>
              {issueUrl && <a href={issueUrl} target="_blank" rel="noreferrer" style={{ fontSize: 12, color: 'var(--green)' }}>✓ Created ↗</a>}
              {issueErr && <span style={{ fontSize: 12, color: 'var(--red)' }}>⚠ {issueErr}</span>}
            </div>
          </div>

          <Section
            title={`Security Findings (${result.vulnerabilities.length})`}
            allSelected={selVulns.size === result.vulnerabilities.length}
            onSelectAll={() => {
              if (selVulns.size === result.vulnerabilities.length) setSelVulns(new Set())
              else setSelVulns(new Set(result.vulnerabilities.map((_, i) => i)))
            }}
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {result.vulnerabilities.map((v, i) => {
                const color = SEV_COLORS[v.severity] || 'var(--text-2)'
                return (
                  <div key={i} style={{
                    border: `1px solid ${color}33`, borderLeft: `3px solid ${color}`,
                    borderRadius: '0 8px 8px 0', padding: '12px 16px',
                    background: `${color}08`,
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                      <input type="checkbox" checked={selVulns.has(i)} onChange={() => toggleVuln(i)}
                        style={{ accentColor: 'var(--accent)', cursor: 'pointer' }} />
                      <span style={{
                        fontSize: 10, fontWeight: 600, letterSpacing: '0.05em',
                        color, background: `${color}20`, padding: '2px 6px', borderRadius: 4,
                      }}>{v.severity.toUpperCase()}</span>
                      <span style={{ fontWeight: 600, fontSize: 13, color: 'var(--text)' }}>{v.title}</span>
                    </div>
                    <div style={{ fontSize: 13, color: 'var(--text)', marginBottom: 6, lineHeight: 1.5 }}>{v.description}</div>
                    <div style={{ fontSize: 12, color: 'var(--text-2)', paddingLeft: 10, borderLeft: '2px solid var(--border2)', lineHeight: 1.5 }}>
                      💡 {v.recommendation}
                    </div>
                  </div>
                )
              })}
            </div>
          </Section>
        </>
      ) : result.vulnerabilities && (
        <Section title="Security Findings">
          <div style={{ fontSize: 13, color: 'var(--text-2)' }}>No notable vulnerabilities found in the reviewed files.</div>
        </Section>
      )}
    </div>
  )
}

function Section({ title, children, onSelectAll, allSelected }) {
  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--border)',
      borderRadius: 10, padding: '16px 20px', marginBottom: 14,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12 }}>
        <div style={{
          fontSize: 12, fontWeight: 600, letterSpacing: '0.04em',
          color: 'var(--text-2)', textTransform: 'uppercase', flex: 1,
        }}>
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

const inputStyle = {
  background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8,
  padding: '9px 12px', color: 'var(--text)', fontSize: 13, width: '100%',
  outline: 'none', transition: 'border-color 0.15s',
}

const btnStyle = (disabled) => ({
  background: disabled ? 'var(--surface2)' : 'var(--accent)',
  color: disabled ? 'var(--text-3)' : '#fff',
  border: 'none', borderRadius: 8, padding: '10px 24px',
  fontSize: 13, fontWeight: 500, transition: 'background 0.15s',
  opacity: disabled ? 0.7 : 1, cursor: disabled ? 'default' : 'pointer',
  alignSelf: 'flex-end',
})

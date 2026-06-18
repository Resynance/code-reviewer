import { useState, useEffect } from 'react'
import { api } from '../lib/api.js'

const SAMPLE_DIFF = `diff --git a/src/auth/middleware.py b/src/auth/middleware.py
index 8a3f2c1..d4e9b7a 100644
--- a/src/auth/middleware.py
+++ b/src/auth/middleware.py
@@ -12,6 +12,18 @@ class AuthMiddleware:
     def __init__(self, secret_key: str):
         self.secret_key = secret_key
 
+    def validate_token(self, token: str) -> dict:
+        try:
+            payload = jwt.decode(token, self.secret_key, algorithms=["HS256"])
+            return payload
+        except jwt.ExpiredSignatureError:
+            raise HTTPException(status_code=401, detail="Token expired")
+        except jwt.InvalidTokenError:
+            raise HTTPException(status_code=401, detail="Invalid token")
+
+    def get_user_id(self, token: str) -> str:
+        payload = self.validate_token(token)
+        return payload.get("sub")
+
     def __call__(self, request: Request, call_next):
         token = request.headers.get("Authorization", "").replace("Bearer ", "")
         if not token:`

export default function ReviewPage() {
  const [form, setForm] = useState({
    pr_number: 201,
    repo: 'my-org/my-repo',
    title: 'Add JWT validation to auth middleware',
    description: 'Implements token validation and user extraction methods on the auth middleware class.',
    diff: SAMPLE_DIFF,
    author: 'dev',
    base_branch: 'main',
    files_changed: ['src/auth/middleware.py'],
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

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  // Load configured repos so the repo field can be a dropdown. If the form
  // still holds the placeholder default, switch it to the first real repo.
  useEffect(() => {
    api.listRepos().then(r => {
      const list = r.repos || []
      setRepos(list)
      if (list.length) {
        setForm(f => (f.repo === 'my-org/my-repo' ? { ...f, repo: list[0] } : f))
      }
    }).catch(() => {})
  }, [])

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
    } catch (e) {
      setPrError(e.message)
    } finally {
      setLoadingPr(false)
    }
  }

  async function submit() {
    setLoading(true)
    setResult(null)
    setError(null)
    try {
      const res = await api.review({
        ...form,
        files_changed: form.files_changed.filter(Boolean),
      })
      setResult(res)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
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
        {repos.length > 0 && (
          <Field label="Load a pull request" style={{ gridColumn: '1 / -1' }}>
            <select value={selectedPr} onChange={e => loadPr(e.target.value)} style={inputStyle} disabled={loadingPrs || loadingPr}>
              <option value="">
                {loadingPrs ? 'Loading pull requests…' : loadingPr ? 'Loading PR…' : 'Select a pull request…'}
              </option>
              {prList.map(pr => (
                <option key={pr.number} value={pr.number}>
                  #{pr.number}  {pr.state === 'open' ? '●' : '○'} {pr.state}{pr.draft ? ' · draft' : ''}  —  {pr.title}
                </option>
              ))}
            </select>
            {prError && <div style={{ fontSize: 12, color: 'var(--red)', marginTop: 4 }}>⚠ {prError}</div>}
          </Field>
        )}
        <Field label="PR Number" >
          <input value={form.pr_number} onChange={e => set('pr_number', Number(e.target.value))}
            type="number" style={inputStyle} />
        </Field>
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

      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 28 }}>
        <button onClick={submit} disabled={loading} style={btnStyle(loading)}>
          {loading ? '⟳ Reviewing…' : '▶ Run Review'}
        </button>
        {error && <span style={{ color: 'var(--red)', fontSize: 13 }}>⚠ {error}</span>}
      </div>

      {result && <ReviewResult result={result} />}
    </div>
  )
}

function ReviewResult({ result }) {
  const statusColor = result.approved ? 'var(--green)' : 'var(--yellow)'
  const statusLabel = result.approved ? 'Approved' : 'Changes Requested'

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
          <div style={{ fontWeight: 600, fontSize: 15, color: statusColor }}>{statusLabel}</div>
          <div style={{ color: 'var(--text-2)', fontSize: 13, marginTop: 2 }}>{result.summary}</div>
        </div>
        <ConfidencePill value={result.confidence} />
      </div>

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

      {/* Issues */}
      {result.issues?.length > 0 && (
        <Section title={`🔍 Issues (${result.issues.length})`}>
          {result.issues.map((issue, i) => (
            <IssueCard key={i} issue={issue} />
          ))}
        </Section>
      )}

      {/* Suggestions */}
      {result.suggestions?.length > 0 && (
        <Section title="💭 Suggestions">
          {result.suggestions.map((s, i) => (
            <div key={i} style={{
              padding: '10px 14px', background: 'var(--surface2)', borderRadius: 6, marginBottom: 6,
              display: 'flex', gap: 10, alignItems: 'flex-start',
            }}>
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

function IssueCard({ issue }) {
  const sevColors = { critical: '#EF4444', high: '#F97316', medium: '#EAB308', low: '#6366F1' }
  const color = sevColors[issue.severity] || 'var(--text-2)'
  return (
    <div style={{
      border: `1px solid ${color}33`, borderLeft: `3px solid ${color}`,
      borderRadius: '0 8px 8px 0', padding: '12px 16px', marginBottom: 10,
      background: `${color}08`,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
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
      <div style={{ fontSize: 12, color: 'var(--text-2)', paddingLeft: 10, borderLeft: '2px solid var(--border2)' }}>
        💡 {issue.suggestion}
      </div>
    </div>
  )
}

function Section({ title, children, accent }) {
  return (
    <div style={{
      background: 'var(--surface)', border: `1px solid ${accent ? 'var(--accent)' : 'var(--border)'}`,
      borderRadius: 10, padding: '16px 20px', marginBottom: 16,
    }}>
      <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: '0.04em', color: 'var(--text-2)', marginBottom: 12, textTransform: 'uppercase' }}>
        {title}
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

import { useState, useEffect, useCallback } from 'react'
import { api } from '../lib/api.js'
import { useMediaQuery } from '../lib/useMediaQuery.js'

export default function ReviewsPage() {
  const isMobile = useMediaQuery('(max-width: 860px)')
  const [reviews, setReviews] = useState([])
  const [loading, setLoading] = useState(true)
  const [repos, setRepos] = useState([])
  const [repoFilter, setRepoFilter] = useState('')

  useEffect(() => {
    api.listRepos().then(r => setRepos(r.repos || [])).catch(() => {})
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.reviews({ repo: repoFilter, limit: 100 })
      setReviews(res.reviews || [])
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }, [repoFilter])

  useEffect(() => { load() }, [load])

  return (
    <div style={{ maxWidth: 880, width: '100%' }}>
      <div style={{ display: 'flex', alignItems: isMobile ? 'stretch' : 'center', flexDirection: isMobile ? 'column' : 'row', gap: 12, justifyContent: 'space-between', marginBottom: 24 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 600, marginBottom: 4 }}>Review History</h1>
          <p style={{ color: 'var(--text-2)' }}>Every review run, newest first — from the UI and GitHub webhooks.</p>
        </div>
        <select value={repoFilter} onChange={e => setRepoFilter(e.target.value)} style={{
          background: 'var(--surface)', border: '1px solid var(--border)',
          color: 'var(--text)', borderRadius: 8, padding: '9px 12px', fontSize: 13, outline: 'none', width: isMobile ? '100%' : 'auto',
        }}>
          <option value="">All repos</option>
          {repos.map(r => <option key={r} value={r}>{r}</option>)}
        </select>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--text-3)' }}>Loading…</div>
      ) : reviews.length === 0 ? (
        <div style={{
          textAlign: 'center', padding: '60px 40px', background: 'var(--surface)',
          border: '1px dashed var(--border2)', borderRadius: 12, color: 'var(--text-2)',
        }}>
          No reviews yet. Run a review and it'll show up here.
        </div>
      ) : (
        reviews.map(r => <ReviewRow key={r.id} review={r} />)
      )}
    </div>
  )
}

function ReviewRow({ review }) {
  const [open, setOpen] = useState(false)
  const approved = review.approved
  const color = approved ? 'var(--green)' : 'var(--yellow)'
  const pct = Math.round((review.confidence || 0) * 100)
  const when = review.created_at ? new Date(review.created_at).toLocaleString() : ''

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, marginBottom: 10, overflow: 'hidden' }}>
      <div onClick={() => setOpen(o => !o)} style={{ padding: '14px 18px', display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer' }}>
        <span style={{ fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--accent)', minWidth: 48 }}>#{review.pr_number}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {review.title || review.summary || '(no title)'}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 2 }}>{review.repo} · {when} · {review.source || 'api'}</div>
        </div>
        <span style={{ fontSize: 11, color, background: `${color}18`, padding: '2px 8px', borderRadius: 20, whiteSpace: 'nowrap' }}>
          {approved ? 'Approved' : 'Changes'}
        </span>
        <span style={{ fontSize: 12, fontWeight: 600, color: pct >= 80 ? 'var(--green)' : pct >= 60 ? 'var(--yellow)' : 'var(--red)', fontFamily: 'var(--font-mono)', minWidth: 42, textAlign: 'right' }}>{pct}%</span>
        <span style={{ color: 'var(--text-3)', fontSize: 12 }}>{open ? '▾' : '▸'}</span>
      </div>

      {open && (
        <div style={{ padding: '0 18px 16px', borderTop: '1px solid var(--border)' }}>
          {review.summary && (
            <div style={{ paddingTop: 14, fontSize: 13, color: 'var(--text-2)' }}>{review.summary}</div>
          )}
          {(review.issues || []).length > 0 && (
            <Section title={`Issues (${review.issues.length})`}>
              {review.issues.map((it, i) => (
                <div key={i} style={{ fontSize: 12, color: 'var(--text-2)', marginBottom: 6 }}>
                  <span style={{ color: sevColor(it.severity), fontWeight: 600, textTransform: 'uppercase', fontSize: 10, marginRight: 6 }}>{it.severity}</span>
                  <code style={{ color: 'var(--text-3)' }}>{it.file}</code> — {it.description}
                </div>
              ))}
            </Section>
          )}
          {(review.suggestions || []).length > 0 && (
            <Section title="Suggestions">
              {review.suggestions.map((s, i) => (
                <div key={i} style={{ fontSize: 12, color: 'var(--text-2)', marginBottom: 5 }}>
                  <span style={{ color: 'var(--text-3)', textTransform: 'uppercase', fontSize: 10, marginRight: 6 }}>{(s.type || '').replace('_', ' ')}</span>
                  {s.description}
                </div>
              ))}
            </Section>
          )}
          {(review.past_decisions || []).length > 0 && (
            <Section title="Past decisions applied">
              {review.past_decisions.map((d, i) => (
                <div key={i} style={{ fontSize: 12, color: 'var(--text-2)', marginBottom: 5 }}>
                  <span style={{ color: 'var(--accent)' }}>{d.ref}</span> — {d.how_applied}
                </div>
              ))}
            </Section>
          )}
        </div>
      )}
    </div>
  )
}

function Section({ title, children }) {
  return (
    <div style={{ marginTop: 14 }}>
      <div style={{ fontSize: 11, fontWeight: 600, letterSpacing: '0.04em', color: 'var(--text-3)', textTransform: 'uppercase', marginBottom: 8 }}>{title}</div>
      {children}
    </div>
  )
}

function sevColor(sev) {
  return { critical: '#EF4444', high: '#F97316', medium: '#EAB308', low: '#6366F1' }[sev] || 'var(--text-3)'
}

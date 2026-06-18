import { useState, useEffect, useCallback } from 'react'
import { api } from '../lib/api.js'

export default function DecisionsPage() {
  const [decisions, setDecisions] = useState([])
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [showAdd, setShowAdd] = useState(false)
  const [deleteId, setDeleteId] = useState(null)
  const [repos, setRepos] = useState([])
  const [repoFilter, setRepoFilter] = useState('')

  useEffect(() => {
    api.listRepos().then(r => setRepos(r.repos || [])).catch(() => {})
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.listDecisions(30, repoFilter)
      setDecisions(res.decisions || [])
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }, [repoFilter])

  // Reloads whenever the repo filter changes (load identity depends on it).
  useEffect(() => { load() }, [load])

  async function search() {
    if (!searchQuery.trim()) return load()
    setSearching(true)
    try {
      const res = await api.searchDecisions(searchQuery, 15, repoFilter)
      setDecisions(res.results || [])
    } finally {
      setSearching(false)
    }
  }

  async function remove(docId) {
    await api.deleteDecision(docId)
    setDeleteId(null)
    load()
  }

  return (
    <div style={{ maxWidth: 860 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 600, marginBottom: 4 }}>Decision Store</h1>
          <p style={{ color: 'var(--text-2)' }}>Past PR decisions and architectural choices the reviewer has learned from.</p>
        </div>
        <button onClick={() => setShowAdd(true)} style={{
          background: 'var(--accent)', color: '#fff', border: 'none',
          borderRadius: 8, padding: '9px 18px', fontSize: 13, fontWeight: 500,
        }}>+ Add Decision</button>
      </div>

      {/* Search */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
        <input value={searchQuery} onChange={e => setSearchQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && search()}
          placeholder="Search decisions by topic, file, or concept…"
          style={{
            flex: 1, background: 'var(--surface)', border: '1px solid var(--border)',
            borderRadius: 8, padding: '9px 14px', color: 'var(--text)', fontSize: 13, outline: 'none',
          }} />
        <select value={repoFilter} onChange={e => setRepoFilter(e.target.value)} style={{
          background: 'var(--surface)', border: '1px solid var(--border)',
          color: 'var(--text)', borderRadius: 8, padding: '9px 12px', fontSize: 13, outline: 'none',
        }}>
          <option value="">All</option>
          <option value="*">Global</option>
          {repos.map(r => <option key={r} value={r}>{r}</option>)}
        </select>
        <button onClick={search} disabled={searching} style={{
          background: 'var(--surface2)', border: '1px solid var(--border)',
          color: 'var(--text)', borderRadius: 8, padding: '9px 16px', fontSize: 13,
        }}>{searching ? '⟳' : 'Search'}</button>
        {searchQuery && (
          <button onClick={() => { setSearchQuery(''); load() }} style={{
            background: 'transparent', border: '1px solid var(--border)',
            color: 'var(--text-2)', borderRadius: 8, padding: '9px 12px', fontSize: 13,
          }}>✕</button>
        )}
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--text-3)' }}>Loading decisions…</div>
      ) : decisions.length === 0 ? (
        <EmptyState onAdd={() => setShowAdd(true)} />
      ) : (
        <div>
          {decisions.map((d, i) => (
            <DecisionCard key={d.doc_id || i} decision={d}
              onDelete={() => setDeleteId(d.doc_id)} />
          ))}
        </div>
      )}

      {showAdd && <AddModal repos={repos} onClose={() => setShowAdd(false)} onSaved={load} />}
      {deleteId && (
        <ConfirmModal
          message="Remove this decision from the store? This cannot be undone."
          onConfirm={() => remove(deleteId)}
          onCancel={() => setDeleteId(null)}
        />
      )}
    </div>
  )
}

function ScopeTag({ repo }) {
  const isGlobal = repo === '*'
  const color = isGlobal ? 'var(--accent)' : 'var(--text-2)'
  return (
    <span style={{
      fontSize: 11, fontFamily: 'var(--font-mono)', color, background: `${color}18`,
      padding: '2px 8px', borderRadius: 20, whiteSpace: 'nowrap', maxWidth: 160,
      overflow: 'hidden', textOverflow: 'ellipsis',
    }}>{isGlobal ? 'global' : repo}</span>
  )
}

function DecisionCard({ decision, onDelete }) {
  const [expanded, setExpanded] = useState(false)
  const outcomeColor = {
    approved_and_merged: 'var(--green)',
    changes_requested: 'var(--yellow)',
    merged_no_review: 'var(--blue)',
    closed_without_merge: 'var(--red)',
  }[decision.outcome] || 'var(--text-3)'

  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10,
      marginBottom: 10, overflow: 'hidden',
    }}>
      <div style={{
        padding: '14px 18px', display: 'flex', alignItems: 'center', gap: 12,
        cursor: 'pointer',
      }} onClick={() => setExpanded(e => !e)}>
        <span style={{ fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--accent)', minWidth: 70 }}>
          {decision.ref}
        </span>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text)' }}>{decision.summary}</div>
        </div>
        {decision.repo && <ScopeTag repo={decision.repo} />}
        <span style={{
          fontSize: 11, color: outcomeColor, background: `${outcomeColor}18`,
          padding: '2px 8px', borderRadius: 20, whiteSpace: 'nowrap',
        }}>{decision.outcome?.replace(/_/g, ' ')}</span>
        {decision.score !== undefined && (
          <span style={{ fontSize: 11, color: 'var(--text-3)', minWidth: 50, textAlign: 'right' }}>
            {Math.round(decision.score * 100)}% sim
          </span>
        )}
        <span style={{ color: 'var(--text-3)', fontSize: 12 }}>{expanded ? '▾' : '▸'}</span>
      </div>

      {expanded && (
        <div style={{ padding: '0 18px 16px', borderTop: '1px solid var(--border)' }}>
          <div style={{ paddingTop: 14, fontSize: 12, color: 'var(--text-2)', lineHeight: 1.6 }}>
            <strong style={{ color: 'var(--text-3)', display: 'block', marginBottom: 4 }}>REASONING</strong>
            <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'inherit' }}>{decision.reasoning}</pre>
          </div>
          {decision.date && (
            <div style={{ marginTop: 10, fontSize: 11, color: 'var(--text-3)' }}>
              Stored: {new Date(decision.date).toLocaleDateString()}
            </div>
          )}
          <div style={{ marginTop: 12, display: 'flex', justifyContent: 'flex-end' }}>
            <button onClick={(e) => { e.stopPropagation(); onDelete() }} style={{
              background: 'transparent', border: '1px solid var(--red)33', color: 'var(--red)',
              borderRadius: 6, padding: '5px 12px', fontSize: 12,
            }}>Remove</button>
          </div>
        </div>
      )}
    </div>
  )
}

function EmptyState({ onAdd }) {
  return (
    <div style={{
      textAlign: 'center', padding: '60px 40px', background: 'var(--surface)',
      border: '1px dashed var(--border2)', borderRadius: 12,
    }}>
      <div style={{ fontSize: 36, marginBottom: 12 }}>◈</div>
      <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>No decisions stored yet</div>
      <div style={{ color: 'var(--text-2)', marginBottom: 20 }}>
        Run a backfill on your repo to import past PR decisions, or add one manually.
      </div>
      <button onClick={onAdd} style={{
        background: 'var(--accent)', color: '#fff', border: 'none',
        borderRadius: 8, padding: '9px 20px', fontSize: 13, fontWeight: 500,
      }}>Add your first decision</button>
    </div>
  )
}

function AddModal({ repos = [], onClose, onSaved }) {
  const [form, setForm] = useState({ ref: '', summary: '', reasoning: '', outcome: 'approved_and_merged', repo: '*' })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  async function save() {
    if (!form.ref || !form.summary) { setError('Ref and summary are required.'); return }
    setSaving(true)
    try {
      const { repo, ...rest } = form
      // Tag the decision with a repo (via metadata) so it can be filtered later.
      await api.addDecision({ ...rest, metadata: repo ? { repo } : {} })
      onSaved()
      onClose()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Overlay onClose={onClose}>
      <div style={{ width: 520 }}>
        <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 20 }}>Add Decision</h2>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <ModalField label="Reference (e.g. PR #142 or ADR-007)">
            <input value={form.ref} onChange={e => set('ref', e.target.value)} style={modalInput} />
          </ModalField>
          <ModalField label="Summary">
            <input value={form.summary} onChange={e => set('summary', e.target.value)} style={modalInput} placeholder="What was decided?" />
          </ModalField>
          <ModalField label="Reasoning">
            <textarea value={form.reasoning} onChange={e => set('reasoning', e.target.value)}
              rows={4} style={{ ...modalInput, resize: 'vertical' }} placeholder="Why was this decision made?" />
          </ModalField>
          <ModalField label="Outcome">
            <select value={form.outcome} onChange={e => set('outcome', e.target.value)} style={modalInput}>
              <option value="approved_and_merged">Approved and merged</option>
              <option value="changes_requested">Changes requested</option>
              <option value="closed_without_merge">Closed without merge</option>
            </select>
          </ModalField>
          <ModalField label="Scope">
            <select value={form.repo} onChange={e => set('repo', e.target.value)} style={modalInput}>
              <option value="*">Global (all repos)</option>
              {repos.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
          </ModalField>
        </div>
        {error && <div style={{ color: 'var(--red)', fontSize: 12, marginTop: 10 }}>{error}</div>}
        <div style={{ display: 'flex', gap: 10, marginTop: 20, justifyContent: 'flex-end' }}>
          <button onClick={onClose} style={{ background: 'transparent', border: '1px solid var(--border)', color: 'var(--text-2)', borderRadius: 8, padding: '9px 16px', fontSize: 13 }}>Cancel</button>
          <button onClick={save} disabled={saving} style={{ background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 8, padding: '9px 20px', fontSize: 13, fontWeight: 500 }}>
            {saving ? 'Saving…' : 'Save Decision'}
          </button>
        </div>
      </div>
    </Overlay>
  )
}

function ConfirmModal({ message, onConfirm, onCancel }) {
  return (
    <Overlay onClose={onCancel}>
      <div style={{ width: 380, textAlign: 'center' }}>
        <div style={{ fontSize: 32, marginBottom: 12 }}>⚠</div>
        <div style={{ fontSize: 14, color: 'var(--text-2)', marginBottom: 24 }}>{message}</div>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'center' }}>
          <button onClick={onCancel} style={{ background: 'transparent', border: '1px solid var(--border)', color: 'var(--text-2)', borderRadius: 8, padding: '9px 20px', fontSize: 13 }}>Cancel</button>
          <button onClick={onConfirm} style={{ background: 'var(--red)', color: '#fff', border: 'none', borderRadius: 8, padding: '9px 20px', fontSize: 13 }}>Remove</button>
        </div>
      </div>
    </Overlay>
  )
}

function Overlay({ children, onClose }) {
  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex',
      alignItems: 'center', justifyContent: 'center', zIndex: 100,
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: '28px 32px',
      }}>
        {children}
      </div>
    </div>
  )
}

function ModalField({ label, children }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      <label style={{ fontSize: 11, fontWeight: 500, color: 'var(--text-3)', letterSpacing: '0.04em', textTransform: 'uppercase' }}>{label}</label>
      {children}
    </div>
  )
}

const modalInput = {
  background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 7,
  padding: '9px 12px', color: 'var(--text)', fontSize: 13, width: '100%', outline: 'none',
}

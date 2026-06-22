import { useEffect, useState } from 'react'
import { api } from '../lib/api.js'
import { useMediaQuery } from '../lib/useMediaQuery.js'

const FILTER_STYLE = {
  background: 'var(--surface)',
  border: '1px solid var(--border)',
  color: 'var(--text)',
  borderRadius: 8,
  padding: '9px 12px',
  fontSize: 13,
  outline: 'none',
}

export default function QueuePage() {
  const isMobile = useMediaQuery('(max-width: 860px)')
  const [jobs, setJobs] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [status, setStatus] = useState('')
  const [jobType, setJobType] = useState('')
  const [openJobs, setOpenJobs] = useState({})

  async function load({ quiet = false } = {}) {
    if (!quiet) setLoading(true)
    setError(null)
    try {
      const res = await api.listQueueJobs({ limit: 100, status, jobType })
      setJobs(res.jobs || [])
    } catch (e) {
      setError(e.message)
    } finally {
      if (!quiet) setLoading(false)
    }
  }

  useEffect(() => { load() }, [status, jobType])

  useEffect(() => {
    const timer = setInterval(() => load({ quiet: true }), 3000)
    return () => clearInterval(timer)
  }, [status, jobType])

  return (
    <div style={{ maxWidth: 1100, width: '100%' }}>
      <h1 style={{ fontSize: 22, fontWeight: 600, marginBottom: 4 }}>Local Queue</h1>
      <p style={{ color: 'var(--text-2)', marginBottom: 24 }}>
        Review and assessment jobs queued for local execution, including claim state, agentic routing, and worker errors.
      </p>

      <div style={{ display: 'flex', gap: 10, flexDirection: isMobile ? 'column' : 'row', alignItems: isMobile ? 'stretch' : 'center', marginBottom: 18 }}>
        <select value={status} onChange={e => setStatus(e.target.value)} style={{ ...FILTER_STYLE, width: isMobile ? '100%' : 180 }}>
          <option value="">All statuses</option>
          <option value="queued">Queued</option>
          <option value="running">Running</option>
          <option value="done">Done</option>
          <option value="error">Error</option>
        </select>
        <select value={jobType} onChange={e => setJobType(e.target.value)} style={{ ...FILTER_STYLE, width: isMobile ? '100%' : 180 }}>
          <option value="">All job types</option>
          <option value="review">Review</option>
          <option value="assessment">Assessment</option>
        </select>
        <button onClick={() => load()} style={buttonStyle(false)}>Refresh</button>
        {error && <span style={{ fontSize: 13, color: 'var(--red)' }}>⚠ {error}</span>}
      </div>

      {loading ? (
        <div style={{ color: 'var(--text-3)', padding: '32px 0' }}>Loading…</div>
      ) : jobs.length === 0 ? (
        <div style={{ color: 'var(--text-3)', padding: '32px 0' }}>No local queue jobs match the current filters.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {jobs.map(job => (
            <QueueJobCard
              key={job.id}
              job={job}
              open={!!openJobs[job.id]}
              onToggle={() => setOpenJobs(current => ({ ...current, [job.id]: !current[job.id] }))}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function QueueJobCard({ job, open, onToggle }) {
  const req = job.request || {}
  const result = job.result || {}
  const statusColor = {
    queued: 'var(--text-3)',
    running: 'var(--yellow)',
    done: 'var(--green)',
    error: 'var(--red)',
  }[job.status] || 'var(--text-2)'

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: '16px 18px' }}>
      <button
        onClick={onToggle}
        style={{
          width: '100%',
          background: 'transparent',
          border: 'none',
          padding: 0,
          textAlign: 'left',
          cursor: 'pointer',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 11, fontWeight: 600, color: statusColor, background: `${statusColor}20`, padding: '2px 8px', borderRadius: 999 }}>
            {String(job.status || '').toUpperCase()}
          </span>
          <span style={{ fontSize: 12, color: 'var(--text-2)' }}>{job.job_type}</span>
          {req.agentic && <span style={{ fontSize: 12, color: 'var(--accent)' }}>agentic</span>}
          <span style={{ fontSize: 13, color: 'var(--text)', fontWeight: 500, minWidth: 0, flex: 1 }}>
            {req.repo || '—'}{req.pr_number != null ? ` #${req.pr_number}` : ''}{req.title ? ` — ${req.title}` : ''}
          </span>
          <span style={{ fontSize: 12, color: 'var(--text-2)' }}>
            {job.claimed_by || 'Unclaimed'}
          </span>
          <span style={{ fontSize: 14, color: 'var(--text-3)' }}>{open ? '▾' : '▸'}</span>
        </div>
        <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 10, fontSize: 12, color: 'var(--text-3)' }}>
          <span>{fmtTime(job.created_at)}</span>
          <span>{req.model || 'default model'}</span>
          <span>{`${req.files_changed_count ?? 0} files`}</span>
          <span>{`${req.diff_lines ?? 0} lines`}</span>
          {result.summary && <span style={{ color: 'var(--text-2)' }}>{result.summary}</span>}
          {job.error && <span style={{ color: 'var(--red)' }}>Failed</span>}
        </div>
      </button>

      {open && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10, marginTop: 14, marginBottom: 12 }}>
            <Stat label="Repo" value={req.repo || '—'} />
            <Stat label="PR" value={req.pr_number != null ? `#${req.pr_number}` : '—'} />
            <Stat label="Claimed By" value={job.claimed_by || 'Unclaimed'} />
            <Stat label="Model" value={req.model || 'default'} />
            <Stat label="Files" value={String(req.files_changed_count ?? 0)} />
            <Stat label="Diff Lines" value={String(req.diff_lines ?? 0)} />
          </div>

          {req.agentic && (
            <div style={{ fontSize: 12, color: 'var(--text-2)', marginBottom: 10 }}>
              Sources: {(req.agent_sources || []).join(', ') || 'all enabled'}
            </div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
            <Stat label="Created" value={fmtTime(job.created_at)} />
            <Stat label="Started" value={fmtTime(job.started_at)} />
            <Stat label="Completed" value={fmtTime(job.completed_at)} />
            <Stat label="Result" value={result.summary || (job.error ? 'Failed' : '—')} />
          </div>

          {job.error && (
            <div style={{ marginTop: 12, fontSize: 12, color: 'var(--red)', whiteSpace: 'pre-wrap' }}>
              {job.error}
            </div>
          )}
        </>
      )}
    </div>
  )
}

function Stat({ label, value }) {
  return (
    <div style={{ background: 'var(--surface2)', borderRadius: 8, padding: '10px 12px' }}>
      <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 3, textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 13, color: 'var(--text)' }}>{value || '—'}</div>
    </div>
  )
}

function fmtTime(value) {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString()
  } catch {
    return value
  }
}

function buttonStyle(disabled) {
  return {
    background: disabled ? 'var(--surface2)' : 'var(--accent)',
    color: disabled ? 'var(--text-3)' : '#fff',
    border: 'none',
    borderRadius: 8,
    padding: '9px 16px',
    fontSize: 13,
    fontWeight: 500,
  }
}

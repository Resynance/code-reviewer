import { useState, useEffect } from 'react'
import { api } from '../lib/api.js'

export default function SettingsPage() {
  const [stats, setStats] = useState(null)
  const [settings, setSettings] = useState(null)

  // Model / provider form (prefilled from the server's effective values)
  const [modelInput, setModelInput] = useState('')
  const [providerInput, setProviderInput] = useState('')
  const [savingModel, setSavingModel] = useState(false)
  const [modelMsg, setModelMsg] = useState(null)

  // GitHub credential form (write-only — values are never read back from the server)
  const [token, setToken] = useState('')
  const [secret, setSecret] = useState('')
  const [savingCreds, setSavingCreds] = useState(false)
  const [credMsg, setCredMsg] = useState(null)

  // Repo manager
  const [newRepo, setNewRepo] = useState('')
  const [repoErr, setRepoErr] = useState(null)
  const [pages, setPages] = useState(5)
  const [backfillState, setBackfillState] = useState({}) // repo -> { status, text }
  const [openPrs, setOpenPrs] = useState({}) // repo -> { open, status, prs, total, error }

  function refreshStats() {
    api.stats().then(setStats).catch(() => {})
  }

  useEffect(() => {
    refreshStats()
    api.getSettings().then(s => {
      setSettings(s)
      setModelInput(s.openrouter_model || '')
      setProviderInput(s.openrouter_provider || '')
    }).catch(() => {})
  }, [])

  async function saveModel() {
    setSavingModel(true)
    setModelMsg(null)
    try {
      const s = await api.saveSettings({
        openrouter_model: modelInput.trim(),
        openrouter_provider: providerInput.trim(),
      })
      setSettings(s)
      setModelInput(s.openrouter_model || '')
      setProviderInput(s.openrouter_provider || '')
      setModelMsg('Saved ✓')
      refreshStats()
    } catch (e) {
      setModelMsg(e.message)
    } finally {
      setSavingModel(false)
    }
  }

  async function saveCreds() {
    const payload = {}
    if (token) payload.github_token = token
    if (secret) payload.webhook_secret = secret
    if (!Object.keys(payload).length) { setCredMsg('Enter a token or secret to save.'); return }
    setSavingCreds(true)
    setCredMsg(null)
    try {
      const s = await api.saveSettings(payload)
      setSettings(s)
      setToken('')
      setSecret('')
      setCredMsg('Saved ✓')
      refreshStats()
    } catch (e) {
      setCredMsg(e.message)
    } finally {
      setSavingCreds(false)
    }
  }

  async function addRepo() {
    const r = newRepo.trim()
    if (!r) return
    setRepoErr(null)
    try {
      const res = await api.addRepo(r)
      setSettings(s => ({ ...s, repos: res.repos }))
      setNewRepo('')
    } catch (e) {
      setRepoErr(e.message)
    }
  }

  async function removeRepo(r) {
    try {
      const res = await api.removeRepo(r)
      setSettings(s => ({ ...s, repos: res.repos }))
      setBackfillState(b => { const { [r]: _, ...rest } = b; return rest })
    } catch (e) {
      setRepoErr(e.message)
    }
  }

  async function runBackfill(r) {
    setBackfillState(b => ({ ...b, [r]: { status: 'running' } }))
    try {
      const res = await api.backfill(r, pages)
      setBackfillState(b => ({ ...b, [r]: { status: 'done', text: `Imported ${res.imported} PRs` } }))
      refreshStats()
    } catch (e) {
      setBackfillState(b => ({ ...b, [r]: { status: 'error', text: e.message } }))
    }
  }

  async function toggleOpenPrs(r) {
    if (openPrs[r]?.open) {
      setOpenPrs(s => ({ ...s, [r]: { ...s[r], open: false } }))
      return
    }
    setOpenPrs(s => ({ ...s, [r]: { open: true, status: 'loading' } }))
    try {
      const res = await api.openPrs(r)
      setOpenPrs(s => ({ ...s, [r]: { open: true, status: 'done', prs: res.new_prs || [], total: res.open_pr_count } }))
    } catch (e) {
      setOpenPrs(s => ({ ...s, [r]: { open: true, status: 'error', error: e.message } }))
    }
  }

  const repos = settings?.repos || []

  return (
    <div style={{ maxWidth: 680 }}>
      <h1 style={{ fontSize: 22, fontWeight: 600, marginBottom: 4 }}>Settings</h1>
      <p style={{ color: 'var(--text-2)', marginBottom: 32 }}>Configure GitHub access, manage repositories, and review system status.</p>

      {/* Status */}
      <Card title="System Status">
        {stats ? (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
            <StatRow label="OpenRouter API key" value={stats.api_key_configured ? 'Configured ✓' : 'Not set ✗'} ok={stats.api_key_configured} />
            <StatRow label="GitHub token" value={stats.github_token_configured ? 'Configured ✓' : 'Not set ✗'} ok={stats.github_token_configured} />
            <StatRow label="Model" value={stats.model || '—'} />
            <StatRow label="Provider" value={stats.provider || 'auto'} />
            <StatRow label="Vector backend" value={stats.backend} />
            <StatRow label="Repositories" value={`${repos.length}`} />
            <StatRow label="Decisions stored (sample)" value={`~${stats.decisions_sampled}`} />
          </div>
        ) : <div style={{ color: 'var(--text-3)' }}>Loading…</div>}
      </Card>

      {/* Model / provider */}
      <Card title="Model">
        <p style={{ color: 'var(--text-2)', fontSize: 13, marginBottom: 16 }}>
          The OpenRouter model used for reviews. Optionally pin a provider to route to.
          Leave the model blank to use the default (<code style={{ color: 'var(--accent)', fontFamily: 'var(--font-mono)' }}>anthropic/claude-sonnet-4.5</code>).
        </p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div>
            <label style={labelStyle}>Model slug — see openrouter.ai/models</label>
            <input value={modelInput} onChange={e => setModelInput(e.target.value)}
              placeholder="anthropic/claude-sonnet-4.5" style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>Provider (optional) — e.g. Anthropic, Google, Fireworks</label>
            <input value={providerInput} onChange={e => setProviderInput(e.target.value)}
              placeholder="auto-route (no preference)" style={inputStyle} />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <button onClick={saveModel} disabled={savingModel} style={primaryBtn(savingModel)}>
              {savingModel ? 'Saving…' : 'Save'}
            </button>
            {modelMsg && <span style={{ fontSize: 13, color: modelMsg === 'Saved ✓' ? 'var(--green)' : 'var(--red)' }}>{modelMsg}</span>}
          </div>
        </div>
      </Card>

      {/* GitHub credentials */}
      <Card title="GitHub Access">
        <p style={{ color: 'var(--text-2)', fontSize: 13, marginBottom: 16 }}>
          Stored securely on the server (config.json). Leave a field blank to keep its current value.
        </p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div>
            <label style={labelStyle}>
              GitHub token {settings && (settings.github_token_set
                ? <span style={{ color: 'var(--green)' }}>· set</span>
                : <span style={{ color: 'var(--text-3)' }}>· not set</span>)}
            </label>
            <input type="password" value={token} onChange={e => setToken(e.target.value)}
              placeholder={settings?.github_token_set ? '•••••••• (unchanged)' : 'ghp_…'} style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>
              Webhook secret {settings && (settings.webhook_secret_set
                ? <span style={{ color: 'var(--green)' }}>· set</span>
                : <span style={{ color: 'var(--text-3)' }}>· not set</span>)}
            </label>
            <input type="password" value={secret} onChange={e => setSecret(e.target.value)}
              placeholder={settings?.webhook_secret_set ? '•••••••• (unchanged)' : 'any random string'} style={inputStyle} />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <button onClick={saveCreds} disabled={savingCreds} style={primaryBtn(savingCreds)}>
              {savingCreds ? 'Saving…' : 'Save'}
            </button>
            {credMsg && <span style={{ fontSize: 13, color: credMsg === 'Saved ✓' ? 'var(--green)' : 'var(--red)' }}>{credMsg}</span>}
          </div>
        </div>
      </Card>

      {/* Repositories */}
      <Card title="Repositories">
        <p style={{ color: 'var(--text-2)', fontSize: 13, marginBottom: 16 }}>
          Repos appear in the review form and the decision filter. Backfill imports a repo's closed PRs;
          Open PRs lists open PRs not yet in the store (both require a GitHub token above).
        </p>

        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', marginBottom: 8 }}>
          <div style={{ flex: 1 }}>
            <label style={labelStyle}>Add repository (owner/repo)</label>
            <input value={newRepo} onChange={e => setNewRepo(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && addRepo()}
              placeholder="my-org/my-repo" style={inputStyle} />
          </div>
          <div style={{ width: 110 }}>
            <label style={labelStyle}>Backfill pages</label>
            <input type="number" min={1} max={50} value={pages}
              onChange={e => setPages(Number(e.target.value))} style={inputStyle} />
          </div>
          <button onClick={addRepo} style={{ ...primaryBtn(false), height: 38 }}>Add</button>
        </div>
        {repoErr && <div style={{ color: 'var(--red)', fontSize: 12, marginBottom: 8 }}>⚠ {repoErr}</div>}

        {repos.length === 0 ? (
          <div style={{ color: 'var(--text-3)', fontSize: 13, padding: '12px 0' }}>No repositories configured yet.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
            {repos.map(r => {
              const bf = backfillState[r] || {}
              const op = openPrs[r] || {}
              return (
                <div key={r} style={{ background: 'var(--surface2)', borderRadius: 8, padding: '10px 14px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <code style={{ flex: 1, fontSize: 13, color: 'var(--text)', fontFamily: 'var(--font-mono)' }}>{r}</code>
                    <button onClick={() => toggleOpenPrs(r)} style={smallBtn}>
                      {op.open ? '▾ Open PRs' : '▸ Open PRs'}
                    </button>
                    <button onClick={() => runBackfill(r)} disabled={bf.status === 'running'} style={smallBtn}>
                      {bf.status === 'running' ? '⟳ Backfilling…' : 'Backfill'}
                    </button>
                    <button onClick={() => removeRepo(r)} style={dangerBtn}>Remove</button>
                  </div>
                  {bf.text && (
                    <div style={{ fontSize: 12, marginTop: 6, color: bf.status === 'error' ? 'var(--red)' : 'var(--green)' }}>
                      {bf.status === 'error' ? '⚠ ' : '✓ '}{bf.text}
                    </div>
                  )}
                  {op.open && <OpenPrList op={op} />}
                </div>
              )
            })}
          </div>
        )}
      </Card>

      {/* Webhook */}
      <Card title="GitHub Webhook Setup">
        <p style={{ color: 'var(--text-2)', fontSize: 13, marginBottom: 12 }}>
          Add this webhook in your GitHub repo settings to trigger automatic reviews on new PRs.
          It verifies payloads against the webhook secret configured above.
        </p>
        {[
          ['Payload URL', 'https://your-server.com/webhook/github'],
          ['Content type', 'application/json'],
          ['Secret', 'The webhook secret set under GitHub Access'],
          ['Events', 'Pull requests, Pull request reviews'],
        ].map(([k, v]) => (
          <div key={k} style={{ display: 'flex', gap: 16, padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
            <div style={{ width: 130, fontSize: 12, color: 'var(--text-3)', flexShrink: 0 }}>{k}</div>
            <code style={{ fontSize: 12, color: 'var(--text)', fontFamily: 'var(--font-mono)' }}>{v}</code>
          </div>
        ))}
      </Card>
    </div>
  )
}

function OpenPrList({ op }) {
  const box = { marginTop: 8, paddingTop: 10, borderTop: '1px solid var(--border)' }
  if (op.status === 'loading') {
    return <div style={{ ...box, fontSize: 12, color: 'var(--text-3)' }}>Loading open PRs…</div>
  }
  if (op.status === 'error') {
    return <div style={{ ...box, fontSize: 12, color: 'var(--red)' }}>⚠ {op.error}</div>
  }
  if (!op.prs || op.prs.length === 0) {
    return (
      <div style={{ ...box, fontSize: 12, color: 'var(--text-3)' }}>
        No new open PRs{op.total ? ` — all ${op.total} open PRs are already in the store.` : '.'}
      </div>
    )
  }
  return (
    <div style={box}>
      <div style={{ fontSize: 12, color: 'var(--text-3)', marginBottom: 6 }}>
        {op.prs.length} not yet in the store · {op.total} open total
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {op.prs.map(pr => (
          <div key={pr.number} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
            <a href={pr.url} target="_blank" rel="noreferrer"
              style={{ color: 'var(--accent)', fontFamily: 'var(--font-mono)', textDecoration: 'none' }}>
              #{pr.number}
            </a>
            <span style={{ flex: 1, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {pr.title}
            </span>
            {pr.draft && (
              <span style={{ fontSize: 10, color: 'var(--text-3)', border: '1px solid var(--border2)', borderRadius: 4, padding: '1px 5px' }}>
                draft
              </span>
            )}
            <span style={{ color: 'var(--text-3)' }}>{pr.author}</span>
          </div>
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

function StatRow({ label, value, ok }) {
  const color = ok === true ? 'var(--green)' : ok === false ? 'var(--red)' : 'var(--text)'
  return (
    <div style={{ background: 'var(--surface2)', borderRadius: 7, padding: '10px 14px' }}>
      <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 13, fontWeight: 500, color }}>{value}</div>
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
  borderRadius: 7, padding: '6px 12px', fontSize: 12,
}
const dangerBtn = {
  background: 'transparent', border: '1px solid var(--border)', color: 'var(--red)',
  borderRadius: 7, padding: '6px 12px', fontSize: 12,
}

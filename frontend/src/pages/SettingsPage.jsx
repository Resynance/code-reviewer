import { useState, useEffect } from 'react'
import { api } from '../lib/api.js'

export default function SettingsPage() {
  const [stats, setStats] = useState(null)
  const [settings, setSettings] = useState(null)

  // Model list + embedding form
  const [modelsList, setModelsList] = useState([]) // [{label, model, provider}]
  const [embeddingInput, setEmbeddingInput] = useState('')
  const [hipaaPoliciesInput, setHipaaPoliciesInput] = useState('')
  const [savingModel, setSavingModel] = useState(false)
  const [modelMsg, setModelMsg] = useState(null)

  // GitHub token list
  const [tokenInput, setTokenInput] = useState('')
  const [addingToken, setAddingToken] = useState(false)
  const [tokenMsg, setTokenMsg] = useState(null)

  // Webhook secret (separate save)
  const [secret, setSecret] = useState('')
  const [savingSecret, setSavingSecret] = useState(false)
  const [secretMsg, setSecretMsg] = useState(null)

  // Repo manager
  const [newRepo, setNewRepo] = useState('')
  const [repoErr, setRepoErr] = useState(null)
  const [pages, setPages] = useState(5)
  const [backfillState, setBackfillState] = useState({}) // repo -> { status, text }
  const [openPrs, setOpenPrs] = useState({}) // repo -> { open, status, prs, total, error }

  // Browse-from-GitHub (discover repos under the token's orgs / your account)
  const [owners, setOwners] = useState([])
  const [browseOwner, setBrowseOwner] = useState('') // "login::type"
  const [ownerRepos, setOwnerRepos] = useState([])
  const [loadingOwnerRepos, setLoadingOwnerRepos] = useState(false)
  const [browseRepo, setBrowseRepo] = useState('')
  const [repoFilter, setRepoFilter] = useState('')
  const [browseErr, setBrowseErr] = useState(null)

  // Access allowlist
  const [accessEmails, setAccessEmails] = useState([])
  const [newEmail, setNewEmail] = useState('')
  const [accessErr, setAccessErr] = useState(null)

  function refreshStats() {
    api.stats().then(setStats).catch(() => {})
  }

  useEffect(() => {
    refreshStats()
    api.getSettings().then(s => {
      setSettings(s)
      setModelsList(s.openrouter_models || [])
      setEmbeddingInput(s.embedding_model || '')
      setHipaaPoliciesInput(JSON.stringify(s.hipaa_policies || { default: {}, repos: {} }, null, 2))
    }).catch(() => {})
    api.listAccess().then(r => setAccessEmails(r.emails || [])).catch(() => {})
    api.githubOwners().then(r => setOwners(r.owners || [])).catch(() => {})
  }, [])

  // Load an owner's repos when one is picked.
  useEffect(() => {
    if (!browseOwner) { setOwnerRepos([]); return }
    const [login, type] = browseOwner.split('::')
    let active = true
    setLoadingOwnerRepos(true); setBrowseErr(null); setBrowseRepo(''); setRepoFilter('')
    api.githubRepos(login, type)
      .then(r => { if (active) setOwnerRepos(r.repos || []) })
      .catch(e => { if (active) { setOwnerRepos([]); setBrowseErr(e.message) } })
      .finally(() => active && setLoadingOwnerRepos(false))
    return () => { active = false }
  }, [browseOwner])

  async function addBrowsedRepo() {
    if (!browseRepo) return
    setBrowseErr(null)
    try {
      const res = await api.addRepo(browseRepo)
      setSettings(s => ({ ...s, repos: res.repos }))
      setBrowseRepo('')
    } catch (e) {
      setBrowseErr(e.message)
    }
  }

  async function addAccess() {
    const e = newEmail.trim()
    if (!e) return
    setAccessErr(null)
    try {
      const res = await api.addAccess(e)
      setAccessEmails(res.emails || [])
      setNewEmail('')
    } catch (err) {
      setAccessErr(err.message)
    }
  }

  async function removeAccess(email) {
    setAccessErr(null)
    try {
      const res = await api.removeAccess(email)
      setAccessEmails(res.emails || [])
    } catch (err) {
      setAccessErr(err.message)
    }
  }

  function updateModelField(idx, field, value) {
    setModelsList(list => list.map((m, i) => i === idx ? { ...m, [field]: value } : m))
  }

  function addModelSlot() {
    setModelsList(list => [...list, { label: '', model: '', provider: '' }])
  }

  function removeModelSlot(idx) {
    setModelsList(list => list.filter((_, i) => i !== idx))
  }

  async function saveModel() {
    setSavingModel(true)
    setModelMsg(null)
    try {
      let parsedPolicies
      try {
        parsedPolicies = JSON.parse(hipaaPoliciesInput || '{}')
      } catch {
        setModelMsg('HIPAA policies must be valid JSON')
        setSavingModel(false)
        return
      }
      const s = await api.saveSettings({
        openrouter_models: modelsList,
        embedding_model: embeddingInput.trim(),
        hipaa_policies: parsedPolicies,
      })
      setSettings(s)
      setModelsList(s.openrouter_models || [])
      setEmbeddingInput(s.embedding_model || '')
      setHipaaPoliciesInput(JSON.stringify(s.hipaa_policies || { default: {}, repos: {} }, null, 2))
      setModelMsg('Saved ✓')
      refreshStats()
    } catch (e) {
      setModelMsg(e.message)
    } finally {
      setSavingModel(false)
    }
  }

  async function addToken() {
    if (!tokenInput.trim()) return
    setAddingToken(true)
    setTokenMsg(null)
    try {
      const res = await api.addGithubToken(tokenInput.trim())
      setSettings(s => ({ ...s, github_tokens: res.github_tokens, github_token_set: res.github_tokens.length > 0 }))
      setTokenInput('')
      setTokenMsg('Added ✓')
      refreshStats()
      api.githubOwners().then(r => setOwners(r.owners || [])).catch(() => {})
    } catch (e) {
      setTokenMsg(e.message)
    } finally {
      setAddingToken(false)
    }
  }

  async function removeToken(username) {
    setTokenMsg(null)
    try {
      const res = await api.removeGithubToken(username)
      setSettings(s => ({ ...s, github_tokens: res.github_tokens, github_token_set: res.github_tokens.length > 0 }))
      refreshStats()
      api.githubOwners().then(r => setOwners(r.owners || [])).catch(() => {})
    } catch (e) {
      setTokenMsg(e.message)
    }
  }

  async function saveSecret() {
    if (!secret) { setSecretMsg('Enter a secret to save.'); return }
    setSavingSecret(true)
    setSecretMsg(null)
    try {
      const s = await api.saveSettings({ webhook_secret: secret })
      setSettings(s)
      setSecret('')
      setSecretMsg('Saved ✓')
    } catch (e) {
      setSecretMsg(e.message)
    } finally {
      setSavingSecret(false)
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
            <StatRow label="GitHub tokens" value={
              settings?.github_tokens?.length
                ? `${settings.github_tokens.length} configured ✓`
                : stats.github_token_configured ? '1 configured ✓' : 'Not set ✗'
            } ok={stats.github_token_configured} />
            <StatRow label="Model" value={stats.model || '—'} />
            <StatRow label="Provider" value={stats.provider || 'auto'} />
            <StatRow label="Embedding model" value={stats.embedding_model || '—'} />
            <StatRow label="Vector backend" value={stats.backend} />
            <StatRow label="Repositories" value={`${repos.length}`} />
            <StatRow label="Decisions stored (sample)" value={`~${stats.decisions_sampled}`} />
          </div>
        ) : <div style={{ color: 'var(--text-3)' }}>Loading…</div>}
      </Card>

      {/* Model list */}
      <Card title="Models">
        <p style={{ color: 'var(--text-2)', fontSize: 13, marginBottom: 16 }}>
          Add one or more OpenRouter models. The first entry is the default.
          All configured models appear in the dropdown on the Review and Assess pages.
          See <code style={{ color: 'var(--accent)', fontFamily: 'var(--font-mono)' }}>openrouter.ai/models</code> for available model IDs.
        </p>

        {modelsList.length === 0 && (
          <div style={{ color: 'var(--text-3)', fontSize: 13, padding: '6px 0', marginBottom: 12 }}>
            No models configured — the default (<code style={{ fontFamily: 'var(--font-mono)' }}>anthropic/claude-sonnet-4.5</code>) will be used.
          </div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 12 }}>
          {modelsList.map((m, i) => (
            <div key={i} style={{ display: 'grid', gridTemplateColumns: '160px 1fr 160px auto', gap: 8, alignItems: 'center', background: 'var(--surface2)', borderRadius: 8, padding: '10px 12px' }}>
              <div>
                {i === 0 && <label style={labelStyle}>Label</label>}
                <input
                  value={m.label}
                  onChange={e => updateModelField(i, 'label', e.target.value)}
                  placeholder={`Model ${i + 1}`}
                  style={inputStyle}
                />
              </div>
              <div>
                {i === 0 && <label style={labelStyle}>Model ID</label>}
                <input
                  value={m.model}
                  onChange={e => updateModelField(i, 'model', e.target.value)}
                  placeholder="anthropic/claude-sonnet-4.5"
                  style={inputStyle}
                />
              </div>
              <div>
                {i === 0 && <label style={labelStyle}>Provider (optional)</label>}
                <input
                  value={m.provider}
                  onChange={e => updateModelField(i, 'provider', e.target.value)}
                  placeholder="auto-route"
                  style={inputStyle}
                />
              </div>
              <button onClick={() => removeModelSlot(i)} style={{ ...dangerBtn, alignSelf: i === 0 ? 'flex-end' : 'center', height: 36 }}>
                Remove
              </button>
            </div>
          ))}
        </div>

        <button onClick={addModelSlot} style={{ ...smallBtn, marginBottom: 16 }}>+ Add model</button>

        <div>
          <label style={labelStyle}>Embedding model — used to index/search decisions (pgvector backend)</label>
          <input value={embeddingInput} onChange={e => setEmbeddingInput(e.target.value)}
            placeholder="openai/text-embedding-3-small" style={inputStyle} />
        </div>

        <div style={{ marginTop: 16 }}>
          <label style={labelStyle}>HIPAA policy JSON — default policy plus per-repo overrides</label>
          <textarea
            value={hipaaPoliciesInput}
            onChange={e => setHipaaPoliciesInput(e.target.value)}
            rows={14}
            style={{ ...inputStyle, fontFamily: 'var(--font-mono)', fontSize: 12, resize: 'vertical' }}
          />
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 14 }}>
          <button onClick={saveModel} disabled={savingModel} style={primaryBtn(savingModel)}>
            {savingModel ? 'Saving…' : 'Save'}
          </button>
          {modelMsg && <span style={{ fontSize: 13, color: modelMsg === 'Saved ✓' ? 'var(--green)' : 'var(--red)' }}>{modelMsg}</span>}
        </div>
      </Card>

      {/* Access allowlist */}
      <Card title="Access">
        <p style={{ color: 'var(--text-2)', fontSize: 13, marginBottom: 16 }}>
          Emails allowed to sign in and use the app. Changes take effect within ~30s — no redeploy.
        </p>
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', marginBottom: 8 }}>
          <div style={{ flex: 1 }}>
            <label style={labelStyle}>Add user by email</label>
            <input value={newEmail} onChange={e => setNewEmail(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && addAccess()}
              placeholder="user@example.com" style={inputStyle} />
          </div>
          <button onClick={addAccess} style={{ ...primaryBtn(false), height: 38 }}>Add</button>
        </div>
        {accessErr && <div style={{ color: 'var(--red)', fontSize: 12, marginBottom: 8 }}>⚠ {accessErr}</div>}
        {accessEmails.length === 0 ? (
          <div style={{ color: 'var(--text-3)', fontSize: 13, padding: '8px 0' }}>
            No users yet — anyone matching the ALLOWED_EMAILS env bootstrap can still sign in.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
            {accessEmails.map(em => (
              <div key={em} style={{ display: 'flex', alignItems: 'center', gap: 10, background: 'var(--surface2)', borderRadius: 8, padding: '8px 14px' }}>
                <span style={{ flex: 1, fontSize: 13, color: 'var(--text)' }}>{em}</span>
                <button onClick={() => removeAccess(em)} style={dangerBtn}>Remove</button>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* GitHub tokens */}
      <Card title="GitHub Access">
        <p style={{ color: 'var(--text-2)', fontSize: 13, marginBottom: 16 }}>
          Add one token per GitHub account. Repos are automatically routed to the
          right token based on owner membership. Tokens are stored on the server only.
        </p>

        {(settings?.github_tokens || []).length === 0 ? (
          <div style={{ color: 'var(--text-3)', fontSize: 13, padding: '6px 0', marginBottom: 12 }}>
            No tokens configured.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 14 }}>
            {(settings.github_tokens || []).map(t => (
              <div key={t.username} style={{ display: 'flex', alignItems: 'center', gap: 10, background: 'var(--surface2)', borderRadius: 8, padding: '9px 14px' }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text)' }}>
                    {t.username || '(unknown)'}
                  </div>
                  {t.orgs?.length > 0 && (
                    <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 2 }}>
                      orgs: {t.orgs.join(', ')}
                    </div>
                  )}
                </div>
                <button onClick={() => removeToken(t.username)} style={dangerBtn}>Remove</button>
              </div>
            ))}
          </div>
        )}

        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', marginBottom: 6 }}>
          <div style={{ flex: 1 }}>
            <label style={labelStyle}>Add GitHub token</label>
            <input type="password" value={tokenInput} onChange={e => setTokenInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && addToken()}
              placeholder="ghp_… or github_pat_…" style={inputStyle} />
          </div>
          <button onClick={addToken} disabled={addingToken} style={{ ...primaryBtn(addingToken), height: 38 }}>
            {addingToken ? 'Verifying…' : 'Add'}
          </button>
        </div>
        {tokenMsg && (
          <div style={{ fontSize: 13, marginBottom: 6, color: tokenMsg === 'Added ✓' ? 'var(--green)' : 'var(--red)' }}>
            {tokenMsg}
          </div>
        )}
      </Card>

      {/* Webhook secret */}
      <Card title="GitHub Webhook Secret">
        <p style={{ color: 'var(--text-2)', fontSize: 13, marginBottom: 16 }}>
          Required to receive GitHub pull-request events. Leave blank to keep the current value.
        </p>
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', marginBottom: 6 }}>
          <div style={{ flex: 1 }}>
            <label style={labelStyle}>
              Webhook secret {settings && (settings.webhook_secret_set
                ? <span style={{ color: 'var(--green)' }}>· set</span>
                : <span style={{ color: 'var(--text-3)' }}>· not set</span>)}
            </label>
            <input type="password" value={secret} onChange={e => setSecret(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && saveSecret()}
              placeholder={settings?.webhook_secret_set ? '•••••••• (unchanged)' : 'any random string'} style={inputStyle} />
          </div>
          <button onClick={saveSecret} disabled={savingSecret} style={{ ...primaryBtn(savingSecret), height: 38 }}>
            {savingSecret ? 'Saving…' : 'Save'}
          </button>
        </div>
        {secretMsg && (
          <div style={{ fontSize: 13, color: secretMsg === 'Saved ✓' ? 'var(--green)' : 'var(--red)' }}>
            {secretMsg}
          </div>
        )}
      </Card>

      {/* Repositories */}
      <Card title="Repositories">
        <p style={{ color: 'var(--text-2)', fontSize: 13, marginBottom: 16 }}>
          Repos appear in the review form and the decision filter. Backfill imports a repo's closed PRs;
          Open PRs lists open PRs not yet in the store (both require a GitHub token above).
        </p>

        {owners.length > 0 && (
          <div style={{ marginBottom: 14 }}>
            <label style={labelStyle}>Browse from GitHub</label>
            <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end' }}>
              <select value={browseOwner} onChange={e => setBrowseOwner(e.target.value)} style={{ ...inputStyle, flex: '0 0 210px' }}>
                <option value="">Select owner…</option>
                {owners.map(o => <option key={o.login} value={`${o.login}::${o.type}`}>{o.login}{o.type === 'user' ? ' (you)' : ''}</option>)}
              </select>
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 6 }}>
                {browseOwner && (
                  <input
                    value={repoFilter}
                    onChange={e => { setRepoFilter(e.target.value); setBrowseRepo('') }}
                    placeholder="Filter repos…"
                    style={{ ...inputStyle, fontSize: 12 }}
                  />
                )}
                <select
                  value={browseRepo}
                  onChange={e => setBrowseRepo(e.target.value)}
                  disabled={!browseOwner || loadingOwnerRepos}
                  style={inputStyle}
                >
                  <option value="">{loadingOwnerRepos ? 'Loading repos…' : browseOwner ? 'Select a repo…' : '—'}</option>
                  {ownerRepos
                    .filter(r => !(settings?.repos || []).includes(r.full_name))
                    .filter(r => !repoFilter || r.full_name.toLowerCase().includes(repoFilter.toLowerCase()))
                    .map(r => (
                      <option key={r.full_name} value={r.full_name}>{r.full_name}{r.private ? ' 🔒' : ''}</option>
                    ))
                  }
                </select>
              </div>
              <button onClick={addBrowsedRepo} disabled={!browseRepo} style={{ ...primaryBtn(false), height: 38 }}>Add</button>
            </div>
            {browseErr && <div style={{ color: 'var(--red)', fontSize: 12, marginTop: 6 }}>⚠ {browseErr}</div>}
          </div>
        )}

        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', marginBottom: 8 }}>
          <div style={{ flex: 1 }}>
            <label style={labelStyle}>Add manually (owner/repo)</label>
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

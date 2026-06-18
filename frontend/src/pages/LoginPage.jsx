import { useState } from 'react'
import { signIn } from '../lib/auth.js'

export default function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    const { error } = await signIn(email, password)
    if (error) setError(error.message)
    setBusy(false)
    // On success, the onAuthStateChange listener in App re-renders into the app.
  }

  return (
    <div style={{ height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg)' }}>
      <form onSubmit={submit} style={{
        width: 340, background: 'var(--surface)', border: '1px solid var(--border)',
        borderRadius: 12, padding: '28px 28px 24px',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
          <span style={{ fontSize: 20 }}>⌘</span>
          <span style={{ fontSize: 17, fontWeight: 600 }}>ReviewBot</span>
        </div>
        <p style={{ color: 'var(--text-2)', fontSize: 13, marginBottom: 20 }}>Sign in to continue.</p>

        <label style={labelStyle}>Email</label>
        <input type="email" value={email} onChange={e => setEmail(e.target.value)}
          autoComplete="email" required style={inputStyle} />

        <label style={{ ...labelStyle, marginTop: 14 }}>Password</label>
        <input type="password" value={password} onChange={e => setPassword(e.target.value)}
          autoComplete="current-password" required style={inputStyle} />

        {error && <div style={{ color: 'var(--red)', fontSize: 12, marginTop: 12 }}>⚠ {error}</div>}

        <button type="submit" disabled={busy} style={{
          marginTop: 20, width: '100%', background: busy ? 'var(--surface2)' : 'var(--accent)',
          color: busy ? 'var(--text-3)' : '#fff', border: 'none', borderRadius: 8,
          padding: '10px 0', fontSize: 14, fontWeight: 500,
        }}>{busy ? 'Signing in…' : 'Sign in'}</button>
      </form>
    </div>
  )
}

const labelStyle = { display: 'block', fontSize: 11, color: 'var(--text-3)', marginBottom: 5, letterSpacing: '0.04em', textTransform: 'uppercase' }
const inputStyle = { display: 'block', width: '100%', background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px', color: 'var(--text)', fontSize: 14, outline: 'none' }

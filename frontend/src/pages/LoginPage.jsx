import { useState } from 'react'
import { signIn, signInWithGitHub } from '../lib/auth.js'

export default function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(null) // 'github' | 'password' | null
  const [error, setError] = useState(null)

  async function withGitHub() {
    setBusy('github')
    setError(null)
    const { error } = await signInWithGitHub()
    if (error) { setError(error.message); setBusy(null) }
    // On success the browser redirects to GitHub; App's onAuthStateChange takes
    // over after the round-trip.
  }

  async function withPassword(e) {
    e.preventDefault()
    setBusy('password')
    setError(null)
    const { error } = await signIn(email, password)
    if (error) setError(error.message)
    setBusy(null)
  }

  return (
    <div style={{ minHeight: '100dvh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg)', padding: 16 }}>
      <div style={{
        width: '100%', maxWidth: 340, background: 'var(--surface)', border: '1px solid var(--border)',
        borderRadius: 12, padding: '32px 28px',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, marginBottom: 8 }}>
          <span style={{ fontSize: 20 }}>⌘</span>
          <span style={{ fontSize: 17, fontWeight: 600 }}>ReviewBot</span>
        </div>
        <p style={{ color: 'var(--text-2)', fontSize: 13, marginBottom: 22, textAlign: 'center' }}>Sign in to continue.</p>

        <button onClick={withGitHub} disabled={busy !== null} style={{
          width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10,
          background: busy ? 'var(--surface2)' : 'var(--accent)', color: busy ? 'var(--text-3)' : '#fff',
          border: 'none', borderRadius: 8, padding: '11px 0', fontSize: 14, fontWeight: 500,
        }}>
          <GithubMark /> {busy === 'github' ? 'Redirecting…' : 'Sign in with GitHub'}
        </button>

        <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '18px 0' }}>
          <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
          <span style={{ fontSize: 11, color: 'var(--text-3)' }}>or</span>
          <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
        </div>

        <form onSubmit={withPassword}>
          <label style={labelStyle}>Email</label>
          <input type="email" value={email} onChange={e => setEmail(e.target.value)}
            autoComplete="email" required style={inputStyle} />
          <label style={{ ...labelStyle, marginTop: 12 }}>Password</label>
          <input type="password" value={password} onChange={e => setPassword(e.target.value)}
            autoComplete="current-password" required style={inputStyle} />
          <button type="submit" disabled={busy !== null} style={{
            marginTop: 16, width: '100%', background: 'transparent', color: 'var(--text)',
            border: '1px solid var(--border2)', borderRadius: 8, padding: '10px 0', fontSize: 13, fontWeight: 500,
          }}>{busy === 'password' ? 'Signing in…' : 'Sign in with email'}</button>
        </form>

        {error && <div style={{ color: 'var(--red)', fontSize: 12, marginTop: 14 }}>⚠ {error}</div>}
      </div>
    </div>
  )
}

function GithubMark() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  )
}

const labelStyle = { display: 'block', fontSize: 11, color: 'var(--text-3)', marginBottom: 5, letterSpacing: '0.04em', textTransform: 'uppercase' }
const inputStyle = { display: 'block', width: '100%', background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px', color: 'var(--text)', fontSize: 14, outline: 'none' }

import { useState } from 'react'
import { signInWithGitHub } from '../lib/auth.js'

export default function LoginPage() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function go() {
    setBusy(true)
    setError(null)
    const { error } = await signInWithGitHub()
    if (error) {
      setError(error.message)
      setBusy(false)
    }
    // On success the browser redirects to GitHub; the rest of this function
    // never runs. After the round-trip, App's onAuthStateChange picks up the
    // session and renders the app.
  }

  return (
    <div style={{ height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg)' }}>
      <div style={{
        width: 340, background: 'var(--surface)', border: '1px solid var(--border)',
        borderRadius: 12, padding: '32px 28px', textAlign: 'center',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, marginBottom: 8 }}>
          <span style={{ fontSize: 20 }}>⌘</span>
          <span style={{ fontSize: 17, fontWeight: 600 }}>ReviewBot</span>
        </div>
        <p style={{ color: 'var(--text-2)', fontSize: 13, marginBottom: 24 }}>Sign in to continue.</p>

        <button onClick={go} disabled={busy} style={{
          width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10,
          background: busy ? 'var(--surface2)' : 'var(--accent)', color: busy ? 'var(--text-3)' : '#fff',
          border: 'none', borderRadius: 8, padding: '11px 0', fontSize: 14, fontWeight: 500,
        }}>
          <GithubMark /> {busy ? 'Redirecting…' : 'Sign in with GitHub'}
        </button>

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

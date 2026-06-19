import { useState, useEffect } from 'react'
import { api } from '../lib/api.js'
import { authEnabled, signOut } from '../lib/auth.js'

const NAV = [
  { key: 'review', label: 'Review', icon: '▶' },
  { key: 'assess', label: 'Assess', icon: '◎' },
  { key: 'history', label: 'History', icon: '◷' },
  { key: 'decisions', label: 'Decisions', icon: '◈' },
  { key: 'settings', label: 'Settings', icon: '⚙' },
]

export default function Sidebar({ current, onNav }) {
  return (
    <aside
      style={{
        width: 220,
        flexShrink: 0,
        background: 'var(--surface)',
        borderRight: '1px solid var(--border)',
        display: 'flex',
        flexDirection: 'column',
        padding: '20px 14px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '4px 10px 24px' }}>
        <span style={{ fontSize: 20 }}>⌘</span>
        <span style={{ fontSize: 16, fontWeight: 600, letterSpacing: '0.02em' }}>ReviewBot</span>
      </div>

      <nav style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {NAV.map((item) => {
          const active = item.key === current
          return (
            <button
              key={item.key}
              onClick={() => onNav(item.key)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                padding: '10px 12px',
                borderRadius: 8,
                border: 'none',
                cursor: 'pointer',
                fontSize: 13,
                fontWeight: 500,
                textAlign: 'left',
                color: active ? 'var(--text)' : 'var(--text-2)',
                background: active ? 'var(--accent-glow)' : 'transparent',
                transition: 'background 0.15s, color 0.15s',
              }}
            >
              <span style={{ width: 16, textAlign: 'center', color: active ? 'var(--accent)' : 'var(--text-3)' }}>
                {item.icon}
              </span>
              {item.label}
            </button>
          )
        })}
      </nav>

      <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: 8 }}>
        <BalanceBadge />
        {authEnabled && (
          <button onClick={() => signOut()} style={{
            margin: '0 10px', background: 'transparent', border: '1px solid var(--border)',
            color: 'var(--text-2)', borderRadius: 8, padding: '7px 10px', fontSize: 12,
            display: 'flex', alignItems: 'center', gap: 8,
          }}>
            <span style={{ color: 'var(--text-3)' }}>⎋</span> Sign out
          </button>
        )}
        <div style={{ padding: '0 10px', fontSize: 11, color: 'var(--text-3)' }}>
          ReviewBot
        </div>
      </div>
    </aside>
  )
}

function BalanceBadge() {
  const [state, setState] = useState({ status: 'loading' })

  useEffect(() => {
    let active = true
    api
      .balance()
      .then((data) => {
        if (!active) return
        if (!data || !data.configured) setState({ status: 'unconfigured' })
        else setState({ status: 'ok', balance: data.balance, currency: data.currency })
      })
      .catch(() => active && setState({ status: 'error' }))
    return () => {
      active = false
    }
  }, [])

  let label
  let color = 'var(--text-2)'
  if (state.status === 'loading') {
    label = 'Balance…'
  } else if (state.status === 'unconfigured') {
    label = 'No API key'
    color = 'var(--text-3)'
  } else if (state.status === 'error') {
    label = 'Balance unavailable'
    color = 'var(--text-3)'
  } else {
    // Green when comfortable, yellow when low, red when nearly empty.
    color = state.balance > 1 ? 'var(--green)' : state.balance > 0.1 ? 'var(--yellow)' : 'var(--red)'
    label = `$${state.balance.toFixed(2)} left`
  }

  return (
    <div
      title="OpenRouter credit balance"
      style={{
        margin: '0 10px',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '8px 10px',
        borderRadius: 8,
        background: 'var(--surface2)',
        border: '1px solid var(--border)',
        fontSize: 12,
        fontWeight: 500,
        color,
      }}
    >
      <span style={{ color: 'var(--text-3)' }}>◎</span>
      {label}
    </div>
  )
}

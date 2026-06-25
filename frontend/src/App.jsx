import { useState, useEffect } from 'react'
import Sidebar from './components/Sidebar.jsx'
import ReviewPage from './pages/ReviewPage.jsx'
import ReviewsPage from './pages/ReviewsPage.jsx'
import DecisionsPage from './pages/DecisionsPage.jsx'
import SettingsPage from './pages/SettingsPage.jsx'
import AssessmentPage from './pages/AssessmentPage.jsx'
import CompliancePage from './pages/CompliancePage.jsx'
import QueuePage from './pages/QueuePage.jsx'
import LoginPage from './pages/LoginPage.jsx'
import { authEnabled, supabase } from './lib/auth.js'
import { useMediaQuery } from './lib/useMediaQuery.js'

export default function App() {
  const [page, setPage] = useState('review')
  const [navOpen, setNavOpen] = useState(false)
  // When auth is off (local dev), treat as ready with no session required.
  const [session, setSession] = useState(null)
  const [authReady, setAuthReady] = useState(!authEnabled)
  const isMobile = useMediaQuery('(max-width: 860px)')

  useEffect(() => {
    if (!authEnabled) return
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session)
      setAuthReady(true)
    })
    const { data: sub } = supabase.auth.onAuthStateChange((_e, s) => setSession(s))
    return () => sub.subscription.unsubscribe()
  }, [])

  if (authEnabled && !authReady) {
    return <Centered>Loading…</Centered>
  }
  if (authEnabled && !session) {
    return <LoginPage />
  }

  const pages = { review: ReviewPage, assess: AssessmentPage, compliance: CompliancePage, queue: QueuePage, history: ReviewsPage, decisions: DecisionsPage, settings: SettingsPage }
  const labels = { review: 'Review', assess: 'Assess', compliance: 'Compliance', queue: 'Queue', history: 'History', decisions: 'Decisions', settings: 'Settings' }
  const Page = pages[page] || ReviewPage

  return (
    <div style={{ display: 'flex', height: '100dvh', overflow: 'hidden', background: 'var(--bg)' }}>
      <Sidebar
        current={page}
        onNav={(next) => { setPage(next); setNavOpen(false) }}
        mobile={isMobile}
        open={navOpen}
        onClose={() => setNavOpen(false)}
      />
      <main style={{ flex: 1, overflow: 'auto', padding: isMobile ? '20px 16px 24px' : '32px' }}>
        {isMobile && (
          <div style={{
            position: 'sticky',
            top: -20,
            zIndex: 5,
            margin: '-20px -16px 20px',
            padding: '14px 16px 12px',
            background: 'linear-gradient(180deg, rgba(13,16,23,0.96), rgba(13,16,23,0.88))',
            borderBottom: '1px solid var(--border)',
            backdropFilter: 'blur(14px)',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <button
                onClick={() => setNavOpen(true)}
                style={{
                  width: 40,
                  height: 40,
                  borderRadius: 10,
                  border: '1px solid var(--border)',
                  background: 'var(--surface)',
                  color: 'var(--text)',
                  fontSize: 18,
                }}
                aria-label="Open navigation"
              >
                ☰
              </button>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 11, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>ReviewBot</div>
                <div style={{ fontSize: 16, fontWeight: 600 }}>{labels[page] || 'Review'}</div>
              </div>
            </div>
          </div>
        )}
        <Page />
      </main>
    </div>
  )
}

function Centered({ children }) {
  return (
    <div style={{ height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-3)' }}>
      {children}
    </div>
  )
}

import { useState, useEffect } from 'react'
import Sidebar from './components/Sidebar.jsx'
import ReviewPage from './pages/ReviewPage.jsx'
import ReviewsPage from './pages/ReviewsPage.jsx'
import DecisionsPage from './pages/DecisionsPage.jsx'
import SettingsPage from './pages/SettingsPage.jsx'
import AssessmentPage from './pages/AssessmentPage.jsx'
import LoginPage from './pages/LoginPage.jsx'
import { authEnabled, supabase } from './lib/auth.js'

export default function App() {
  const [page, setPage] = useState('review')
  // When auth is off (local dev), treat as ready with no session required.
  const [session, setSession] = useState(null)
  const [authReady, setAuthReady] = useState(!authEnabled)

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

  const pages = { review: ReviewPage, assess: AssessmentPage, history: ReviewsPage, decisions: DecisionsPage, settings: SettingsPage }
  const Page = pages[page] || ReviewPage

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
      <Sidebar current={page} onNav={setPage} />
      <main style={{ flex: 1, overflow: 'auto', padding: '32px' }}>
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

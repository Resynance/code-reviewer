import { useState } from 'react'
import Sidebar from './components/Sidebar.jsx'
import ReviewPage from './pages/ReviewPage.jsx'
import DecisionsPage from './pages/DecisionsPage.jsx'
import SettingsPage from './pages/SettingsPage.jsx'

export default function App() {
  const [page, setPage] = useState('review')

  const pages = { review: ReviewPage, decisions: DecisionsPage, settings: SettingsPage }
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

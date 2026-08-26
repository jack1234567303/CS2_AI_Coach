import { Routes, Route, Link } from 'react-router-dom'
import HomePage from './pages/HomePage.jsx'
import MatchPage from './pages/MatchPage.jsx'

export default function App() {
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-800 bg-panel/60 backdrop-blur sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-4 py-3 flex items-center gap-3">
          <Link to="/" className="text-xl font-bold text-accent">CS2 AI Coach</Link>
          <span className="text-xs text-slate-500">Demo 事件理解 + 战术行为分析 (Phase 2)</span>
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-4 py-6">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/match/:matchId" element={<MatchPage />} />
        </Routes>
      </main>
    </div>
  )
}

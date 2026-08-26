import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api.js'
import {
  DeathPanel, PeekPanel, PositionPanel, ProblemsPanel,
  StatCards, TeamworkPanel, UtilityPanel,
} from '../components/panels.jsx'
import CoachReport from '../components/CoachReport.jsx'

const TABS = [
  ['overview', '概览'],
  ['deaths', '死亡原因'],
  ['peeks', 'Peek 分析'],
  ['utility', '道具'],
  ['teamwork', '团队配合'],
  ['position', '站位热力图'],
  ['coach', 'AI 教练'],
]

export default function MatchPage() {
  const { matchId } = useParams()
  const [detail, setDetail] = useState(null)
  const [analysis, setAnalysis] = useState(null)
  const [steamid, setSteamid] = useState('')
  const [tab, setTab] = useState('overview')
  const [error, setError] = useState('')

  useEffect(() => {
    setError('')
    api.matchDetail(matchId).then(d => {
      setDetail(d)
      const first = d.players?.[0]
      if (first) setSteamid(first.steamid)
    }).catch(e => setError(String(e.message)))
  }, [matchId])

  useEffect(() => {
    if (!steamid) return
    setAnalysis(null)
    setError('')
    api.analysis(matchId, steamid)
      .then(setAnalysis)
      .catch(e => setError(String(e.message)))
  }, [matchId, steamid])

  if (!detail) return error
    ? <p className="text-danger">{error}</p>
    : <p className="text-slate-500">加载中…</p>

  return (
    <div className="space-y-5">
      <div className="card flex flex-wrap items-center gap-x-6 gap-y-2">
        <div>
          <span className="text-xl font-bold">{detail.map}</span>
          <span className="ml-2 text-slate-400">{detail.rounds} 回合 · {detail.tick_rate} tick</span>
        </div>
        <div className="text-accent font-mono">
          {Object.entries(detail.score || {}).map(([t, s], i) => (
            <span key={t}>{i > 0 && ' : '}{t} {s}</span>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span className="text-sm text-slate-400">分析对象:</span>
          <select value={steamid} onChange={e => setSteamid(e.target.value)}
                  className="bg-surface border border-slate-700 rounded px-2 py-1 text-sm">
            {detail.players.map(p => (
              <option key={p.steamid} value={p.steamid}>
                {p.name}(Rating {p.rating})
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        {TABS.map(([key, label]) => (
          <button key={key}
                  className={tab === key ? 'btn-primary' : 'btn-ghost'}
                  onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </div>

      {error && <p className="text-danger text-sm mb-4">{error}</p>}
      {!analysis ? (
        <p className="text-slate-500">分析计算中…(首次约数秒)</p>
      ) : (
        <div>
          {tab === 'overview' && (
            <div className="space-y-4">
              <StatCards stats={analysis.target_stats} />
              <ProblemsPanel analysis={analysis} />
            </div>
          )}
          {tab === 'deaths' && <DeathPanel analysis={analysis} />}
          {tab === 'peeks' && <PeekPanel analysis={analysis} />}
          {tab === 'utility' && <UtilityPanel analysis={analysis} />}
          {tab === 'teamwork' && <TeamworkPanel analysis={analysis} />}
          {tab === 'position' && <PositionPanel analysis={analysis} />}
          {tab === 'coach' && <CoachReport matchId={matchId} steamid={steamid} />}
        </div>
      )}
    </div>
  )
}

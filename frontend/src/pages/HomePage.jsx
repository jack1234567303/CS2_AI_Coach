import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'

const STATUS_STYLE = {
  ready: 'bg-accent/20 text-accent',
  parsing: 'bg-warn/20 text-warn animate-pulse',
  error: 'bg-danger/20 text-danger',
}

export default function HomePage() {
  const [matches, setMatches] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const fileRef = useRef(null)

  const refresh = () => api.listMatches().then(setMatches).catch(e => setError(String(e.message)))

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 5000)   // 轮询解析状态
    return () => clearInterval(t)
  }, [])

  const onUpload = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setBusy(true); setError('')
    try {
      const res = await api.uploadDemo(file)
      setError(`已上传:${res.match_id},正在后台解析(带帧约 1-5 分钟)`)
      refresh()
    } catch (err) {
      setError(`上传失败:${err.message}`)
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const onSample = async () => {
    setBusy(true); setError('')
    try {
      const res = await api.createSample()
      refresh()
      window.location.hash = `#/match/${res.match_id}`
    } catch (err) {
      setError(String(err.message))
    } finally { setBusy(false) }
  }

  return (
    <div className="space-y-6">
      <section className="card">
        <h2 className="text-lg font-semibold mb-3">上传比赛 Demo</h2>
        <p className="text-sm text-slate-400 mb-4">
          支持完美平台导出的比赛 ZIP(内含 .dem)。系统将使用 awpy 解析,重建事件并运行行为分析。
          带 Tick 帧解析较慢(1-5 分钟),解析中可离开本页。
        </p>
        <div className="flex flex-wrap gap-3 items-center">
          <label className="btn-primary cursor-pointer">
            选择 ZIP / DEM 文件
            <input ref={fileRef} type="file" accept=".zip,.dem" className="hidden"
                   onChange={onUpload} disabled={busy} />
          </label>
          <button className="btn-ghost" onClick={onSample} disabled={busy}>
            生成示例比赛(无需 Demo)
          </button>
        </div>
        {error && <p className="mt-3 text-sm text-warn">{error}</p>}
      </section>

      <section>
        <h2 className="text-lg font-semibold mb-3">比赛列表</h2>
        {matches === null ? (
          <p className="text-slate-500">加载中…</p>
        ) : matches.length === 0 ? (
          <p className="text-slate-500">还没有比赛。上传 Demo 或生成示例比赛开始。</p>
        ) : (
          <div className="space-y-2">
            {matches.map(m => (
              <div key={m.match_id} className="card flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{m.map_name || '(解析中)'}</span>
                    <span className={`text-xs px-2 py-0.5 rounded ${STATUS_STYLE[m.status] || 'bg-slate-700'}`}>
                      {m.status}
                    </span>
                    {m.source === 'sample' && (
                      <span className="text-xs px-2 py-0.5 rounded bg-slate-700 text-slate-300">示例</span>
                    )}
                  </div>
                  <div className="text-xs text-slate-500 truncate">
                    {m.match_id} · {m.rounds} 回合 · {m.players?.length || 0} 名玩家
                    {m.error ? ` · ${m.error}` : ''}
                  </div>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  <span className="font-mono text-accent">{m.score}</span>
                  {m.status === 'ready' && (
                    <Link className="btn-primary text-sm" to={`/match/${m.match_id}`}>分析</Link>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

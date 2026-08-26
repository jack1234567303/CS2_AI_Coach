import Heatmap from './Heatmap.jsx'

export function StatCards({ stats }) {
  if (!stats) return null
  const items = [
    ['K/D', `${stats.kills}/${stats.deaths}`],
    ['ADR', stats.adr],
    ['KAST', `${(stats.kast * 100).toFixed(0)}%`],
    ['HS%', `${stats.hsp}%`],
    ['Rating', stats.rating],
    ['突破成功率', stats.entry_rate != null ? `${stats.entry_rate}%` : '—'],
    ['补枪击杀', stats.trade_kills],
    ['死亡被补率', stats.traded_deaths > 0
      ? `${((stats.traded_deaths / Math.max(1, stats.deaths)) * 100).toFixed(0)}%` : '—'],
  ]
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
      {items.map(([label, value]) => (
        <div key={label} className="card text-center py-3">
          <div className="text-2xl font-bold text-white">{value}</div>
          <div className="text-xs text-slate-400 mt-1">{label}</div>
        </div>
      ))}
    </div>
  )
}

const SEV_STYLE = {
  high: 'bg-danger/20 text-danger',
  medium: 'bg-warn/20 text-warn',
  low: 'bg-slate-600/40 text-slate-300',
}

export function ProblemsPanel({ analysis }) {
  const { problems, strengths, data_notes } = analysis
  return (
    <div className="space-y-3">
      {data_notes?.length > 0 && (
        <div className="card border-warn/40">
          <h3 className="font-semibold text-warn mb-1">数据质量提示</h3>
          <ul className="list-disc pl-5 space-y-1 text-sm text-slate-300">
            {data_notes.map((n, i) => <li key={i}>{n}</li>)}
          </ul>
        </div>
      )}
      {strengths?.length > 0 && (
        <div className="card">
          <h3 className="font-semibold text-accent mb-2">检测到的优势</h3>
          <ul className="list-disc pl-5 space-y-1 text-sm text-slate-300">
            {strengths.map((s, i) => <li key={i}>{s.title}</li>)}
          </ul>
        </div>
      )}
      {problems.map((p, i) => (
        <div key={i} className="card">
          <div className="flex items-center gap-2 mb-1">
            <span className={`text-xs px-2 py-0.5 rounded ${SEV_STYLE[p.severity]}`}>
              {p.severity.toUpperCase()}
            </span>
            <span className="font-medium text-white">{p.title}</span>
          </div>
          {p.detail && <p className="text-sm text-slate-400">{p.detail}</p>}
          {p.evidence?.length > 0 && (
            <details className="mt-2">
              <summary className="text-xs text-slate-500 cursor-pointer">
                数据依据({p.evidence.length} 条)
              </summary>
              <ul className="mt-1 space-y-1 text-xs text-slate-400 list-disc pl-4">
                {p.evidence.map((e, j) => (
                  <li key={j}>
                    <span className="text-slate-500">R{e.round} · tick {e.tick}</span> {e.description}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      ))}
    </div>
  )
}

export function DeathPanel({ analysis }) {
  const d = analysis.deaths
  return (
    <div className="space-y-4">
      <div className="card">
        <h3 className="font-semibold mb-2">死亡原因分布(共 {d.total_deaths} 次)</h3>
        <div className="space-y-2">
          {d.summary.map(s => (
            <div key={s.cause}>
              <div className="flex justify-between text-sm mb-0.5">
                <span>{s.label}</span>
                <span className="text-slate-400">{s.count} 次({(s.share * 100).toFixed(0)}%)</span>
              </div>
              <div className="h-2 bg-slate-800 rounded">
                <div className="h-2 bg-danger/70 rounded"
                     style={{ width: `${s.share * 100}%` }} />
              </div>
              <div className="text-xs text-slate-500 mt-0.5">回合:{s.rounds.join(', ')}</div>
            </div>
          ))}
        </div>
      </div>
      <div className="card">
        <h3 className="font-semibold mb-2">逐次死亡明细</h3>
        <div className="space-y-1.5 max-h-96 overflow-y-auto pr-1">
          {d.deaths.map((r, i) => (
            <div key={i} className="text-sm border-l-2 border-slate-700 pl-3 py-1">
              <div className="text-slate-300">{r.description}</div>
              <div className="text-xs text-slate-500">
                分类:{r.cause_label}
                {r.avenged_by ? ` · 被 ${r.avenged_by} 补枪` : ''}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export function PeekPanel({ analysis }) {
  const p = analysis.peeks
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          ['交火总数', p.total_engagements],
          ['无保护 peek', p.unprotected_peeks],
          ['其中致死', p.unprotected_peek_deaths],
          ['协同闪光掩护', p.with_flash_support],
        ].map(([l, v]) => (
          <div key={l} className="card text-center py-3">
            <div className="text-2xl font-bold text-white">{v}</div>
            <div className="text-xs text-slate-400 mt-1">{l}</div>
          </div>
        ))}
      </div>
      {p.top_areas?.length > 0 && (
        <div className="card">
          <h3 className="font-semibold mb-2">无保护 peek 高发区域</h3>
          <table className="w-full text-sm">
            <thead className="text-slate-400 text-xs">
              <tr><th className="text-left">区域</th><th>次数</th><th>致死</th></tr>
            </thead>
            <tbody>
              {p.top_areas.map(a => (
                <tr key={a.area} className="border-t border-slate-800">
                  <td className="py-1">{a.area}</td>
                  <td className="text-center">{a.count}</td>
                  <td className="text-center text-danger">{a.deaths}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="card">
        <h3 className="font-semibold mb-2">交火明细(最近 15 次)</h3>
        <div className="space-y-1 max-h-96 overflow-y-auto text-sm">
          {[...p.engagements].reverse().slice(0, 15).map((e, i) => (
            <div key={i} className="flex items-center gap-2 text-xs border-l-2 pl-2 py-0.5"
                 style={{ borderColor: e.unprotected ? 'rgb(248,113,113)' : 'rgb(71,85,105)' }}>
              <span className="text-slate-500">R{e.round}</span>
              <span className="w-24 truncate">{e.area}</span>
              <span className="text-slate-400">vs {e.opponent_name}</span>
              {e.flash_support && <span className="text-accent">闪光✦</span>}
              {e.unprotected && <span className="text-danger">无保护</span>}
              <span className="ml-auto text-slate-500">
                {{ won: '获胜', died: '死亡', died_traded: '死亡·被补', neutral: '无交换' }[e.outcome]}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export function UtilityPanel({ analysis }) {
  const u = analysis.utility
  const rows = [
    ['闪光投掷', u.flash.thrown, `白杀/相关击杀 ${u.flash.blind_kills + u.flash.followup_kills} 次 · 被白死亡 ${u.flash.deaths_while_blind} 次`],
    ['烟雾投掷', u.smoke.thrown, `平均持续 ${u.smoke.avg_duration || '—'}s(理论 18s)· 提前失效 ${u.smoke.early_expired}`],
    ['燃烧瓶', u.fire.thrown, `总伤害 ${u.fire.damage}`],
    ['手雷', u.he.thrown, `总伤害 ${u.he.damage}`],
  ]
  return (
    <div className="space-y-3">
      {rows.map(([name, count, detail]) => (
        <div key={name} className="card">
          <div className="flex justify-between items-center">
            <span className="font-medium">{name}</span>
            <span className="text-2xl font-bold text-white">{count}</span>
          </div>
          <p className="text-sm text-slate-400 mt-1">{detail}</p>
        </div>
      ))}
      <div className="card text-sm text-slate-400">
        队友平均:闪光 {u.team_avg_flash_thrown} 颗 / 烟雾 {u.team_avg_smoke_thrown} 颗
      </div>
    </div>
  )
}

export function TeamworkPanel({ analysis }) {
  const t = analysis.teamwork
  return (
    <div className="space-y-4">
      <div className="card">
        <h3 className="font-semibold mb-2">补枪(Trade)概览</h3>
        <p className="text-sm text-slate-300">
          完成补枪 <b className="text-accent">{t.trade_kills.length}</b> 次 ·
          死亡被补率 <b className="text-white">{(t.traded_death_rate * 100).toFixed(0)}%</b> ·
          近距离漏补 <b className="text-danger">{t.missed_trades.length}</b> 次
        </p>
      </div>
      {t.missed_trades?.length > 0 && (
        <div className="card">
          <h3 className="font-semibold mb-2">疑似漏补(你在附近但 5 秒内无人补枪)</h3>
          <ul className="text-sm text-slate-300 space-y-1 list-disc pl-5">
            {t.missed_trades.map((m, i) => (
              <li key={i}>第{m.round}回合 {m.victim_name} 被 {m.killer_name} 击杀</li>
            ))}
          </ul>
        </div>
      )}
      {t.trade_kills?.length > 0 && (
        <div className="card">
          <h3 className="font-semibold mb-2">你的补枪击杀</h3>
          <ul className="text-sm text-slate-300 space-y-1 list-disc pl-5">
            {t.trade_kills.map((x, i) => (
              <li key={i}>第{x.round}回合 {x.victim_name} 倒地 {x.reaction_seconds}s 后补掉 {x.killer_name}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

export function PositionPanel({ analysis }) {
  const p = analysis.position
  return (
    <div className="space-y-4">
      {p.coverage_note && (
        <div className="card border-warn/40 text-sm text-warn">{p.coverage_note}</div>
      )}
      <div className="grid md:grid-cols-2 gap-4">
        <Heatmap mapName={p.map_name} points={p.points || []} />
      <div className="space-y-3">
        <div className="card">
          <h3 className="font-semibold mb-2">区域统计</h3>
          <table className="w-full text-sm">
            <thead className="text-slate-400 text-xs">
              <tr><th className="text-left">区域</th><th>死亡</th><th>击杀</th><th>前压死</th><th>主要方向</th></tr>
            </thead>
            <tbody>
              {p.area_stats.map(a => (
                <tr key={a.area} className="border-t border-slate-800">
                  <td className="py-1">{a.area}</td>
                  <td className="text-center text-danger">{a.deaths}</td>
                  <td className="text-center text-accent">{a.kills}</td>
                  <td className="text-center">{a.aggressive_deaths}</td>
                  <td className="text-center text-slate-400">{a.top_direction || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {p.ct_aggressive_death_rate != null && (
          <div className="card text-sm text-slate-300">
            CT 防守时 <b className="text-warn">{(p.ct_aggressive_death_rate * 100).toFixed(0)}%</b> 的死亡
            发生在回合开始 20 秒内(主动前压/抢点)。
          </div>
        )}
      </div>
      </div>
    </div>
  )
}

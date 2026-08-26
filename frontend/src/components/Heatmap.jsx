import { useEffect, useRef, useState } from 'react'

// 雷达底图三级降级:后端(awpy 离线数据) -> awpy GitHub -> 网格背景
const FALLBACK_MAP_IMG =
  'https://raw.githubusercontent.com/pnxenopoulos/awpy/main/src/awpy/data/map_images'

const SIDE_FILTERS = [
  { key: 'all', label: '全部' },
  { key: '2', label: 'T 方' },
  { key: '3', label: 'CT 方' },
]

export default function Heatmap({ mapName, points }) {
  const canvasRef = useRef(null)
  const [imgLevel, setImgLevel] = useState(0)   // 0=尝试中 1=后端 2=网络 3=网格
  const [sideFilter, setSideFilter] = useState('all')
  const kinds = [
    { key: 'presence', label: '活动范围', color: '56,189,248' },
    { key: 'kill', label: '击杀位置', color: '74,222,128' },
    { key: 'death', label: '死亡位置', color: '248,113,113' },
  ]
  const [enabled, setEnabled] = useState({ presence: true, kill: true, death: true })

  const visiblePoints = sideFilter === 'all'
    ? points
    : points.filter(p => String(p.side) === sideFilter)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    const W = canvas.width, H = canvas.height
    ctx.clearRect(0, 0, W, H)

    // 背景:逐级尝试图片源,全部失败则画网格
    const sources = [
      `/api/maps/${mapName}.png`,
      `${FALLBACK_MAP_IMG}/${(mapName || '').replace('de_', '')}.png`,
    ]

    function draw(ctx, W, H, img) {
      if (img) {
        ctx.globalAlpha = 0.85
        ctx.drawImage(img, 0, 0, W, H)
        ctx.globalAlpha = 1
      } else {
        setImgLevel(3)
        ctx.strokeStyle = 'rgba(148,163,184,0.15)'
        ctx.lineWidth = 1
        for (let i = 1; i < 8; i++) {
          ctx.beginPath(); ctx.moveTo(i * W / 8, 0); ctx.lineTo(i * W / 8, H); ctx.stroke()
          ctx.beginPath(); ctx.moveTo(0, i * H / 8); ctx.lineTo(W, i * H / 8); ctx.stroke()
        }
      }
      for (const { key, color } of kinds) {
        if (!enabled[key]) continue
        const pts = visiblePoints.filter(p => p.kind === key)
        for (const p of pts) {
          const x = p.x * W, y = p.y * H
          const r = key === 'presence' ? 10 : 16
          const g = ctx.createRadialGradient(x, y, 0, x, y, r)
          g.addColorStop(0, `rgba(${color},0.55)`)
          g.addColorStop(1, `rgba(${color},0)`)
          ctx.fillStyle = g
          ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill()
          if (key !== 'presence') {
            ctx.fillStyle = `rgba(${color},0.95)`
            ctx.beginPath(); ctx.arc(x, y, 2.2, 0, Math.PI * 2); ctx.fill()
          }
        }
      }
    }

    let cancelled = false
    const tryLoad = (idx) => {
      if (cancelled) return
      if (idx >= sources.length) {
        draw(ctx, W, H, null)
        return
      }
      const img = new Image()
      img.crossOrigin = 'anonymous'
      img.onload = () => {
        if (cancelled) return
        setImgLevel(idx + 1)
        draw(ctx, W, H, img)
      }
      img.onerror = () => { if (!cancelled) tryLoad(idx + 1) }
      img.src = sources[idx]
    }
    tryLoad(0)
    return () => { cancelled = true }
  }, [mapName, visiblePoints, enabled])

  return (
    <div>
      <canvas ref={canvasRef} width={768} height={768}
              className="w-full max-w-[600px] rounded-lg border border-slate-700 bg-slate-900" />
      <div className="flex flex-wrap gap-4 mt-2 text-sm items-center">
        <div className="flex gap-1 bg-slate-800/60 rounded-lg p-0.5">
          {SIDE_FILTERS.map(f => (
            <button key={f.key}
                    className={`px-3 py-1 rounded-md text-xs transition-colors ${
                      sideFilter === f.key ? 'bg-accent/30 text-accent' : 'text-slate-400 hover:text-slate-200'
                    }`}
                    onClick={() => setSideFilter(f.key)}>
              {f.label}
            </button>
          ))}
        </div>
        {kinds.map(k => (
          <label key={k.key} className="flex items-center gap-1.5 cursor-pointer">
            <input type="checkbox" checked={enabled[k.key]}
                   onChange={e => setEnabled(s => ({ ...s, [k.key]: e.target.checked }))} />
            <span style={{ color: `rgb(${k.color})` }}>{k.label}</span>
          </label>
        ))}
        {imgLevel === 3 && <span className="text-slate-500 text-xs">(雷达图不可用,显示网格背景;联网或安装 awpy 地图数据后可显示底图)</span>}
      </div>
    </div>
  )
}

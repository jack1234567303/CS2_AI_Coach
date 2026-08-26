const BASE = ''   // 开发模式走 vite proxy

async function jsonOrThrow(res) {
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try {
      const body = await res.json()
      msg = body.detail || JSON.stringify(body)
    } catch { /* ignore */ }
    throw new Error(msg)
  }
  return res.json()
}

export const api = {
  health: () => fetch(`${BASE}/api/health`).then(jsonOrThrow),

  listMatches: () => fetch(`${BASE}/api/matches`).then(jsonOrThrow),

  createSample: () =>
    fetch(`${BASE}/api/matches/sample`, { method: 'POST' }).then(jsonOrThrow),

  matchDetail: (id) => fetch(`${BASE}/api/matches/${id}`).then(jsonOrThrow),

  analysis: (id, steamid) =>
    fetch(`${BASE}/api/matches/${id}/analysis/${steamid}`).then(jsonOrThrow),

  coach: (id, steamid) =>
    fetch(`${BASE}/api/matches/${id}/coach/${steamid}`, { method: 'POST' }).then(jsonOrThrow),

  uploadDemo: async (file, parseFrames = true) => {
    const form = new FormData()
    form.append('file', file)
    const qs = parseFrames ? '' : '?parse_frames=false'
    return fetch(`${BASE}/api/matches/upload${qs}`, { method: 'POST', body: form })
      .then(jsonOrThrow)
  },
}

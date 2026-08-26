import { useState } from 'react'
import { marked } from 'marked'
import { api } from '../api.js'

export default function CoachReport({ matchId, steamid }) {
  const [report, setReport] = useState(null)
  const [llmUsed, setLlmUsed] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const generate = async () => {
    setBusy(true); setError('')
    try {
      const res = await api.coach(matchId, steamid)
      setReport(res.report)
      setLlmUsed(res.llm_used)
    } catch (e) {
      setError(String(e.message))
    } finally { setBusy(false) }
  }

  return (
    <div className="space-y-4">
      <div className="card flex items-center justify-between">
        <div>
          <h3 className="font-semibold">AI 教练报告</h3>
          <p className="text-sm text-slate-400">
            基于本页结构化分析结果生成;配置 LLM Key 后由大模型撰写,否则输出规则模板报告。
            每条结论都引用具体回合与事件。
          </p>
        </div>
        <button className="btn-primary" onClick={generate} disabled={busy}>
          {busy ? '生成中…' : '生成报告'}
        </button>
      </div>
      {error && <p className="text-danger text-sm">{error}</p>}
      {report && (
        <div className="card">
          <div className="text-xs text-slate-500 mb-2">
            {llmUsed ? '由 LLM 生成' : '由规则模板生成(未配置 LLM Key 或调用失败)'}
          </div>
          <div className="prose-report"
               dangerouslySetInnerHTML={{ __html: marked.parse(report) }} />
        </div>
      )}
    </div>
  )
}

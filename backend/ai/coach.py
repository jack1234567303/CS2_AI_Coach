"""AI Coach:把结构化分析结果转成 Markdown 训练报告。

原则(方案规定):
- 禁止把 Tick 数据发给 LLM;只发送 Feature + Analysis Result
- AI 不能凭空评价玩家;每条结论必须能追溯到 Round / Tick / Event
- 未配置 LLM Key 时退回确定性模板报告(同样引用回合/事件)

LLM 仅负责"解释",不参与任何 Demo 解析。
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

import httpx

from backend import config
from backend.analyzer.engine import FullAnalysis
from backend.common.models import MatchData

SYSTEM_PROMPT = """你是 CS2 职业教练。你会收到一份由规则系统从比赛 Demo 中提取的结构化分析结果(JSON)。
你的任务是把分析结果写成中文 Markdown 报告。严格遵守:
1. 只能基于输入数据评价,禁止编造任何数据或场次。
2. 每个问题/建议都必须引用具体依据(回合号、位置、事件),输入 evidence 中已给出。
3. 报告结构固定为:
# 玩家分析
## 优势
## 最大问题
## 数据依据
## 训练建议
4. 训练建议要具体可执行(练习方式、地图点位、道具用法),不超过 6 条。
5. 语气:专业、直接、建设性。"""


def build_coach_input(match: MatchData, analysis: FullAnalysis) -> dict:
    """构造发给 LLM 的精简输入(方案第六节格式)。"""
    ts = analysis.target_stats
    return {
        "player": analysis.target_name,
        "map": match.map_name,
        "score": match.final_score,
        "rounds": len(match.rounds),
        "basic_stats": None if ts is None else {
            "kills": ts.kills, "deaths": ts.deaths, "kd": ts.kd, "adr": ts.adr,
            "hsp": ts.hsp, "kast": ts.kast, "rating": ts.rating,
            "entry_attempts": ts.entry_attempts, "entry_rate": ts.entry_rate,
        },
        "engagement_summary": {
            "total": analysis.peeks.total_engagements,
            "unprotected_peeks": analysis.peeks.unprotected_peeks,
            "unprotected_peek_deaths": analysis.peeks.unprotected_peek_deaths,
            "with_flash_support": analysis.peeks.with_flash_support,
        },
        "death_causes": [{"cause": s.cause, "label": s.label, "count": s.count,
                          "share": s.share, "rounds": s.rounds[:10]}
                         for s in analysis.deaths.summary],
        "utility": {
            "flash_thrown": analysis.utility.flash.thrown,
            "flash_blind_kills": analysis.utility.flash.blind_kills,
            "smoke_thrown": analysis.utility.smoke.thrown,
            "smoke_avg_duration": analysis.utility.smoke.avg_duration,
        },
        "teamwork": {
            "trade_kills": len(analysis.teamwork.trade_kills),
            "missed_trades": len(analysis.teamwork.missed_trades),
            "traded_death_rate": analysis.teamwork.traded_death_rate,
        },
        "position": {
            "top_death_areas": [{"area": a.area, "deaths": a.deaths,
                                 "aggressive_deaths": a.aggressive_deaths}
                                for a in analysis.position.area_stats[:5]],
            "ct_aggressive_death_rate": analysis.position.ct_aggressive_death_rate,
        },
        "strengths": [{"title": s.title, "detail": s.detail} for s in analysis.strengths],
        "problems": [{
            "type": p.type, "severity": p.severity, "title": p.title, "detail": p.detail,
            "evidence": [f"第{e.round}回合 tick={e.tick}: {e.description}"
                         for e in p.evidence],
        } for p in analysis.problems],
    }


# ---------------- LLM 调用 ----------------

def _call_openai(payload: dict) -> Optional[str]:
    if not config.OPENAI_API_KEY:
        return None
    try:
        r = httpx.post(
            f"{config.OPENAI_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
            json={
                "model": config.OPENAI_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "分析数据如下,请生成报告:\n"
                     + json.dumps(payload, ensure_ascii=False, indent=1)},
                ],
                "temperature": 0.3,
            },
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception:
        return None


def _call_anthropic(payload: dict) -> Optional[str]:
    if not config.ANTHROPIC_API_KEY:
        return None
    try:
        r = httpx.post(
            f"{config.ANTHROPIC_BASE_URL.rstrip('/')}/v1/messages",
            headers={"x-api-key": config.ANTHROPIC_API_KEY,
                     "anthropic-version": "2023-06-01"},
            json={
                "model": config.ANTHROPIC_MODEL,
                "max_tokens": 3000,
                "system": SYSTEM_PROMPT,
                "messages": [{
                    "role": "user",
                    "content": "分析数据如下,请生成报告:\n"
                    + json.dumps(payload, ensure_ascii=False, indent=1),
                }],
            },
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"]
    except Exception:
        return None


def _llm_report(payload: dict) -> Optional[str]:
    provider = config.LLM_PROVIDER
    if provider == "none":
        return None
    if provider == "openai":
        return _call_openai(payload)
    if provider == "anthropic":
        return _call_anthropic(payload)
    # auto
    for fn in (_call_openai, _call_anthropic):
        out = fn(payload)
        if out:
            return out
    return None


# ---------------- 规则模板报告(无 LLM 时的确定性输出) ----------------

def _template_report(match: MatchData, a: FullAnalysis, payload: dict) -> str:
    ts = a.target_stats
    lines: List[str] = []
    lines.append(f"# 玩家分析:{a.target_name}")
    lines.append("")
    lines.append(f"地图 {match.map_name} | {len(match.rounds)} 回合 | 比分 "
                 + " : ".join(f"{k} {v}" for k, v in match.final_score.items()))
    if ts:
        lines.append(f"K/D **{ts.kills}/{ts.deaths}** | ADR **{ts.adr}** | KAST **{ts.kast * 100:.0f}%** "
                     f"| HS% **{ts.hsp}%** | Rating ≈ **{ts.rating}**")

    lines.append("")
    lines.append("## 优势")
    if a.strengths:
        for s in a.strengths[:4]:
            lines.append(f"- **{s.title}**")
            if s.detail:
                lines.append(f"  - {s.detail}")
            for e in s.evidence[:3]:
                lines.append(f"  - 依据:{e.description}")
    else:
        lines.append("- 本场数据中未检测到显著优势项,先解决下方问题可以最快提升战绩。")

    lines.append("")
    lines.append("## 最大问题")
    top = a.problems[:3] if a.problems else []
    if top:
        for i, p in enumerate(top, 1):
            lines.append(f"{i}. **[{p.severity.upper()}] {p.title}**")
            if p.detail:
                lines.append(f"   - {p.detail}")
    else:
        lines.append("1. 未检测到模式化的问题,本场发挥稳定。")

    lines.append("")
    lines.append("## 数据依据")
    lines.append("### 死亡原因分布")
    for s in a.deaths.summary:
        lines.append(f"- {s.label}:{s.count} 次({s.share * 100:.0f}%),回合 {', '.join(map(str, s.rounds[:8]))}")
    if a.deaths.deaths:
        lines.append("### 典型死亡回放(前 6 条)")
        for d in a.deaths.deaths[:6]:
            lines.append(f"- {d.description}")
    lines.append("### Peek 统计")
    lines.append(f"- 交火 {a.peeks.total_engagements} 次;无保护 peek {a.peeks.unprotected_peeks} 次,"
                 f"其中 {a.peeks.unprotected_peek_deaths} 次死亡;有协同闪光掩护的交火 {a.peeks.with_flash_support} 次")
    if a.peeks.top_areas:
        areas = ";".join(f"{x.area} {x.count}次/死{x.deaths}次" for x in a.peeks.top_areas[:3])
        lines.append(f"- 无保护 peek 主要发生地:{areas}")
    lines.append("### 道具")
    u = a.utility
    lines.append(f"- 闪光 {u.flash.thrown} 颗,白杀/闪光相关击杀 {u.flash.blind_kills + u.flash.followup_kills} 次")
    lines.append(f"- 烟雾 {u.smoke.thrown} 颗,平均持续 {u.smoke.avg_duration or 0}s(理论 18s)")

    lines.append("")
    lines.append("## 训练建议")
    sug: List[str] = []
    ptypes = {p.type for p in a.problems}
    if "bad_peek" in ptypes or "bad_peek_death" in ptypes:
        sug.append("**停止无保护 peek**:单排时默认\"有人补枪才露\",主动 peek 前先确认队友位置(小地图)与闪光;"
                   "练习贴墙短闪(pop flash)自己创造出枪窗口。")
    if "solo_death" in ptypes:
        sug.append("**减少单摸**:进攻端跟随大部队推进,需要拉扯时先与队友沟通;死亡回放重点看第 "
                   + "、".join(str(r) for r in _rounds_of(a, "isolated_death")[:3]) + " 回合。")
    if "missed_trade" in ptypes:
        sug.append("**补枪训练**:队友倒地后 5 秒是黄金窗口,预瞄队友死亡的枪线方向;"
                   "创意服练\"背闪+秒拉枪线\"组合,或与好友练 2v2 补枪图。")
    if "flash_utilization_low" in ptypes:
        sug.append("**闪光协同**:每次进攻前固定\"报点—丢闪—1.5 秒后拉枪\"流程;"
                   "社区图 prac / aim_botz 练习投掷点位,重点掌握本图常用进攻位的 pop flash。")
    if "ct_over_aggressive" in ptypes:
        sug.append("**CT 前压节制**:回合前 20 秒避免单人抢点;若要抢信息,配合烟雾/闪光并预设撤退路线。")
    if "blind_death" in ptypes:
        sug.append("**背闪习惯**:听到闪光弹声立刻转身;被白后第一时间撤到掩体后,而不是原地开枪。")
    if "death_hotspot" in ptypes and a.position.area_stats:
        sug.append(f"**热点区域复盘**:{a.position.area_stats[0].area} 是你的主要死亡点,"
                   "用观看 Demo 的方式复盘这些回合的道具与走位,寻找替代站位。")
    if not sug:
        sug.append("保持当前节奏,针对性复盘高位淘汰回合,巩固优势。")
    for i, s_ in enumerate(sug[:6], 1):
        lines.append(f"{i}. {s_}")
    lines.append("")
    lines.append("---")
    lines.append("*本报告由规则系统基于 Demo 事件生成;每条结论均可在上述回合/tick 中复核。*")
    return "\n".join(lines)


def _rounds_of(a: FullAnalysis, cause: str) -> List[int]:
    for s in a.deaths.summary:
        if s.cause == cause:
            return s.rounds
    return []


def generate_report(match: MatchData, analysis: FullAnalysis,
                    use_llm: bool = True) -> Dict[str, object]:
    payload = build_coach_input(match, analysis)
    llm_text: Optional[str] = None
    if use_llm:
        llm_text = _llm_report(payload)
    if llm_text:
        return {"report": llm_text, "llm_used": True, "provider_hint": config.LLM_PROVIDER}
    return {"report": _template_report(match, analysis, payload), "llm_used": False,
            "provider_hint": "template"}

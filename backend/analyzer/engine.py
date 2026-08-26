"""分析引擎:编排全部分析模块,产出完整分析结果(Phase 2 核心)。"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from backend.analyzer.common import MatchContext
from backend.analyzer.death_analysis import DeathAnalysisResult, analyze_deaths
from backend.analyzer.peek_analysis import PeekResult, analyze_peeks
from backend.analyzer.position_analysis import PositionResult, analyze_position
from backend.analyzer.stats_analysis import PlayerOverallStats, StatsResult, analyze_stats
from backend.analyzer.teamwork_analysis import TeamworkResult, analyze_teamwork
from backend.analyzer.utility_analysis import UtilityResult, analyze_utility
from backend.common.models import MatchData, Problem, Strength


class FullAnalysis(BaseModel):
    match_id: str
    map_name: str = ""
    target_steamid: str = ""
    target_name: str = ""
    rounds_total: int = 0
    stats: StatsResult = Field(default_factory=StatsResult)
    target_stats: Optional[PlayerOverallStats] = None
    teamwork: TeamworkResult = Field(default_factory=TeamworkResult)
    deaths: DeathAnalysisResult = Field(default_factory=DeathAnalysisResult)
    peeks: PeekResult = Field(default_factory=PeekResult)
    utility: UtilityResult = Field(default_factory=UtilityResult)
    position: PositionResult = Field(default_factory=PositionResult)
    problems: List[Problem] = Field(default_factory=list)   # 全模块问题汇总(按严重度)
    strengths: List[Strength] = Field(default_factory=list)
    data_notes: List[str] = Field(default_factory=list)     # 数据质量备注(非玩家问题)


SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def run_full_analysis(match: MatchData, target_steamid: str) -> FullAnalysis:
    ctx = MatchContext(match)
    target_name = ctx.name_of.get(target_steamid, target_steamid)

    stats = analyze_stats(match, ctx)
    target_stats = next((p for p in stats.players if p.steamid == target_steamid), None)
    teamwork = analyze_teamwork(match, ctx, target_steamid)
    deaths = analyze_deaths(match, ctx, target_steamid)
    peeks = analyze_peeks(match, ctx, target_steamid)
    utility = analyze_utility(match, ctx, target_steamid)
    position = analyze_position(match, ctx, target_steamid)

    problems: List[Problem] = []
    problems += deaths.problems + peeks.problems + utility.problems + position.problems
    if teamwork.problems:
        problems += teamwork.problems
    problems.sort(key=lambda p: SEVERITY_ORDER.get(p.severity, 3))

    data_notes: List[str] = []
    if position.coverage != "full" and position.coverage_note:
        data_notes.append(position.coverage_note)
    n_pos_unknown = next((s.count for s in deaths.summary
                          if s.cause == "position_unknown"), 0)
    if n_pos_unknown and not any("位置数据" in n for n in data_notes):
        data_notes.append(f"{n_pos_unknown} 次死亡因位置数据缺失无法归类(距离类分析对该玩家不可用)")

    strengths: List[Strength] = list(teamwork.strengths) + list(utility.strengths)
    if target_stats is not None:
        if target_stats.adr >= 80:
            strengths.append(Strength(
                title=f"输出稳定:ADR {target_stats.adr}",
                detail=f"每回合平均伤害 {target_stats.adr},高于常规 70 的及格线。"))
        if target_stats.entry_rate is not None and target_stats.entry_attempts >= 4 \
                and target_stats.entry_rate >= 60:
            strengths.append(Strength(
                title=f"突破成功率高:{target_stats.entry_rate:.0f}%",
                detail=f"{target_stats.entry_attempts} 次首杀尝试成功 {target_stats.entry_successes} 次。"))
        if target_stats.kast >= 0.7:
            strengths.append(Strength(
                title=f"KAST {target_stats.kast * 100:.0f}%",
                detail="回合参与度(击杀/助攻/存活/被补枪)高于 70%,稳定性好。"))

    return FullAnalysis(
        match_id=match.match_id,
        map_name=match.map_name,
        target_steamid=target_steamid,
        target_name=target_name,
        rounds_total=len(match.rounds),
        stats=stats,
        target_stats=target_stats,
        teamwork=teamwork,
        deaths=deaths,
        peeks=peeks,
        utility=utility,
        position=position,
        problems=problems,
        strengths=strengths,
        data_notes=data_notes,
    )

"""Module 2 死亡原因分析:回答"为什么死"。

规则系统,按优先级对每次死亡分类:
1. clutch           队友全灭后的残局死亡(1vX)
2. blind_death      被闪光致盲状态下的死亡
3. traded_death     死后 5 秒内被队友补枪(正常交换)
4. peek_death       主动接触敌人且无队友支援/无协同闪的死亡
5. late_trade       队友刚倒、距离远、晚到补枪位的死亡
6. isolated_death   孤立无支援的死亡(未主动接触)
7. protected_death  有队友在支援距离内的正常对枪死亡
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from backend.analyzer.common import (
    ENGAGEMENT_GAP_S, MatchContext, SUPPORT_DISTANCE, TRADE_WINDOW_S,
    classify_severity, dist,
)
from backend.common.models import Evidence, KillEvent, MatchData, Problem


class DeathRecord(BaseModel):
    round: int
    tick: int
    second: float = 0.0
    killer_name: str = ""
    weapon: str = ""
    headshot: bool = False
    area: str = ""
    side: Optional[int] = None
    teammates_alive: int = 0
    nearest_teammate_distance: Optional[float] = None
    was_blind: bool = False
    initiated_contact: bool = False    # 死前5秒内先手造成伤害
    avenged_by: Optional[str] = None
    cause: str = ""
    cause_label: str = ""
    description: str = ""


class CauseSummary(BaseModel):
    cause: str
    label: str
    count: int
    share: float = 0.0
    rounds: List[int] = Field(default_factory=list)
    top_areas: List[str] = Field(default_factory=list)


class DeathAnalysisResult(BaseModel):
    target_steamid: str
    total_deaths: int = 0
    deaths: List[DeathRecord] = Field(default_factory=list)
    summary: List[CauseSummary] = Field(default_factory=list)
    problems: List[Problem] = Field(default_factory=list)


CAUSE_LABELS = {
    "clutch": "残局孤军(队友全灭)",
    "blind_death": "被白状态死亡",
    "traded_death": "被补枪死亡(正常交换)",
    "peek_death": "无保护 peek 死亡",
    "late_trade": "补枪迟到死亡",
    "isolated_death": "孤立无支援死亡",
    "protected_death": "有支援对枪死亡(正常)",
    "position_unknown": "位置数据缺失(无法分类)",
}


def analyze_deaths(match: MatchData, ctx: MatchContext, target_steamid: str) -> DeathAnalysisResult:
    res = DeathAnalysisResult(target_steamid=target_steamid)
    mates = ctx.teammates_of(target_steamid) - {target_steamid}
    window = int(TRADE_WINDOW_S * ctx.tick_rate)
    gap = int(ENGAGEMENT_GAP_S * ctx.tick_rate)

    for k in ctx.deaths_of(target_steamid):
        rec = DeathRecord(
            round=k.round, tick=k.tick, second=k.second,
            killer_name=k.attacker_name, weapon=k.weapon, headshot=k.headshot,
            side=k.victim_side,
            area=k.victim_area or ctx.area_label(k.round, k.tick, k.victim_x, k.victim_y),
            was_blind=k.victim_blind,
        )
        teammates = ctx.alive_teammates(target_steamid, k.round, k.tick)
        rec.teammates_alive = len(teammates)
        near_d = ctx.nearest_teammate_distance(
            target_steamid, k.round, k.tick, pos=(k.victim_x, k.victim_y))
        rec.nearest_teammate_distance = round(near_d, 0) if near_d is not None else None

        # 死亡前 5 秒是否先手对敌造成伤害(主动接触)
        initiated = False
        ri = ctx.rounds.get(k.round)
        if ri:
            for d in ri.damages:
                if k.tick - window <= d.tick <= k.tick and d.attacker_steamid == target_steamid \
                        and d.victim_steamid not in mates and d.victim_steamid == k.attacker_steamid:
                    initiated = True
                    break
                if d.tick > k.tick:
                    break
        rec.initiated_contact = initiated

        # 是否被补枪
        avenger = None
        for k2 in (ri.kills if ri else []):
            if k.tick < k2.tick <= k.tick + window and k2.attacker_steamid in mates \
                    and k2.victim_steamid == k.attacker_steamid:
                avenger = k2.attacker_name
                break
        rec.avenged_by = avenger

        # 死前5秒内附近队友死亡(潜在补枪场景)
        mate_died_near = None
        for k2 in (ri.kills if ri else []):
            if k.tick - window <= k2.tick < k.tick and k2.victim_steamid in mates:
                dd = dist(k2.victim_x, k2.victim_y, k.victim_x, k.victim_y)
                if dd > 1000:
                    mate_died_near = k2
                    break

        # 分类(优先级从上到下)
        pos_missing = k.victim_x == 0.0 and k.victim_y == 0.0 and not k.victim_area
        if rec.teammates_alive == 0:
            cause = "clutch"
        elif rec.was_blind:
            cause = "blind_death"
        elif avenger:
            cause = "traded_death"
        elif pos_missing:
            # 该玩家此 Demo 无坐标(平台录制问题),距离类分类不可信
            cause = "position_unknown"
        elif initiated and (rec.nearest_teammate_distance is None
                            or rec.nearest_teammate_distance > SUPPORT_DISTANCE):
            cause = "peek_death"
        elif mate_died_near is not None:
            cause = "late_trade"
        elif rec.nearest_teammate_distance is None or rec.nearest_teammate_distance > SUPPORT_DISTANCE:
            cause = "isolated_death"
        else:
            cause = "protected_death"
        rec.cause = cause
        rec.cause_label = CAUSE_LABELS[cause]
        near_s = f"{rec.nearest_teammate_distance:.0f}" if rec.nearest_teammate_distance is not None else "?"
        rec.description = (
            f"第{k.round}回合 {ctx.seconds(k.tick - (ri.rd.start_tick if ri else 0)):.1f}秒 "
            f"于 {rec.area} 被 {rec.killer_name} 用 {rec.weapon}"
            + ("(爆头)" if rec.headshot else "")
            + f" 击杀;当时存活队友 {rec.teammates_alive} 人,最近队友距离 {near_s}"
            + (",死后被 " + avenger + " 补枪" if avenger else "")
        )
        res.deaths.append(rec)

    res.total_deaths = len(res.deaths)
    by_cause: dict = {}
    for r in res.deaths:
        s = by_cause.setdefault(r.cause, CauseSummary(
            cause=r.cause, label=r.cause_label, count=0, rounds=[], top_areas=[]))
        s.count += 1
        s.rounds.append(r.round)
        if r.area not in s.top_areas:
            s.top_areas.append(r.area)
    for s in by_cause.values():
        s.share = round(s.count / res.total_deaths, 3) if res.total_deaths else 0.0
        s.top_areas = s.top_areas[:5]
        s.rounds = s.rounds[:20]
    res.summary = sorted(by_cause.values(), key=lambda s: -s.count)

    # 生成问题
    problem_causes = {
        "peek_death": ("bad_peek_death", "无保护 peek 导致死亡"),
        "isolated_death": ("solo_death", "孤立无支援死亡(危险单摸)"),
        "blind_death": ("blind_death", "被白死亡"),
        "late_trade": ("late_trade", "补枪迟到"),
    }
    for cause, (ptype, title) in problem_causes.items():
        s = by_cause.get(cause)
        if s is None or s.count < 2:
            continue
        sev = classify_severity(s.count, res.total_deaths)
        detail_by = {
            "bad_peek_death": f"共 {s.count} 次死亡属于主动接触敌人但 5 秒内无队友在支援距离({SUPPORT_DISTANCE:.0f} 单位)内、也无协同闪光掩护。",
            "solo_death": f"共 {s.count} 次死亡时身边无队友(>{SUPPORT_DISTANCE:.0f} 单位),属于单摸被击杀。",
            "blind_death": f"共 {s.count} 次死亡发生在被闪光致盲状态,需要加强对闪光的规避(背闪)与队友闪光协同。",
            "late_trade": f"共 {s.count} 次死亡发生在队友倒地 5 秒内、但距离超过 1000 单位,赶到补枪位太晚。",
        }
        res.problems.append(Problem(
            type=ptype, severity=sev, title=f"{title}:{s.count} 次(占死亡 {s.share * 100:.0f}%)",
            detail=detail_by[ptype],
            evidence=[Evidence(round=r.round, tick=r.tick, description=r.description)
                      for r in res.deaths if r.cause == cause][:8],
        ))
    return res

"""Module 3 Peek 分析:第一次接触敌人时的保护情况。

以"交火(engagement)"为单位:目标玩家的伤害事件(造成或受到)按 3 秒间隔聚类。
对每次交火判定:
- 是否本人主动先手(第一笔伤害由本人造成)
- 队友存活数 / 最近队友距离
- 交火前 3 秒内是否有队友协同闪光落在附近
- 血量优势(交火前一帧的 HP 对比)
- 是否同回合同区域重复 peek
- 结果:赢 / 死 / 死但被补 / 无交换
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from pydantic import BaseModel, Field

from backend.analyzer.common import (
    ENGAGEMENT_GAP_S, FLASH_SUPPORT_RADIUS, FLASH_SUPPORT_WINDOW_S,
    MatchContext, SUPPORT_DISTANCE, classify_severity, dist,
)
from backend.common.models import DamageEvent, Evidence, MatchData, Problem


class Engagement(BaseModel):
    round: int
    start_tick: int
    area: str = ""
    opponent_name: str = ""
    initiated_by_target: bool = False
    teammates_alive: int = 0
    nearest_teammate_distance: Optional[float] = None
    flash_support: bool = False
    flash_support_by: Optional[str] = None
    hp_self: Optional[int] = None
    hp_opponent: Optional[int] = None
    hp_advantage: bool = False
    outcome: str = "neutral"     # won / died / died_traded / neutral
    unprotected: bool = False
    repeated: bool = False       # 同回合同区域二次以上接触


class PeekAreaStat(BaseModel):
    area: str
    count: int
    deaths: int


class PeekResult(BaseModel):
    target_steamid: str
    total_engagements: int = 0
    unprotected_peeks: int = 0
    unprotected_peek_deaths: int = 0
    repeated_peeks: int = 0
    with_flash_support: int = 0
    won_engagements: int = 0
    engagements: List[Engagement] = Field(default_factory=list)
    top_areas: List[PeekAreaStat] = Field(default_factory=list)
    problems: List[Problem] = Field(default_factory=list)


def _cluster_engagements(ctx: MatchContext, target: str) -> List[Tuple[int, int, List[DamageEvent], str]]:
    """返回 (round, start_tick, damages, opponent_steamid) 列表。"""
    events = [d for d in ctx.damages_involving(target)
              if d.attacker_steamid and d.victim_steamid != d.attacker_steamid]
    events.sort(key=lambda d: (d.round, d.tick))
    engagements: List[Tuple[int, int, List[DamageEvent], str]] = []
    cur: List[DamageEvent] = []
    gap = int(ENGAGEMENT_GAP_S * ctx.tick_rate)
    for d in events:
        if cur and (d.round != cur[-1].round or d.tick - cur[-1].tick > gap):
            engagements.append(_finish(cur, target))
            cur = []
        cur.append(d)
    if cur:
        engagements.append(_finish(cur, target))
    return engagements


def _finish(cur: List[DamageEvent], target: str) -> Tuple[int, int, List[DamageEvent], str]:
    opponent = None
    for d in cur:
        if d.attacker_steamid == target and d.victim_steamid != target:
            opponent = d.victim_steamid
            break
    if opponent is None:
        for d in cur:
            if d.attacker_steamid != target:
                opponent = d.attacker_steamid
                break
    return cur[0].round, cur[0].tick, cur, opponent or ""


def analyze_peeks(match: MatchData, ctx: MatchContext, target_steamid: str) -> PeekResult:
    res = PeekResult(target_steamid=target_steamid)
    mates = ctx.teammates_of(target_steamid) - {target_steamid}
    fw = int(FLASH_SUPPORT_WINDOW_S * ctx.tick_rate)

    seen_area_this_round: dict = {}
    for round_num, start_tick, damages, opponent in _cluster_engagements(ctx, target_steamid):
        first = damages[0]
        opp_name = ctx.name_of.get(opponent, first.victim_name if first.attacker_steamid == target_steamid else first.attacker_name)
        my_x = first.attacker_x if first.attacker_steamid == target_steamid else first.victim_x
        my_y = first.attacker_y if first.attacker_steamid == target_steamid else first.victim_y
        area = ctx.area_label(round_num, start_tick, my_x, my_y)

        eng = Engagement(
            round=round_num, start_tick=start_tick, area=area, opponent_name=opp_name,
            initiated_by_target=first.attacker_steamid == target_steamid,
        )
        last_tick = damages[-1].tick
        # 位置缺失的玩家(Demo 录制问题)不参与距离/闪光半径判定
        pos_missing = my_x == 0.0 and my_y == 0.0

        # 队友支援
        eng.teammates_alive = len(ctx.alive_teammates(target_steamid, round_num, start_tick))
        nd = ctx.nearest_teammate_distance(target_steamid, round_num, start_tick,
                                           pos=(my_x, my_y))
        eng.nearest_teammate_distance = round(nd, 0) if nd is not None else None

        # 协同闪光:交火前 fw tick 内队友闪光落在交火点附近
        flash_pos = (my_x, my_y)
        ri = ctx.rounds.get(round_num)
        if not pos_missing:
            ri = ctx.rounds.get(round_num)
            for g in (ri.grenades if ri else []):
                if g.grenade_type == "Flash" and g.player_steamid in mates:
                    det = g.detonation_tick or g.throw_tick
                    if start_tick - fw <= det <= start_tick + int(4 * ctx.tick_rate):
                        gx = g.detonation_x if g.detonation_x is not None else g.throw_x
                        gy = g.detonation_y if g.detonation_y is not None else g.throw_y
                        if dist(gx, gy, flash_pos[0], flash_pos[1]) <= FLASH_SUPPORT_RADIUS:
                            eng.flash_support = True
                            eng.flash_support_by = g.player_name
                            break

        # 血量对比(交火前最近帧)
        me = ctx.player_state_at(target_steamid, round_num, max(0, start_tick - 2))
        opp = ctx.player_state_at(opponent, round_num, max(0, start_tick - 2)) if opponent else None
        if me:
            eng.hp_self = me.hp
        if opp:
            eng.hp_opponent = opp.hp
        if eng.hp_self is not None and eng.hp_opponent is not None:
            eng.hp_advantage = eng.hp_self > eng.hp_opponent

        # 结果
        died = False
        won = False
        for k in (ri.kills if ri else []):
            if start_tick <= k.tick <= last_tick + int(2 * ctx.tick_rate):
                if k.victim_steamid == target_steamid:
                    died = True
                if k.attacker_steamid == target_steamid and k.victim_steamid == opponent:
                    won = True
        if died:
            avenged = False
            for k2 in (ri.kills if ri else []):
                if k2.tick > last_tick and k2.tick <= last_tick + int(5 * ctx.tick_rate) \
                        and k2.attacker_steamid in mates and k2.victim_steamid == opponent:
                    avenged = True
                    break
            eng.outcome = "died_traded" if avenged else "died"
        elif won:
            eng.outcome = "won"

        eng.unprotected = (not pos_missing
                           and eng.initiated_by_target
                           and not eng.flash_support
                           and (eng.nearest_teammate_distance is None
                                or eng.nearest_teammate_distance > SUPPORT_DISTANCE))
        key = (round_num, area)
        seen = seen_area_this_round.get(key, 0)
        eng.repeated = seen > 0
        seen_area_this_round[key] = seen + 1

        res.engagements.append(eng)

    res.total_engagements = len(res.engagements)
    res.unprotected_peeks = sum(1 for e in res.engagements if e.unprotected)
    res.unprotected_peek_deaths = sum(1 for e in res.engagements
                                      if e.unprotected and e.outcome in ("died", "died_traded"))
    res.repeated_peeks = sum(1 for e in res.engagements if e.repeated)
    res.with_flash_support = sum(1 for e in res.engagements if e.flash_support)
    res.won_engagements = sum(1 for e in res.engagements if e.outcome == "won")

    area_stats: dict = {}
    for e in res.engagements:
        if not e.unprotected:
            continue
        a = area_stats.setdefault(e.area, PeekAreaStat(area=e.area, count=0, deaths=0))
        a.count += 1
        if e.outcome in ("died", "died_traded"):
            a.deaths += 1
    res.top_areas = sorted(area_stats.values(), key=lambda a: -a.count)[:6]

    if res.unprotected_peeks >= 3:
        sev = classify_severity(res.unprotected_peeks, max(1, res.total_engagements))
        areas = "、".join(a.area for a in res.top_areas[:3])
        res.problems.append(Problem(
            type="bad_peek", severity=sev,
            title=f"本场出现 {res.unprotected_peeks} 次无保护 peek,其中 {res.unprotected_peek_deaths} 次导致死亡",
            detail=(f"共 {res.total_engagements} 次交火中,{res.unprotected_peeks} 次为你主动先手接触敌人,"
                    f"但 5 秒内既无队友在支援距离({SUPPORT_DISTANCE:.0f} 单位)内,也没有协同闪光落点掩护。"
                    + (f"主要集中在:{areas}。" if areas else "")),
            evidence=[Evidence(round=e.round, tick=e.start_tick,
                               description=f"第{e.round}回合 {e.area} 主动 peek 对 {e.opponent_name}"
                                           f"(结果:{ {'won':'击杀对手','died':'死亡','died_traded':'死亡但被补枪','neutral':'无交换'}[e.outcome] })")
                      for e in res.engagements if e.unprotected][:10],
        ))
    if res.repeated_peeks >= 5:
        res.problems.append(Problem(
            type="repeat_peek", severity="medium",
            title=f"{res.repeated_peeks} 次同回合同位置重复 peek",
            detail="同回合在相同位置再次接触敌人,对手已经架好枪,胜率显著降低。",
            evidence=[Evidence(round=e.round, tick=e.start_tick,
                               description=f"第{e.round}回合 {e.area} 的重复接触")
                      for e in res.engagements if e.repeated][:6],
        ))
    return res

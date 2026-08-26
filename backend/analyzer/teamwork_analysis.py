"""团队配合分析:Trade(补枪)与 Entry(突破首杀)。

回答的问题:
- 队友倒地后,目标玩家是否在 5 秒窗口内完成补枪?
- 目标玩家的死亡是否有人补?
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from backend.analyzer.common import (
    MatchContext, TRADE_WINDOW_S, classify_severity,
)
from backend.common.models import Evidence, KillEvent, MatchData, Problem, Strength


class TradeEvent(BaseModel):
    round: int
    tick: int
    victim_name: str
    killer_name: str
    avenger_name: str
    avenger_is_target: bool = False
    reaction_seconds: float = 0.0
    distance: Optional[float] = None   # 补枪者与死者死亡位置的距离


class DeathTradeStatus(BaseModel):
    round: int
    tick: int
    victim_name: str
    killer_name: str
    avenged_by: Optional[str] = None
    avenged_after_seconds: Optional[float] = None


class TeamworkResult(BaseModel):
    target_steamid: str
    trade_kills: List[TradeEvent] = Field(default_factory=list)      # 目标完成的补枪
    missed_trades: List[DeathTradeStatus] = Field(default_factory=list)  # 目标在场却未补的队友死亡
    teammate_deaths: List[DeathTradeStatus] = Field(default_factory=list)  # 队友全部死亡及补枪状态
    target_deaths: List[DeathTradeStatus] = Field(default_factory=list)    # 目标死亡及是否被补
    traded_death_rate: float = 0.0    # 目标死亡被补率
    problems: List[Problem] = Field(default_factory=list)
    strengths: List[Strength] = Field(default_factory=list)


def _first_kill_after(ctx: MatchContext, round_num: int, tick_from: int, tick_to: int,
                      killer_in: set, victim: Optional[str] = None) -> Optional[KillEvent]:
    for k in ctx.rounds.get(round_num).kills if round_num in ctx.rounds else []:
        if tick_from < k.tick <= tick_to and k.attacker_steamid in killer_in:
            if victim is None or k.victim_steamid == victim:
                return k
    return None


def analyze_teamwork(match: MatchData, ctx: MatchContext, target_steamid: str) -> TeamworkResult:
    res = TeamworkResult(target_steamid=target_steamid)
    mates = ctx.teammates_of(target_steamid) - {target_steamid}
    window = int(TRADE_WINDOW_S * ctx.tick_rate)

    def status_of(k: KillEvent) -> DeathTradeStatus:
        st = DeathTradeStatus(round=k.round, tick=k.tick,
                              victim_name=k.victim_name, killer_name=k.attacker_name)
        av = _first_kill_after(ctx, k.round, k.tick, k.tick + window, mates, k.attacker_steamid)
        if av is not None:
            st.avenged_by = av.attacker_name
            st.avenged_after_seconds = round((av.tick - k.tick) / ctx.tick_rate, 2)
        return st

    for k in match.kills:
        if k.attacker_steamid == target_steamid and k.victim_steamid not in mates \
                and k.victim_steamid and k.victim_steamid != target_steamid:
            # 目标击杀:检查是否为补枪(对象在窗口内刚杀了队友)
            for k2 in match.kills:
                if k2.round == k.round and k.tick - window <= k2.tick < k.tick \
                        and k2.attacker_steamid == k.victim_steamid and k2.victim_steamid in mates:
                    d = None
                    vstate = ctx.player_state_at(k2.victim_steamid, k.round, k2.tick)
                    if vstate:
                        d = round(((vstate.x - k.attacker_x) ** 2 + (vstate.y - k.attacker_y) ** 2) ** 0.5, 0)
                    res.trade_kills.append(TradeEvent(
                        round=k.round, tick=k.tick, victim_name=k2.victim_name,
                        killer_name=k2.attacker_name, avenger_name=k.attacker_name,
                        avenger_is_target=True,
                        reaction_seconds=round((k.tick - k2.tick) / ctx.tick_rate, 2),
                        distance=d,
                    ))
                    break
        elif k.victim_steamid in mates:
            st = status_of(k)
            res.teammate_deaths.append(st)
            # 目标当时存活且距离不远,却没人补 -> 记为疑似漏补
            if st.avenged_by is None:
                near = False
                me = ctx.player_state_at(target_steamid, k.round, k.tick)
                if me is not None and me.alive:
                    near = ((me.x - k.victim_x) ** 2 + (me.y - k.victim_y) ** 2) ** 0.5 <= 1200
                if near:
                    res.missed_trades.append(st)
        elif k.victim_steamid == target_steamid:
            res.target_deaths.append(status_of(k))

    total_deaths = len(res.target_deaths)
    avenged = sum(1 for d in res.target_deaths if d.avenged_by)
    res.traded_death_rate = round(avenged / total_deaths, 3) if total_deaths else 0.0

    if len(res.missed_trades) >= 3:
        sev = classify_severity(len(res.missed_trades), max(1, len(res.teammate_deaths)))
        res.problems.append(Problem(
            type="missed_trade",
            severity=sev,
            title=f"补枪意识不足:{len(res.missed_trades)} 次近距离队友倒地未完成补枪",
            detail=(f"队友在你附近(≤1200 单位)倒地后,{TRADE_WINDOW_S:.0f} 秒内全队无人完成补枪 "
                    f"共 {len(res.missed_trades)} 次。典型场景:队友先倒、你晚到或未预瞄击杀位。"),
            evidence=[Evidence(round=m.round, tick=m.tick,
                               description=f"第{m.round}回合 {m.victim_name} 被 {m.killer_name} 击杀,你当时在场且距离不远,但 {TRADE_WINDOW_S:.0f} 秒内无人补枪")
                      for m in res.missed_trades[:8]],
        ))
    if len(res.trade_kills) >= 3:
        res.strengths.append(Strength(
            title=f"补枪能力较好:完成 {len(res.trade_kills)} 次补枪击杀",
            detail=f"队友倒地 {TRADE_WINDOW_S:.0f} 秒内你完成补枪 {len(res.trade_kills)} 次。",
            evidence=[Evidence(round=t.round, tick=t.tick,
                               description=f"第{t.round}回合 {t.victim_name} 倒地 {t.reaction_seconds}s 后你补掉 {t.killer_name}")
                      for t in res.trade_kills[:6]],
        ))
    return res

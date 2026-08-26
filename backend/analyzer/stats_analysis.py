"""Module 1 基础能力分析:K/D、ADR、HS%、KAST、Rating、Entry、Trade。

Rating 采用公开的 HLTV Rating 2.0 线性近似,仅作横向参考。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from backend.analyzer.common import MatchContext, TRADE_WINDOW_S
from backend.common.models import KillEvent, MatchData


class PlayerOverallStats(BaseModel):
    steamid: str
    name: str
    team_name: str = ""
    rounds_played: int = 0
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    kd: float = 0.0
    kpr: float = 0.0
    dpr: float = 0.0
    adr: float = 0.0
    hsp: float = 0.0          # 爆头击杀占比(近似)
    kast: float = 0.0         # 0-1
    rating: float = 0.0       # HLTV 2.0 线性近似
    opening_kills: int = 0
    opening_deaths: int = 0
    entry_attempts: int = 0
    entry_successes: int = 0
    entry_rate: Optional[float] = None
    trade_kills: int = 0
    traded_deaths: int = 0


class TeamSummary(BaseModel):
    team_name: str
    score: int = 0
    players: List[str] = Field(default_factory=list)


class StatsResult(BaseModel):
    players: List[PlayerOverallStats] = Field(default_factory=list)
    teams: List[TeamSummary] = Field(default_factory=list)


def _is_traded_death(ctx: MatchContext, kill: KillEvent) -> bool:
    """死后 TRADE_WINDOW_S 秒内队友击杀凶手 -> 被补枪。"""
    window = int(TRADE_WINDOW_S * ctx.tick_rate)
    mates = ctx.teammates_of(kill.victim_steamid) - {kill.victim_steamid}
    for k2 in ctx.match.kills:
        if k2.round == kill.round and kill.tick < k2.tick <= kill.tick + window \
                and k2.attacker_steamid in mates and k2.victim_steamid == kill.attacker_steamid:
            return True
    return False


def _is_trade_kill(ctx: MatchContext, kill: KillEvent) -> bool:
    """击杀对象在 TRADE_WINDOW_S 秒内刚击杀过队友 -> 补枪击杀。"""
    window = int(TRADE_WINDOW_S * ctx.tick_rate)
    mates = ctx.teammates_of(kill.attacker_steamid) - {kill.attacker_steamid}
    for k2 in ctx.match.kills:
        if k2.round == kill.round and kill.tick - window <= k2.tick < kill.tick \
                and k2.attacker_steamid == kill.victim_steamid and k2.victim_steamid in mates:
            return True
    return False


def analyze_stats(match: MatchData, ctx: MatchContext) -> StatsResult:
    rounds_played = {p.steamid: 0 for p in match.players}
    for rd in match.rounds:
        for p in match.players:
            if ctx.side_in_round(p.steamid, rd.round) is not None:
                rounds_played[p.steamid] += 1
    # 无帧数据的玩家(平台录制问题)回合数严重低估:回退用队友的回合数
    n_rounds = len(match.rounds)
    for p in match.players:
        if rounds_played[p.steamid] < n_rounds * 0.5:
            mates = ctx.teammates_of(p.steamid) - {p.steamid}
            fallback = max((rounds_played[m] for m in mates if m in rounds_played),
                           default=0)
            rounds_played[p.steamid] = max(rounds_played[p.steamid],
                                           fallback or n_rounds)

    stats: Dict[str, PlayerOverallStats] = {
        p.steamid: PlayerOverallStats(
            steamid=p.steamid, name=p.name, team_name=p.team_name,
            rounds_played=rounds_played[p.steamid],
        ) for p in match.players
    }

    kills = {p.steamid: 0 for p in match.players}
    deaths = {p.steamid: 0 for p in match.players}
    assists = {p.steamid: 0 for p in match.players}
    hs_kills = {p.steamid: 0 for p in match.players}
    dmg_total = {p.steamid: 0 for p in match.players}
    for d in match.damages:
        if d.attacker_steamid in dmg_total and d.attacker_steamid != d.victim_steamid:
            dmg_total[d.attacker_steamid] += d.dmg_health
    for k in match.kills:
        if k.attacker_steamid in kills:
            kills[k.attacker_steamid] += 1
            if k.headshot:
                hs_kills[k.attacker_steamid] += 1
        if k.victim_steamid in deaths:
            deaths[k.victim_steamid] += 1
        if k.assister_steamid in assists and k.assister_steamid != k.attacker_steamid:
            assists[k.assister_steamid] += 1

    # KAST 按回合计算
    kast_rounds = {p.steamid: 0 for p in match.players}
    for rd in match.rounds:
        for p in match.players:
            if ctx.side_in_round(p.steamid, rd.round) is None:
                continue
            got = False
            for k in ctx.rounds.get(rd.round).kills if rd.round in ctx.rounds else []:
                if k.attacker_steamid == p.steamid or k.assister_steamid == p.steamid:
                    got = True
                    break
            if not got:
                died = any(k.victim_steamid == p.steamid for k in ctx.rounds[rd.round].kills)
                if not died:
                    got = True  # survived
                elif _is_traded_death(ctx, next(k for k in ctx.rounds[rd.round].kills
                                                if k.victim_steamid == p.steamid)):
                    got = True  # traded
            if got:
                kast_rounds[p.steamid] += 1

    # Opening / entry
    for rd in match.rounds:
        ok = ctx.opening_kill(rd.round)
        if ok is None:
            continue
        if ok.attacker_steamid in stats:
            stats[ok.attacker_steamid].opening_kills += 1
            stats[ok.attacker_steamid].entry_attempts += 1
            stats[ok.attacker_steamid].entry_successes += 1
        if ok.victim_steamid in stats:
            stats[ok.victim_steamid].opening_deaths += 1
            stats[ok.victim_steamid].entry_attempts += 1

    for k in match.kills:
        if k.attacker_steamid in stats and _is_trade_kill(ctx, k):
            stats[k.attacker_steamid].trade_kills += 1
        if k.victim_steamid in stats and _is_traded_death(ctx, k):
            stats[k.victim_steamid].traded_deaths += 1

    for sid, s in stats.items():
        rp = max(1, rounds_played[sid])
        s.kills, s.deaths, s.assists = kills[sid], deaths[sid], assists[sid]
        s.kd = round(kills[sid] / deaths[sid], 2) if deaths[sid] else float(kills[sid])
        s.kpr = round(kills[sid] / rp, 2)
        s.dpr = round(deaths[sid] / rp, 2)
        s.adr = round(dmg_total[sid] / rp, 1)
        s.hsp = round(hs_kills[sid] / kills[sid] * 100, 1) if kills[sid] else 0.0
        s.kast = round(kast_rounds[sid] / rp, 3)
        adpr = dmg_total[sid] / rp
        # HLTV Rating 2.0 线性近似(社区拟合;系数已按"平均玩家=1.0"校准,仅供参考)
        s.rating = round(max(0.0, 0.0073 * s.kast * 100 + 0.3591 * s.kpr
                             - 0.1319 * s.dpr + 0.0024 * adpr + 0.1588), 2)
        if s.entry_attempts:
            s.entry_rate = round(s.entry_successes / s.entry_attempts * 100, 1)

    team_score: Dict[str, int] = {}
    for name, sc in (match.final_score or {}).items():
        team_score[name] = int(sc)
    teams: Dict[str, TeamSummary] = {}
    for p in match.players:
        t = teams.setdefault(p.team_name or f"Team {p.name}", TeamSummary(team_name=p.team_name or f"Team {p.name}"))
        t.players.append(p.name)
        t.score = team_score.get(p.team_name, 0)

    return StatsResult(
        players=sorted(stats.values(), key=lambda s: s.rating, reverse=True),
        teams=list(teams.values()),
    )

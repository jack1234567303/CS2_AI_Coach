"""分析模块共享工具:MatchContext(带索引的比赛数据访问层)、几何与回合计时辅助。

所有分析模块通过 MatchContext 查询数据,保证:
- tick -> 最近帧 的查询统一走这里(帧已采样,存在 <=1 个采样间隔的误差)
- 队友判定基于"多数回合同侧"(CS2 中场换边,不能用单回合 side)
"""
from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from backend.common.models import (
    Frame, DamageEvent, GrenadeEvent, KillEvent, MatchData, PlayerFrame, RoundData,
    SIDE_CT, SIDE_T,
)

# ---- 可调分析参数(世界单位 ≈ inch;1m ≈ 39.37 units)----
TRADE_WINDOW_S = 5.0            # 补枪窗口(秒)
SUPPORT_DISTANCE = 800.0        # 队友在此距离内视为"可即时支援"(≈20m)
FLASH_SUPPORT_WINDOW_S = 3.0    # 交火前 N 秒内落下的队友闪光算协同闪
FLASH_SUPPORT_RADIUS = 1200.0   # 闪光落点距交火点该距离内算有效支援
ENGAGEMENT_GAP_S = 3.0          # 伤害间隔超过该秒数视为新的一次交火
AGGRESSIVE_TIME_S = 20.0        # 回合开始后 N 秒内的主动接触视为"前压"
AREA_DISTANCE = 900.0           # 位置归并半径

SEVERITY_HIGH_RATIO = 0.30      # 问题占比达到该比例 -> high
SEVERITY_HIGH_COUNT = 5         # 或绝对次数达到该值 -> high
SEVERITY_MEDIUM_COUNT = 3       # 达到该值 -> medium


def dist(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x1 - x2, y1 - y2)


def classify_severity(count: int, total: int) -> str:
    if count >= SEVERITY_HIGH_COUNT or (total > 0 and count / total >= SEVERITY_HIGH_RATIO):
        return "high"
    if count >= SEVERITY_MEDIUM_COUNT:
        return "medium"
    return "low"


@dataclass
class _RoundIndex:
    rd: RoundData
    kills: List[KillEvent] = field(default_factory=list)
    damages: List[DamageEvent] = field(default_factory=list)
    grenades: List[GrenadeEvent] = field(default_factory=list)
    frames: List[Frame] = field(default_factory=list)
    _frame_ticks: List[int] = field(default_factory=list)

    def frame_at(self, tick: int) -> Optional[Frame]:
        """tick 之前(含同 tick)最近的一帧。"""
        if not self._frame_ticks:
            return None
        i = bisect_right(self._frame_ticks, tick) - 1
        if i < 0:
            return self.frames[0]
        return self.frames[i]


class MatchContext:
    def __init__(self, match: MatchData):
        self.match = match
        self.tick_rate = match.tick_rate or 64
        self.name_of: Dict[str, str] = {p.steamid: p.name for p in match.players}
        self.rounds: Dict[int, _RoundIndex] = {}
        for rd in match.rounds:
            self.rounds[rd.round] = _RoundIndex(rd)
        for k in match.kills:
            if k.round in self.rounds:
                self.rounds[k.round].kills.append(k)
        for d in match.damages:
            if d.round in self.rounds:
                self.rounds[d.round].damages.append(d)
        for g in match.grenades:
            if g.round in self.rounds:
                self.rounds[g.round].grenades.append(g)
        for f in match.frames:
            if f.round in self.rounds:
                ri = self.rounds[f.round]
                ri.frames.append(f)
        for ri in self.rounds.values():
            ri.frames.sort(key=lambda f: f.tick)
            ri._frame_ticks = [f.tick for f in ri.frames]
        self._round_sides = self._build_round_sides()
        self._teammates = self._compute_teams()

    # ---- 队伍/队友 ----
    def _build_round_sides(self) -> Dict[int, Dict[str, int]]:
        """每回合玩家阵营:帧数据 + 事件;缺失者用"对手阵营取反"推断。"""
        round_sides: Dict[int, Dict[str, int]] = {}
        for f in self.match.frames:
            for p in f.players:
                if p.steamid:
                    round_sides.setdefault(f.round, {})[p.steamid] = p.side
        pairs = []
        for k in self.match.kills:
            pairs.append((k.round, k.attacker_steamid, k.attacker_side,
                          k.victim_steamid, k.victim_side))
        for d in self.match.damages:
            pairs.append((d.round, d.attacker_steamid, d.attacker_side,
                          d.victim_steamid, d.victim_side))
        unresolved = []
        for rnum, a_sid, a_side, v_sid, v_side in pairs:
            sides = round_sides.setdefault(rnum, {})
            if a_sid and a_side:
                sides[a_sid] = a_side
            if v_sid and v_side:
                sides[v_sid] = v_side
            if a_sid and v_sid and a_sid != v_sid:
                if a_side and not v_side:
                    unresolved.append((rnum, v_sid, 5 - a_side))
                elif v_side and not a_side:
                    unresolved.append((rnum, a_sid, 5 - v_side))
        for rnum, sid, side in unresolved:
            sides = round_sides.setdefault(rnum, {})
            if sides.get(sid) is None:
                sides[sid] = side
        return round_sides

    def _compute_teams(self) -> Dict[str, set]:
        """CS2 中场换边:按"每回合同阵营"聚类队友(阵营含推断)。"""
        parent: Dict[str, str] = {p.steamid: p.steamid for p in self.match.players}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for sides in self._round_sides.values():
            ids = list(sides.keys())
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    if sides[ids[i]] == sides[ids[j]]:
                        union(ids[i], ids[j])
        groups: Dict[str, set] = {}
        for p in self.match.players:
            groups.setdefault(find(p.steamid), set()).add(p.steamid)
        return {pid: grp for grp in groups.values() for pid in grp}

    def teammates_of(self, steamid: str) -> set:
        return self._teammates.get(steamid, {steamid})

    def side_in_round(self, steamid: str, round_num: int) -> Optional[int]:
        sides = self._round_sides.get(round_num)
        if sides and steamid in sides:
            return sides[steamid]
        ri = self.rounds.get(round_num)
        if ri:
            for f in ri.frames:
                for p in f.players:
                    if p.steamid == steamid:
                        return p.side
        return None

    def side_rounds(self, steamid: str, side: int) -> List[int]:
        out = []
        for rd in self.match.rounds:
            if self.side_in_round(steamid, rd.round) == side:
                out.append(rd.round)
        return out

    # ---- 帧查询 ----
    def frame_at(self, round_num: int, tick: int) -> Optional[Frame]:
        ri = self.rounds.get(round_num)
        return ri.frame_at(tick) if ri else None

    def player_state_at(self, steamid: str, round_num: int, tick: int) -> Optional[PlayerFrame]:
        f = self.frame_at(round_num, tick)
        if f is None:
            return None
        for p in f.players:
            if p.steamid == steamid:
                return p
        return None

    def alive_teammates(self, steamid: str, round_num: int, tick: int) -> List[PlayerFrame]:
        """返回 tick 时刻仍存活的队友(不含本人)。"""
        mates = self.teammates_of(steamid) - {steamid}
        f = self.frame_at(round_num, tick)
        if f is None:
            return []
        return [p for p in f.players if p.steamid in mates and p.alive]

    def nearest_teammate_distance(self, steamid: str, round_num: int, tick: int,
                                  pos: Optional[Tuple[float, float]] = None) -> Optional[float]:
        if pos is None:
            st = self.player_state_at(steamid, round_num, tick)
            if st is None:
                return None
            pos = (st.x, st.y)
        best = None
        for m in self.alive_teammates(steamid, round_num, tick):
            d = dist(pos[0], pos[1], m.x, m.y)
            if best is None or d < best:
                best = d
        return best

    # ---- 事件查询 ----
    def ticks(self, seconds: float) -> int:
        return int(seconds * self.tick_rate)

    def seconds(self, ticks: int) -> float:
        return ticks / self.tick_rate

    def round_of_tick(self, tick: int) -> Optional[int]:
        for rd in self.match.rounds:
            if rd.start_tick <= tick <= rd.end_tick:
                return rd.round
        return None

    def damages_involving(self, steamid: str) -> List[DamageEvent]:
        return [d for d in self.match.damages
                if d.attacker_steamid == steamid or d.victim_steamid == steamid]

    def kills_by(self, steamid: str) -> List[KillEvent]:
        return [k for k in self.match.kills if k.attacker_steamid == steamid]

    def deaths_of(self, steamid: str) -> List[KillEvent]:
        return [k for k in self.match.kills if k.victim_steamid == steamid]

    def opening_kill(self, round_num: int) -> Optional[KillEvent]:
        """回合首杀。"""
        ri = self.rounds.get(round_num)
        if ri and ri.kills:
            return min(ri.kills, key=lambda k: k.tick)
        return None

    def area_label(self, round_num: int, tick: int, x: float, y: float,
                   fallback: Optional[str] = None) -> str:
        """优先用击杀事件自带的 area,其次用最近帧的 last_place,最后网格坐标。"""
        st = None
        f = self.frame_at(round_num, tick)
        if f:
            best, bd = None, None
            for p in f.players:
                d = dist(p.x, p.y, x, y)
                if bd is None or d < bd:
                    best, bd = p, d
            if best is not None and bd is not None and bd <= AREA_DISTANCE:
                st = best
        if st is not None and st.last_place:
            return st.last_place
        if fallback:
            return fallback
        return f"({x:.0f},{y:.0f})"

    def round_seconds_elapsed(self, round_num: int, tick: int) -> Optional[float]:
        rd = next((r for r in self.match.rounds if r.round == round_num), None)
        if rd is None:
            return None
        base = rd.freeze_time_end_tick or rd.start_tick
        return max(0.0, (tick - base) / self.tick_rate)

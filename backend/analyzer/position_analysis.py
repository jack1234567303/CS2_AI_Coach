"""Module 5 站位分析:热力图 + 死亡热点 + 被击杀方向 + CT 前压倾向。

坐标归一化:优先使用 awpy 自带地图标定(pos_x/pos_y/scale),
不可用时退回数据驱动的 min-max 归一化。前端拿到的是 0-1 区间的点。
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from backend.analyzer.common import AGGRESSIVE_TIME_S, MatchContext, dist
from backend.common.models import Evidence, KillEvent, MatchData, Problem, SIDE_CT

DIR_BINS = ["东", "东北", "北", "西北", "西", "西南", "南", "东南"]


class HeatPoint(BaseModel):
    x: float          # 归一化 0-1
    y: float
    round: int
    tick: int
    kind: str         # death / kill / presence
    area: str = ""
    side: Optional[int] = None   # 2=T 3=CT(该回合玩家阵营,供前端分阵营查看)


class AreaDeathStat(BaseModel):
    area: str
    deaths: int
    kills: int
    aggressive_deaths: int = 0    # CT 回合开始20秒内的死亡(前压)
    top_direction: Optional[str] = None
    top_direction_count: int = 0


class PositionResult(BaseModel):
    target_steamid: str
    map_name: str = ""
    normalization: str = "minmax"      # awpy | minmax
    coverage: str = "full"             # full | recovered | none(位置数据完整性)
    coverage_note: str = ""
    points: List[HeatPoint] = Field(default_factory=list)
    area_stats: List[AreaDeathStat] = Field(default_factory=list)
    ct_aggressive_death_rate: Optional[float] = None
    problems: List[Problem] = Field(default_factory=list)


def _pos_missing(x: float, y: float, area: Optional[str]) -> bool:
    """Demo 里 pawn 坐标缺失时事件坐标为 (0,0) 且无区域名。"""
    return x == 0.0 and y == 0.0 and not area


def _map_calibration(map_name: str) -> Optional[Tuple[float, float, float]]:
    """地图标定:返回 (pos_x, pos_y, scale)。兼容 awpy 1.x 与 2.x 数据位置。"""
    md = None
    # awpy 2.x:~/.awpy/maps/map-data.json(首次使用需联网下载)
    try:
        from awpy.data import MAPS_DIR  # type: ignore
        f = MAPS_DIR / "map-data.json"
        if f.exists():
            import json as _json
            md = _json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        pass
    # awpy 1.x:包内常量
    if md is None:
        try:
            from awpy.data import MAP_DATA  # type: ignore
            md = dict(MAP_DATA)
        except Exception:
            return None
    key = map_name if map_name in md else map_name.removeprefix("de_")
    if key in md:
        m = md[key]
        return float(m["pos_x"]), float(m["pos_y"]), float(m["scale"])
    return None


def _direction_bin(dx: float, dy: float) -> str:
    ang = math.degrees(math.atan2(dy, dx))  # -180..180, 0=东
    idx = int(((ang + 360 + 22.5) % 360) // 45)
    return DIR_BINS[idx]


def analyze_position(match: MatchData, ctx: MatchContext, target_steamid: str) -> PositionResult:
    res = PositionResult(target_steamid=target_steamid, map_name=match.map_name)

    cal = _map_calibration(match.map_name)

    # 位置数据完整性:tick 帧中该玩家的出现数(部分平台 Demo 个别玩家全程无坐标)
    frame_appearances = sum(1 for f in match.frames
                            for p in f.players if p.steamid == target_steamid)
    recovered = [r for r in match.recovered_positions if r.steamid == target_steamid]
    if frame_appearances >= len(match.rounds):
        res.coverage = "full"
    elif recovered:
        res.coverage = "recovered"
        res.coverage_note = (f"该玩家在此 Demo 中缺少完整坐标(平台录制问题,"
                             f"已用 {len(recovered)} 个道具投掷点恢复站位,热力图仅供参考)")
    else:
        res.coverage = "none"
        res.coverage_note = "该玩家在此 Demo 中没有任何位置数据(平台录制问题),无法生成站位分析"

    # 有效击杀/死亡事件(剔除坐标缺失的 (0,0) 坏点)
    valid_kills = [k for k in ctx.kills_by(target_steamid)
                   if not _pos_missing(k.attacker_x, k.attacker_y, k.attacker_area)]
    valid_deaths = [k for k in ctx.deaths_of(target_steamid)
                    if not _pos_missing(k.victim_x, k.victim_y, k.victim_area)]

    xs, ys = [], []
    for k in valid_kills:
        xs += [k.attacker_x]
        ys += [k.attacker_y]
    for k in valid_deaths:
        xs += [k.victim_x]
        ys += [k.victim_y]
    for f in match.frames:
        for p in f.players:
            if p.steamid == target_steamid:
                xs.append(p.x)
                ys.append(p.y)
    xs += [r.x for r in recovered]
    ys += [r.y for r in recovered]
    if not xs:
        return res

    if cal is not None:
        px, py, scale = cal
        def norm(x: float, y: float) -> Tuple[float, float]:
            # awpy 标定:scale = 每像素世界单位,雷达图为 1024x1024 像素
            return ((x - px) / scale / 1024.0, (py - y) / scale / 1024.0)
        res.normalization = "awpy"
    else:
        margin = 0.05
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        spanx = max(1.0, maxx - minx)
        spany = max(1.0, maxy - miny)
        minx -= spanx * margin
        spanx *= 1 + 2 * margin
        miny -= spany * margin
        spany *= 1 + 2 * margin
        def norm(x: float, y: float) -> Tuple[float, float]:
            return ((x - minx) / spanx, (y - miny) / spany)
        res.normalization = "minmax"

    # 击杀点 / 死亡点(side = 玩家该回合阵营;坐标缺失的事件已剔除)
    for k in valid_kills:
        nx, ny = norm(k.attacker_x, k.attacker_y)
        res.points.append(HeatPoint(x=round(nx, 4), y=round(ny, 4), round=k.round, tick=k.tick,
                                    kind="kill", side=k.attacker_side))
    for k in valid_deaths:
        nx, ny = norm(k.victim_x, k.victim_y)
        res.points.append(HeatPoint(x=round(nx, 4), y=round(ny, 4), round=k.round, tick=k.tick,
                                    kind="death", side=k.victim_side))

    # 恢复位置(道具投掷原点)作为活动点
    for r in recovered:
        nx, ny = norm(r.x, r.y)
        res.points.append(HeatPoint(x=round(nx, 4), y=round(ny, 4), round=r.round, tick=r.tick,
                                    kind="presence", side=ctx.side_in_round(target_steamid, r.round)))

    # 活动轨迹(采样,每 2 秒最多 1 个点)
    last_tick = -10 ** 9
    for f in match.frames:
        if f.tick - last_tick < 2 * ctx.tick_rate:
            continue
        for p in f.players:
            if p.steamid == target_steamid and p.alive:
                nx, ny = norm(p.x, p.y)
                res.points.append(HeatPoint(x=round(nx, 4), y=round(ny, 4), round=f.round, tick=f.tick,
                                            kind="presence", area=p.last_place or "", side=p.side))
                last_tick = f.tick
                break

    # 区域统计 + 被击杀方向 + CT 前压
    area_death: Counter = Counter()
    area_kill: Counter = Counter()
    area_dir: Dict[str, Counter] = {}
    area_aggr: Counter = Counter()
    ct_deaths = 0
    ct_aggr_deaths = 0
    rd_map = {r.round: r for r in match.rounds}
    for k in valid_deaths:
        area = k.victim_area or ctx.area_label(k.round, k.tick, k.victim_x, k.victim_y)
        area_death[area] += 1
        area_dir.setdefault(area, Counter())[_direction_bin(k.attacker_x - k.victim_x,
                                                            k.attacker_y - k.victim_y)] += 1
        if k.victim_side == SIDE_CT:
            ct_deaths += 1
            rd = rd_map.get(k.round)
            if rd and rd.freeze_time_end_tick \
                    and (k.tick - rd.freeze_time_end_tick) < AGGRESSIVE_TIME_S * ctx.tick_rate:
                ct_aggr_deaths += 1
                area_aggr[area] += 1
    for k in valid_kills:
        area = k.attacker_area or ctx.area_label(k.round, k.tick, k.attacker_x, k.attacker_y)
        area_kill[area] += 1

    for area, deaths in area_death.most_common(10):
        dirs = area_dir.get(area, Counter())
        top_dir, top_n = (dirs.most_common(1)[0] if dirs else (None, 0))
        res.area_stats.append(AreaDeathStat(
            area=area, deaths=deaths, kills=area_kill.get(area, 0),
            aggressive_deaths=area_aggr.get(area, 0),
            top_direction=top_dir, top_direction_count=top_n,
        ))
    if ct_deaths:
        res.ct_aggressive_death_rate = round(ct_aggr_deaths / ct_deaths, 3)

    # 规则结论
    if res.area_stats:
        top = res.area_stats[0]
        if top.deaths >= 4:
            res.problems.append(Problem(
                type="death_hotspot", severity="medium",
                title=f"死亡热点:{top.area} 共 {top.deaths} 次死亡",
                detail=(f"你在 {top.area} 的死亡占全场死亡的 "
                        f"{top.deaths / max(1, len(valid_deaths)) * 100:.0f}%。"
                        + (f"主要被击杀方向:{top.top_direction}({top.top_direction_count} 次)。" if top.top_direction else "")),
                evidence=[Evidence(round=k.round, tick=k.tick,
                                   description=f"第{k.round}回合死于 {top.area},被 {k.attacker_name} 用 {k.weapon} 击杀")
                          for k in valid_deaths
                          if (k.victim_area or ctx.area_label(k.round, k.tick, k.victim_x, k.victim_y)) == top.area][:8],
            ))
    if res.ct_aggressive_death_rate is not None and res.ct_aggressive_death_rate >= 0.4 and ct_deaths >= 5:
        res.problems.append(Problem(
            type="ct_over_aggressive", severity="medium",
            title=f"CT 防守时 {res.ct_aggressive_death_rate * 100:.0f}% 的死亡发生在回合开始 {AGGRESSIVE_TIME_S:.0f} 秒内(主动前压/抢点)",
            detail="防守端前期死亡过多会丢失人数优势与地图控制,建议降低 20 秒前的主动 peek 频率,改为队友协同或道具覆盖。",
            evidence=[Evidence(round=k.round, tick=k.tick,
                               description=f"第{k.round}回合 CT 方开局 {ctx.round_seconds_elapsed(k.round, k.tick):.0f}s 死亡于 {k.victim_area or '未知区域'}")
                      for k in valid_deaths
                      if k.victim_side == SIDE_CT
                      and rd_map.get(k.round)
                      and rd_map[k.round].freeze_time_end_tick
                      and (k.tick - rd_map[k.round].freeze_time_end_tick) < AGGRESSIVE_TIME_S * ctx.tick_rate][:8],
        ))
    return res

"""awpy 适配器:ZIP 解压 -> awpy 解析 -> 规范化 MatchData。

不重新实现 Demo 解析,一切解析交给 awpy。
自动兼容两代 awpy:
- awpy 2.x:awpy.demo.Demo(polars DataFrame 属性)
- awpy 1.x:awpy.parser.DemoParser.parse()(dict 输出)
本层职责:字段收敛为 backend.common.models 的结构,分析层不感知 awpy 版本。
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.common.models import (
    DamageEvent, Frame, GrenadeEvent, KillEvent, MatchData, PlayerFrame,
    PlayerInfo, RecoveredPosition, RoundData,
)


class ParseError(Exception):
    pass


# ---------------- ZIP 处理 ----------------

def extract_dem_from_zip(zip_path: Path, out_dir: Path) -> Path:
    """解压 ZIP 并返回其中的 .dem 文件路径(支持嵌套目录)。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            dem_names = [n for n in zf.namelist() if n.lower().endswith(".dem")]
            if not dem_names:
                zf.extractall(out_dir)
                dems = list(out_dir.rglob("*.dem"))
                if not dems:
                    raise ParseError("ZIP 中没有找到 .dem 文件")
                return dems[0]
            target = sorted(dem_names, key=len)[0]
            zf.extract(target, out_dir)
            return out_dir / target
    except zipfile.BadZipFile as e:
        raise ParseError(f"不是有效的 ZIP 文件: {e}") from e


def is_zip(data: bytes) -> bool:
    return data[:2] == b"PK"


# ---------------- 工具函数 ----------------

def _f(v: Any, default: float = 0.0) -> float:
    try:
        return default if v is None else float(v)
    except (TypeError, ValueError):
        return default


def _i(v: Any, default: int = 0) -> int:
    try:
        return default if v is None else int(float(v))
    except (TypeError, ValueError):
        return default


def _s(v: Any, default: str = "") -> str:
    return default if v is None else str(v)


def _opt_str(v: Any) -> Optional[str]:
    if v is None or v == "" or v == "None" or v == "0":
        return None
    return str(v)


def _opt_int(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _g(row: Dict[str, Any], *names: str, default: Any = None) -> Any:
    """从行里取第一个存在的字段(兼容大小写/命名变体)。"""
    for n in names:
        if n in row and row[n] is not None:
            return row[n]
    lowered = {k.lower(): v for k, v in row.items()}
    for n in names:
        if n.lower() in lowered and lowered[n.lower()] is not None:
            return lowered[n.lower()]
    return default


def _norm_weapon(w: str) -> str:
    return w.replace("weapon_", "")


def _norm_nade_type(t: str) -> str:
    t = (t or "").lower()
    if "smoke" in t:
        return "Smoke"
    if "flash" in t:
        return "Flash"
    if "incendiary" in t:
        return "Incendiary"
    if "molotov" in t or "fire" in t or "inferno" in t:
        return "Molotov"
    if "decoy" in t:
        return "Decoy"
    if "grenade" in t or "projectile" in t:   # hegrenade / chegrenade / *projectile
        return "HE"
    return t or "Unknown"


def _norm_side(v: Any) -> Optional[int]:
    """awpy 1.x 用 2/3,v2 用 "t"/"ct",事件原始数据用 "T"/"CT"/"TERRORIST"。"""
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("t", "2", "terrorist"):
        return 2
    if s in ("ct", "3"):
        return 3
    try:
        n = int(float(v))
        return n if n in (2, 3) else None
    except (TypeError, ValueError):
        return None


# ================================================================
# awpy 2.x 支持
# ================================================================

def _demo_tickrate(header: Dict[str, Any]) -> int:
    """用 playback ticks / time 估算实际 tick率。"""
    try:
        pt = float(header.get("playback_time") or 0)
        pticks = float(header.get("playback_ticks") or 0)
        if pt > 0 and pticks > 0:
            return max(16, min(256, int(round(pticks / pt))))
    except (TypeError, ValueError):
        pass
    return 64


def _rows(df: Any) -> List[Dict[str, Any]]:
    """polars/pandas DataFrame -> list[dict];已是 list 时直接返回。"""
    if df is None:
        return []
    if isinstance(df, list):
        return [dict(r) for r in df]
    try:
        return df.to_dicts()          # polars
    except AttributeError:
        return df.to_dict(orient="records")   # pandas


def _fill_ticks_positions(ticks: Any) -> Any:
    """tick 坐标前向填充:玩家死亡/未出生时 demoparser2 返回 null,
    按 (玩家, 回合) 继承最近一个非空位置(尸体留在死亡点,出生前无位置则仍为空)。"""
    try:
        import polars as pl
    except ImportError:
        return ticks
    if ticks is None or not hasattr(ticks, "with_columns") or ticks.height == 0:
        return ticks
    return ticks.sort("tick").with_columns([
        pl.col("X").forward_fill().over("steamid", "round_num"),
        pl.col("Y").forward_fill().over("steamid", "round_num"),
        pl.col("Z").forward_fill().over("steamid", "round_num"),
        pl.col("place").forward_fill().over("steamid", "round_num"),
    ])


def _fallback_positions(ticks: Any) -> Any:
    """提取非空位置索引,供事件坐标回填。"""
    try:
        import polars as pl
    except ImportError:
        return None
    if ticks is None or not hasattr(ticks, "filter") or ticks.height == 0:
        return None
    return (
        ticks.filter(pl.col("X").is_not_null())
        .select([
            pl.col("tick"), pl.col("steamid"), pl.col("round_num"),
            pl.col("X").alias("px"), pl.col("Y").alias("py"),
            pl.col("Z").alias("pz"), pl.col("place").alias("pplace"),
        ])
        .sort("tick")
    )


def _fill_event_positions(events: Any, pos_index: Any) -> Any:
    """事件(kills/damages)里为 null 的玩家坐标,用同回合、同玩家最近一个
    非空 tick 位置回填(asof backward join)。世界伤害(attacker 为空)不回填。"""
    try:
        import polars as pl
    except ImportError:
        return events
    if events is None or pos_index is None or not hasattr(events, "join_asof"):
        return events
    if events.height == 0 or "round_num" not in events.columns:
        return events
    out = events.sort("tick")
    for prefix in ("attacker", "victim"):
        sid = f"{prefix}_steamid"
        if sid not in out.columns:
            continue
        joined = (
            out.rename({sid: "_sid"})
            .join_asof(pos_index.rename({"steamid": "_sid"}),
                       on="tick", by=["_sid", "round_num"], strategy="backward")
        )
        for col, fill in ((f"{prefix}_X", "px"), (f"{prefix}_Y", "py"),
                          (f"{prefix}_Z", "pz"), (f"{prefix}_place", "pplace")):
            if col in joined.columns and fill in joined.columns:
                joined = joined.with_columns(
                    pl.coalesce(pl.col(col), pl.col(fill)).alias(col))
        # 还原玩家 steamid 列(不能随中间列一起丢弃)
        out = joined.drop(["px", "py", "pz", "pplace"], strict=False).rename({"_sid": sid})
    return out


def convert_awpy2(demo: Any, match_id: str) -> MatchData:
    """把 awpy 2.x Demo 对象转换为 MatchData(需要先调用过 demo.parse())。"""
    header = getattr(demo, "header", {}) or {}
    tick_rate = _demo_tickrate(header)
    map_name = _s(header.get("map_name"))
    if map_name and not map_name.startswith("de_"):
        map_name = "de_" + map_name

    # ---- 回合 ----
    rounds: List[RoundData] = []
    for r in _rows(getattr(demo, "rounds", None)):
        winner = _norm_side(_g(r, "winner"))
        site = _s(_g(r, "bomb_site", default=""), default="")
        rounds.append(RoundData(
            round=_i(_g(r, "round_num", default=0)),
            start_tick=_i(_g(r, "start", default=0)),
            freeze_time_end_tick=_i(_g(r, "freeze_end", default=0)),
            end_tick=_i(_g(r, "end", default=0)),
            winner=winner,
            reason=_opt_str(_g(r, "reason")),
            bomb_planted=bool(site and site != "not_planted"),
            bomb_site=None if site in ("", "not_planted") else site,
        ))
    rounds.sort(key=lambda x: x.round)
    by_round = {r.round: r for r in rounds}

    def fill_scores() -> None:
        t_score = ct_score = 0
        for r in rounds:
            if r.winner == 2:
                t_score += 1
            elif r.winner == 3:
                ct_score += 1
            r.t_score, r.ct_score = t_score, ct_score

    fill_scores()

    # ---- 位置补偿:demoparser2 在玩家死亡/未出生时返回 null 坐标 ----
    raw_ticks = getattr(demo, "ticks", None)
    ticks_filled = _fill_ticks_positions(raw_ticks)
    pos_index = _fallback_positions(raw_ticks)

    # ---- 击杀 ----
    kills: List[KillEvent] = []
    players: Dict[str, str] = {}
    for k in _rows(_fill_event_positions(getattr(demo, "kills", None), pos_index)):
        rnum = _i(_g(k, "round_num", default=0)) or 1
        rd = by_round.get(rnum)
        base = rd.start_tick if rd else 0
        tick = _i(_g(k, "tick", default=0))
        a_sid = _opt_str(_g(k, "attacker_steamid"))
        v_sid = _opt_str(_g(k, "victim_steamid")) or ""
        if a_sid:
            players.setdefault(a_sid, _s(_g(k, "attacker_name", default=a_sid)))
        if v_sid:
            players.setdefault(v_sid, _s(_g(k, "victim_name", default=v_sid)))
        kills.append(KillEvent(
            round=rnum, tick=tick,
            second=round(max(0, tick - base) / tick_rate, 1),
            attacker_steamid=a_sid,
            attacker_name=_s(_g(k, "attacker_name", default="World"), "World"),
            attacker_side=_norm_side(_g(k, "attacker_side", "attacker_team_name")),
            attacker_x=_f(_g(k, "attacker_X", "attacker_x")),
            attacker_y=_f(_g(k, "attacker_Y", "attacker_y")),
            attacker_z=_f(_g(k, "attacker_Z", "attacker_z")),
            attacker_area=_opt_str(_g(k, "attacker_place", "attacker_last_place_name")),
            victim_steamid=v_sid,
            victim_name=_s(_g(k, "victim_name")),
            victim_side=_norm_side(_g(k, "victim_side", "victim_team_name")),
            victim_x=_f(_g(k, "victim_X", "victim_x")),
            victim_y=_f(_g(k, "victim_Y", "victim_y")),
            victim_z=_f(_g(k, "victim_Z", "victim_z")),
            victim_area=_opt_str(_g(k, "victim_place", "victim_last_place_name")),
            assister_steamid=_opt_str(_g(k, "assister_steamid")),
            assister_name=_opt_str(_g(k, "assister_name")),
            weapon=_norm_weapon(_s(_g(k, "weapon", default="unknown"))),
            headshot=bool(_g(k, "headshot", default=False)),
            penetrated=bool(_g(k, "penetrated", default=False)),
            noscope=bool(_g(k, "noscope", default=False)),
            thrusmoke=bool(_g(k, "thrusmoke", default=False)),
            attacker_blind=bool(_g(k, "attackerblind", "attacker_blind", default=False)),
            victim_blind=bool(_g(k, "victimblind", "victim_blind", default=False)),
            distance=_f(_g(k, "distance"), default=0.0) or None,
        ))

    # ---- 伤害 ----
    damages: List[DamageEvent] = []
    for d in _rows(_fill_event_positions(getattr(demo, "damages", None), pos_index)):
        rnum = _i(_g(d, "round_num", default=0)) or 1
        rd = by_round.get(rnum)
        base = rd.start_tick if rd else 0
        tick = _i(_g(d, "tick", default=0))
        damages.append(DamageEvent(
            round=rnum, tick=tick,
            second=round(max(0, tick - base) / tick_rate, 1),
            attacker_steamid=_opt_str(_g(d, "attacker_steamid")),
            attacker_name=_s(_g(d, "attacker_name", default="World"), "World"),
            attacker_side=_norm_side(_g(d, "attacker_side", "attacker_team_name")),
            attacker_x=_f(_g(d, "attacker_X", "attacker_x")),
            attacker_y=_f(_g(d, "attacker_Y", "attacker_y")),
            attacker_z=_f(_g(d, "attacker_Z", "attacker_z")),
            victim_steamid=_opt_str(_g(d, "victim_steamid")) or "",
            victim_name=_s(_g(d, "victim_name")),
            victim_side=_norm_side(_g(d, "victim_side", "victim_team_name")),
            victim_x=_f(_g(d, "victim_X", "victim_x")),
            victim_y=_f(_g(d, "victim_Y", "victim_y")),
            victim_z=_f(_g(d, "victim_Z", "victim_z")),
            dmg_health=_i(_g(d, "dmg_health")),
            dmg_armor=_i(_g(d, "dmg_armor")),
            weapon=_norm_weapon(_s(_g(d, "weapon", default="unknown"))),
            hitgroup=_i(_g(d, "hitgroup")),
        ))

    # ---- 道具 ----
    events: Dict[str, Any] = getattr(demo, "events", {}) or {}
    flash_det = {int(_g(e, "entityid", "entity_id", default=0) or 0): e
                 for e in _rows(events.get("flashbang_detonate"))}
    he_det = {int(_g(e, "entityid", "entity_id", default=0) or 0): e
              for e in _rows(events.get("hegrenade_detonate"))}

    grenades: List[GrenadeEvent] = []
    # demoparser2 的 grenades 是逐 tick 的轨迹流(同一手雷飞行期间每 tick 一行),
    # 按 entity_id 去重只留最早一行(= 投掷瞬间)
    raw_throws = sorted(_rows(getattr(demo, "grenades", None)),
                        key=lambda r: _i(_g(r, "tick", default=0)))
    seen_entities: set = set()
    seen_fallback: set = set()
    deduped_throws = []
    for g in raw_throws:
        ent = int(_g(g, "entity_id", "grenade_entity_id", "entityid", default=0) or 0)
        if ent:
            if ent in seen_entities:
                continue
            seen_entities.add(ent)
        else:
            key = (str(_g(g, "thrower_steamid", "steamid")),
                   str(_g(g, "tick")), str(_g(g, "grenade_type")))
            if key in seen_fallback:
                continue
            seen_fallback.add(key)
        deduped_throws.append(g)

    # 投掷原点(≈投掷者位置,误差<1m):供 tick 位置缺失的玩家恢复稀疏站位
    throw_origins: Dict[str, List[Dict[str, Any]]] = {}
    for g in deduped_throws:
        gsid_o = _opt_str(_g(g, "thrower_steamid", "steamid"))
        if gsid_o:
            throw_origins.setdefault(gsid_o, []).append({
                "tick": _i(_g(g, "tick", default=0)),
                "round_num": _i(_g(g, "round_num", default=0)) or 1,
                "x": _f(_g(g, "X", "x")), "y": _f(_g(g, "Y", "y")),
            })

    for g in deduped_throws:                            # 每次投掷一行
        ntype = _norm_nade_type(_s(_g(g, "grenade_type", default="")))
        if ntype in ("Smoke", "Molotov"):
            continue   # 烟雾/火焰由实体生命周期表提供,避免重复计数
        rnum = _i(_g(g, "round_num", default=0)) or 1
        rd = by_round.get(rnum)
        base = rd.start_tick if rd else 0
        entity = int(_g(g, "entity_id", "grenade_entity_id", "entityid", default=0) or 0)
        throw_tick = _i(_g(g, "tick", default=0))
        det = flash_det.get(entity) if ntype == "Flash" else (
            he_det.get(entity) if ntype == "HE" else None)
        det_tick = _i(_g(det, "tick", default=0)) if det else None
        gsid = _opt_str(_g(g, "thrower_steamid", "steamid")) or ""
        if gsid:
            players.setdefault(gsid, _s(_g(g, "thrower", "name", default=gsid)))
        destroyed = det_tick if ntype == "Molotov" else None
        grenades.append(GrenadeEvent(
            round=rnum, grenade_type=ntype,
            player_steamid=gsid,
            player_name=_s(_g(g, "thrower", "name")),
            player_side=_norm_side(_g(g, "thrower_side", "thrower_team_name")),
            throw_tick=throw_tick,
            throw_second=round(max(0, throw_tick - base) / tick_rate, 1),
            throw_x=_f(_g(g, "X", "x")), throw_y=_f(_g(g, "Y", "y")), throw_z=_f(_g(g, "Z", "z")),
            detonation_tick=det_tick or None,
            detonation_x=_f(_g(det, "x")) if det else None,
            detonation_y=_f(_g(det, "y")) if det else None,
            detonation_z=_f(_g(det, "z")) if det else None,
            destroyed_tick=destroyed,
            end_second=((det_tick - throw_tick) / tick_rate
                        if det_tick else None),
        ))

    # 烟雾 / 火焰(带实体生命周期)
    for sm, ntype in ((getattr(demo, "smokes", None), "Smoke"),
                      (getattr(demo, "infernos", None), "Molotov")):
        for g in _rows(sm):
            rnum = _i(_g(g, "round_num", default=0)) or 1
            rd = by_round.get(rnum)
            base = rd.start_tick if rd else 0
            start = _i(_g(g, "start_tick", default=0))
            end = _opt_int(_g(g, "end_tick"))
            gsid = _opt_str(_g(g, "thrower_steamid", "steamid")) or ""
            if gsid:
                players.setdefault(gsid, _s(_g(g, "thrower_name", "thrower", "name", default=gsid)))
            grenades.append(GrenadeEvent(
                round=rnum, grenade_type=ntype,
                player_steamid=gsid,
                player_name=_s(_g(g, "thrower_name", "thrower", "name")),
                player_side=_norm_side(_g(g, "thrower_side", "thrower_team_name")),
                throw_tick=start,
                throw_second=round(max(0, start - base) / tick_rate, 1),
                throw_x=_f(_g(g, "X", "x")), throw_y=_f(_g(g, "Y", "y")), throw_z=_f(_g(g, "Z", "z")),
                detonation_tick=start,
                detonation_x=_f(_g(g, "X", "x")),
                detonation_y=_f(_g(g, "Y", "y")),
                detonation_z=_f(_g(g, "Z", "z")),
                destroyed_tick=end,
                end_second=((end - start) / tick_rate if end is not None else None),
            ))

    # ---- 帧(tick 数据采样;死亡/未出生的空坐标已前向填充) ----
    frames: List[Frame] = []
    sample = max(1, tick_rate // 4)     # ~4 帧/秒
    grouped: Dict[int, List[PlayerFrame]] = {}
    last_kept = -(10 ** 9)
    tick_rows = _rows(ticks_filled)
    if not isinstance(ticks_filled, list):
        tick_rows = sorted(tick_rows, key=lambda t: _i(t.get("tick", 0)))
    for t in tick_rows:
        tick = _i(_g(t, "tick", default=0))
        if _g(t, "X", "x") is None:
            continue   # 本回合从未有过位置(未出生),跳过
        if tick not in grouped:
            if tick - last_kept < sample:
                continue
            last_kept = tick
            grouped[tick] = []
        sid = _opt_str(_g(t, "steamid"))
        if not sid:
            continue
        players.setdefault(sid, _s(_g(t, "name", default=sid)))
        hp = _i(_g(t, "health", "hp", default=100))
        grouped[tick].append(PlayerFrame(
            steamid=sid,
            name=_s(_g(t, "name", default=sid)),
            side=_norm_side(_g(t, "side", "team_name")) or 2,
            alive=hp > 0,
            hp=hp,
            armor=_i(_g(t, "armor")),
            x=_f(_g(t, "X", "x")), y=_f(_g(t, "Y", "y")), z=_f(_g(t, "Z", "z")),
            last_place=_opt_str(_g(t, "place", "last_place_name")),
            blind=_f(_g(t, "flash_time", "blind")),
        ))
    for tick, plist in grouped.items():
        rnum = next((r.round for r in rounds
                     if r.start_tick <= tick <= max(r.end_tick, r.start_tick)), None)
        if rnum is None:
            continue
        rd = by_round[rnum]
        frames.append(Frame(
            round=rnum, tick=tick,
            second=round(max(0, tick - rd.start_tick) / tick_rate, 1),
            players=plist,
            t_alive=sum(1 for p in plist if p.alive and p.side == 2),
            ct_alive=sum(1 for p in plist if p.alive and p.side == 3),
        ))
    frames.sort(key=lambda f: (f.round, f.tick))

    # ---- 队伍归属与比分 ----
    # 用"第 1 回合在场玩家"定义 A 队;A 队每回合的阵营随换边变化,按回合阵营计分。
    side_by_round: Dict[int, Dict[str, int]] = {}
    for f in frames:
        d = side_by_round.setdefault(f.round, {})
        for p in f.players:
            d[p.steamid] = p.side
    first_round = rounds[0].round if rounds else 0
    first_sides = side_by_round.get(first_round, {})
    if first_sides:
        ref_side = next(iter(first_sides.values()))
        team_a = {sid for sid, side in first_sides.items() if side == ref_side}
    else:
        team_a = set()
    team_a_side = {}
    for rnum, sids in side_by_round.items():
        side_of_a = next((side for sid, side in sids.items() if sid in team_a), None)
        if side_of_a is not None:
            team_a_side[rnum] = side_of_a
    a_wins = sum(1 for r in rounds
                 if r.winner is not None and r.winner == team_a_side.get(r.round))
    b_wins = sum(1 for r in rounds if r.winner is not None) - a_wins
    player_infos = [PlayerInfo(steamid=sid, name=nm or sid,
                               team_name="A 队" if sid in team_a else "B 队")
                    for sid, nm in sorted(players.items())]
    team_names = ["A 队", "B 队"]
    final_score = {"A 队": a_wins, "B 队": b_wins}
    duration = 0.0
    if rounds and rounds[-1].end_tick:
        duration = (rounds[-1].end_tick - rounds[0].start_tick) / tick_rate

    # ---- 位置缺失玩家的恢复样本(来自道具投掷原点) ----
    # 完美平台等部分 Demo 中,个别玩家的 pawn 坐标全程缺失(demoparser2 返回 null),
    # 用其每次投掷道具的原点(误差<1m)恢复稀疏站位,供热力图展示。
    covered = set(side_by_round.get(first_round, {}))
    for sids in side_by_round.values():
        covered |= set(sids)
    recovered = []
    for sid, origins in throw_origins.items():
        if sid in covered:
            continue   # 有完整 tick 位置的玩家不需要
        for o in origins:
            recovered.append(RecoveredPosition(
                steamid=sid, name=players.get(sid, sid),
                round=o["round_num"], tick=o["tick"], x=o["x"], y=o["y"]))

    return MatchData(
        match_id=match_id, map_name=map_name, tick_rate=tick_rate,
        duration_seconds=round(duration, 1), source="awpy",
        team_names=team_names, final_score=final_score,
        players=player_infos, rounds=rounds,
        kills=sorted(kills, key=lambda k: (k.round, k.tick)),
        damages=sorted(damages, key=lambda d: (d.round, d.tick)),
        grenades=sorted(grenades, key=lambda g: (g.round, g.throw_tick)),
        frames=frames,
        recovered_positions=recovered,
    )


def parse_with_awpy2(dem_path: Path, match_id: str) -> MatchData:
    from awpy.demo import Demo  # type: ignore
    demo = Demo(dem_path)
    try:
        extra = []
        detected = demo.detected_events or []
        if "player_blind" in detected:
            extra = ["player_blind"]
        demo.parse(events=(list(demo.default_events) + extra) if extra else None)
    except Exception as e:
        raise ParseError(f"awpy 解析失败: {e}") from e
    if not len(getattr(demo, "rounds", [])):
        raise ParseError("awpy 解析结果为空(可能不是完整比赛 Demo,如 POV/断线 Demo)")
    return convert_awpy2(demo, match_id)


# ================================================================
# awpy 1.x 支持(旧版 dict 输出)
# ================================================================

def normalize_awpy(data: Dict[str, Any], match_id: str) -> MatchData:
    """awpy 1.x parse() dict -> MatchData(事件按回合嵌套或顶层平铺均可)。"""
    if not isinstance(data, dict) or "rounds" not in data:
        raise ParseError("awpy 输出缺少 rounds 字段,可能是版本不兼容")

    tick_rate = _i(data.get("tick_rate"), 64) or 64
    map_name = _s(data.get("map_name"))
    if map_name and not map_name.startswith("de_"):
        map_name = "de_" + map_name

    players: Dict[str, str] = {}
    raw_rounds = data["rounds"]
    for rd in raw_rounds:
        for k in rd.get("kills", []) or []:
            if k.get("attacker_steamid"):
                players[str(k["attacker_steamid"])] = _s(k.get("attacker_name"))
            if k.get("victim_steamid"):
                players[str(k["victim_steamid"])] = _s(k.get("victim_name"))

    def second_of(tick: int, rd: Dict[str, Any]) -> float:
        return max(0.0, (tick - _i(rd.get("start_tick"))) / tick_rate)

    rounds: List[RoundData] = []
    kills: List[KillEvent] = []
    damages: List[DamageEvent] = []
    grenades: List[GrenadeEvent] = []
    frames: List[Frame] = []
    frame_sample = max(1, tick_rate // 4)

    for idx, rd in enumerate(raw_rounds, start=1):
        rnum = _i(rd.get("round"), idx)
        r = RoundData(
            round=rnum,
            start_tick=_i(rd.get("start_tick")),
            freeze_time_end_tick=_i(rd.get("freeze_time_end_tick")),
            end_tick=_i(rd.get("end_tick")),
            winner=_norm_side(rd.get("winner")) if rd.get("winner") else None,
            reason=_opt_str(rd.get("reason")),
            bomb_planted=bool(rd.get("bomb_planted")),
            bomb_site=_opt_str(rd.get("bomb_site")),
        )
        rounds.append(r)

        for k in rd.get("kills", []) or []:
            a_sid = _opt_str(k.get("attacker_steamid"))
            v_sid = _opt_str(k.get("victim_steamid"))
            tick = _i(k.get("tick"))
            kills.append(KillEvent(
                round=rnum, tick=tick, second=second_of(tick, rd),
                attacker_steamid=a_sid,
                attacker_name=_s(k.get("attacker_name"), "World"),
                attacker_side=_norm_side(k.get("attacker_side")),
                attacker_x=_f(k.get("attacker_x")), attacker_y=_f(k.get("attacker_y")),
                attacker_z=_f(k.get("attacker_z")),
                attacker_area=_opt_str(k.get("attacker_area")),
                victim_steamid=v_sid or "",
                victim_name=_s(k.get("victim_name")),
                victim_side=_norm_side(k.get("victim_side")),
                victim_x=_f(k.get("victim_x")), victim_y=_f(k.get("victim_y")),
                victim_z=_f(k.get("victim_z")),
                victim_area=_opt_str(k.get("victim_area")),
                assister_steamid=_opt_str(k.get("assister_steamid")),
                assister_name=_opt_str(k.get("assister_name")),
                weapon=_norm_weapon(_s(k.get("weapon"))),
                headshot=bool(k.get("headshot")),
                penetrated=bool(k.get("penetrated")),
                noscope=bool(k.get("noscope")),
                thrusmoke=bool(k.get("thrusmoke")),
                attacker_blind=bool(k.get("attackerblind") or k.get("attacker_blind")),
                victim_blind=bool(k.get("victimblinde") or k.get("victim_blind")),
                distance=_f(k.get("distance")) if k.get("distance") is not None else None,
            ))

        for d in rd.get("damages", []) or []:
            damages.append(DamageEvent(
                round=rnum, tick=_i(d.get("tick")), second=second_of(_i(d.get("tick")), rd),
                attacker_steamid=_opt_str(d.get("attacker_steamid")),
                attacker_name=_s(d.get("attacker_name"), "World"),
                attacker_side=_norm_side(d.get("attacker_side")),
                attacker_x=_f(d.get("attacker_x")), attacker_y=_f(d.get("attacker_y")),
                attacker_z=_f(d.get("attacker_z")),
                victim_steamid=_opt_str(d.get("victim_steamid")) or "",
                victim_name=_s(d.get("victim_name")),
                victim_side=_norm_side(d.get("victim_side")),
                victim_x=_f(d.get("victim_x")), victim_y=_f(d.get("victim_y")),
                victim_z=_f(d.get("victim_z")),
                dmg_health=_i(d.get("dmg_health")),
                dmg_armor=_i(d.get("dmg_armor")),
                weapon=_norm_weapon(_s(d.get("weapon"))),
                hitgroup=_i(d.get("hitgroup")),
            ))

        for g in rd.get("grenades", []) or []:
            det = _opt_int(g.get("detonation_tick") or g.get("landing_tick"))
            destroyed = _opt_int(g.get("destroyed_by_tick") or g.get("expiry_tick"))
            gsid = _opt_str(g.get("player_steamid") or g.get("thrower_steamid")) or ""
            grenades.append(GrenadeEvent(
                round=rnum,
                grenade_type=_norm_nade_type(_s(g.get("grenade_type"))),
                player_steamid=gsid,
                player_name=_s(g.get("player_name")),
                player_side=_norm_side(g.get("player_side")),
                throw_tick=_i(g.get("throw_tick")),
                throw_second=second_of(_i(g.get("throw_tick")), rd),
                throw_x=_f(g.get("x") or g.get("throw_x")),
                throw_y=_f(g.get("y") or g.get("throw_y")),
                throw_z=_f(g.get("z") or g.get("throw_z")),
                detonation_tick=det,
                detonation_x=_f(g.get("detonation_x")) if g.get("detonation_x") is not None else None,
                detonation_y=_f(g.get("detonation_y")) if g.get("detonation_y") is not None else None,
                detonation_z=_f(g.get("detonation_z")) if g.get("detonation_z") is not None else None,
                destroyed_tick=destroyed,
                destroyed_by_steamid=_opt_str(g.get("destroyed_by_steamid")),
                end_second=((destroyed - det) / tick_rate
                            if det is not None and destroyed is not None else None),
            ))

        raw_frames = rd.get("frames", []) or []
        last_kept = -10 ** 9
        for f in raw_frames:
            tick = _i(f.get("tick"))
            if tick - last_kept < frame_sample:
                continue
            last_kept = tick
            pf_list: List[PlayerFrame] = []
            t_alive = ct_alive = 0
            candidates: List[Dict[str, Any]] = []
            for team_key in ("t", "ct"):
                grp = f.get(team_key)
                if isinstance(grp, dict):
                    grp = grp.get("players", [])
                if isinstance(grp, list):
                    for p in grp:
                        if isinstance(p, dict) and "steamid" in p:
                            candidates.append(p)
            for p in candidates:
                side = _norm_side(p.get("side") or p.get("team"))
                alive = p.get("alive", True)
                alive = alive if isinstance(alive, bool) else str(alive).lower() != "false"
                if alive:
                    if side == 2:
                        t_alive += 1
                    elif side == 3:
                        ct_alive += 1
                pf_list.append(PlayerFrame(
                    steamid=str(p.get("steamid")),
                    name=_s(p.get("name")),
                    side=side or 2,
                    alive=alive,
                    hp=_i(p.get("hp"), 100),
                    armor=_i(p.get("armor")),
                    helmet=bool(p.get("helmet")),
                    x=_f(p.get("x")), y=_f(p.get("y")), z=_f(p.get("z")),
                    last_place=_opt_str(p.get("last_place")),
                    active_weapon=_norm_weapon(_s(p.get("active_weapon")))
                    if p.get("active_weapon") else None,
                    blind=_f(p.get("blind")),
                    has_bomb=bool(p.get("has_bomb")),
                    has_defuser=bool(p.get("has_defuser")),
                    money=_opt_int(p.get("money")),
                ))
            frames.append(Frame(
                round=rnum, tick=tick, second=second_of(tick, rd),
                players=pf_list, t_alive=t_alive, ct_alive=ct_alive,
                bomb_planted=bool(f.get("bomb_planted")),
                bomb_site=_opt_str(f.get("bomb_site")),
            ))

    # 记分
    t_score = sum(1 for r in rounds if r.winner == 2)
    ct_score = sum(1 for r in rounds if r.winner == 3)
    team_names = ["T(上半场)", "CT(上半场)"]
    final_score = {"T(上半场)": t_score, "CT(上半场)": ct_score}

    player_infos = [PlayerInfo(steamid=sid, name=nm or sid) for sid, nm in sorted(players.items())]
    duration = 0.0
    if rounds and rounds[-1].end_tick:
        duration = (rounds[-1].end_tick - rounds[0].start_tick) / tick_rate

    return MatchData(
        match_id=match_id, map_name=map_name, tick_rate=tick_rate,
        duration_seconds=round(duration, 1), source="awpy",
        team_names=team_names, final_score=final_score,
        players=player_infos, rounds=rounds,
        kills=sorted(kills, key=lambda k: (k.round, k.tick)),
        damages=sorted(damages, key=lambda d: (d.round, d.tick)),
        grenades=sorted(grenades, key=lambda g: (g.round, g.throw_tick)),
        frames=sorted(frames, key=lambda f: (f.round, f.tick)),
    )


# ---------------- 主入口 ----------------

def parse_demo_file(dem_path: Path, match_id: str, parse_frames: bool = True) -> MatchData:
    """用 awpy 解析 .dem 文件并返回规范化 MatchData(自动选择 v2/v1 API)。"""
    # 优先 awpy 2.x
    try:
        from awpy.demo import Demo  # noqa: F401
        return parse_with_awpy2(dem_path, match_id)
    except ImportError:
        pass
    try:
        from awpy.parser import DemoParser  # type: ignore
    except ImportError as e:
        raise ParseError(
            "未安装 awpy(pip install awpy)。示例比赛不受影响,解析真实 Demo 必须安装 awpy。"
        ) from e

    parser = DemoParser(
        demofile=str(dem_path), demo_id=match_id,
        parse_frames=parse_frames, parse_kill=True, parse_damage=True,
        parse_grenades=True, parse_waves=False, buy_style="hltv",
    )
    try:
        data = parser.parse()
    except Exception as e:
        raise ParseError(f"awpy 解析失败: {e}") from e
    if not data or not data.get("rounds"):
        raise ParseError("awpy 解析结果为空(可能不是完整比赛 Demo,如 POV/断线 Demo)")
    return normalize_awpy(data, match_id)


def parse_zip_file(zip_path: Path, work_dir: Path, match_id: str,
                   parse_frames: bool = True) -> MatchData:
    dem = extract_dem_from_zip(zip_path, work_dir / match_id)
    return parse_demo_file(dem, match_id, parse_frames=parse_frames)

"""Module 4 道具分析:烟、闪、火、雷。

说明:demo 中没有直接的"白敌秒数"事件时,闪光效果用可核查的代理指标:
- victim_blind 击杀(击杀时对手处于被白状态)
- 闪光落地后 3 秒内、落点 1200 单位范围内发生的己方击杀(协同闪生效)
所有代理指标都在输出中明确标注,不冒充精确数据。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from backend.analyzer.common import MatchContext, dist
from backend.common.models import Evidence, GrenadeEvent, MatchData, Problem, Strength

SMOKE_FULL_S = 18.0     # 烟雾理论满时长
SMOKE_EARLY_S = 10.0    # 低于该时长视为"被道具处理/提前失效"


class SmokeStats(BaseModel):
    thrown: int = 0
    avg_duration: float = 0.0      # 有消散时间的烟雾平均持续秒数
    early_expired: int = 0         # 持续 < SMOKE_EARLY_S 的数量
    effective_kills_inside: int = 0  # 烟雾持续期内、烟点附近的击杀(卡烟进攻)


class FlashStats(BaseModel):
    thrown: int = 0
    blind_kills: int = 0           # victim_blind 击杀(对手被白状态下被击杀,含队友完成)
    followup_kills: int = 0        # 落地 3 秒内、落点附近己方击杀数
    deaths_while_blind: int = 0    # 本人被白状态死亡


class FireStats(BaseModel):
    thrown: int = 0
    damage: int = 0


class HeStats(BaseModel):
    thrown: int = 0
    damage: int = 0


class UtilityResult(BaseModel):
    target_steamid: str
    smoke: SmokeStats = Field(default_factory=SmokeStats)
    flash: FlashStats = Field(default_factory=FlashStats)
    fire: FireStats = Field(default_factory=FireStats)
    he: HeStats = Field(default_factory=HeStats)
    team_avg_flash_thrown: float = 0.0
    team_avg_smoke_thrown: float = 0.0
    problems: List[Problem] = Field(default_factory=list)
    strengths: List[Strength] = Field(default_factory=list)


def analyze_utility(match: MatchData, ctx: MatchContext, target_steamid: str) -> UtilityResult:
    res = UtilityResult(target_steamid=target_steamid)
    mates = ctx.teammates_of(target_steamid)

    nades: List[GrenadeEvent] = [g for g in match.grenades if g.player_steamid == target_steamid]
    smoke_durations: List[float] = []
    for g in nades:
        if g.grenade_type == "Smoke":
            res.smoke.thrown += 1
            if g.detonation_tick and g.destroyed_tick:
                dur = (g.destroyed_tick - g.detonation_tick) / ctx.tick_rate
                smoke_durations.append(dur)
                if dur < SMOKE_EARLY_S:
                    res.smoke.early_expired += 1
        elif g.grenade_type == "Flash":
            res.flash.thrown += 1
        elif g.grenade_type in ("Molotov", "Incendiary"):
            res.fire.thrown += 1
        elif g.grenade_type == "HE":
            res.he.thrown += 1
    if smoke_durations:
        res.smoke.avg_duration = round(sum(smoke_durations) / len(smoke_durations), 1)

    # 伤害类道具
    for d in match.damages:
        if d.attacker_steamid != target_steamid:
            continue
        if d.weapon in ("HE Grenade", "hegrenade"):
            res.he.damage += d.dmg_health
        elif d.weapon in ("Molotov", "Incendiary Grenade", "inferno"):
            res.fire.damage += d.dmg_health

    # 闪光效果(代理指标)
    for k in match.kills:
        if k.attacker_steamid == target_steamid and k.victim_blind:
            res.flash.blind_kills += 1
        elif k.attacker_steamid in mates and k.attacker_steamid != target_steamid \
                and k.victim_blind and k.assister_steamid == target_steamid:
            res.flash.blind_kills += 1   # 闪光助攻
    for k in match.kills:
        if k.victim_steamid == target_steamid and k.victim_blind:
            res.flash.deaths_while_blind += 1
    for g in nades:
        if g.grenade_type != "Flash":
            continue
        det = g.detonation_tick or g.throw_tick
        gx = g.detonation_x if g.detonation_x is not None else g.throw_x
        gy = g.detonation_y if g.detonation_y is not None else g.throw_y
        for k in match.kills:
            if k.round == g.round and det < k.tick <= det + int(3 * ctx.tick_rate) \
                    and k.attacker_steamid in mates and dist(gx, gy, k.victim_x, k.victim_y) <= 1200:
                res.flash.followup_kills += 1
                break

    # 团队平均(同队,不含本人)
    team_flash = 0
    team_smoke = 0
    n_teammates = 0
    for sid in mates - {target_steamid}:
        n_teammates += 1
        for g in match.grenades:
            if g.player_steamid == sid:
                if g.grenade_type == "Flash":
                    team_flash += 1
                elif g.grenade_type == "Smoke":
                    team_smoke += 1
    if n_teammates:
        res.team_avg_flash_thrown = round(team_flash / n_teammates, 1)
        res.team_avg_smoke_thrown = round(team_smoke / n_teammates, 1)

    # 规则结论
    if res.flash.thrown >= 3:
        per_flash = (res.flash.blind_kills + res.flash.followup_kills) / res.flash.thrown
        if per_flash < 0.5:
            res.problems.append(Problem(
                type="flash_utilization_low", severity="medium",
                title=f"闪光利用率低:{res.flash.thrown} 颗闪光仅转化 {res.flash.blind_kills + res.flash.followup_kills} 次击杀",
                detail=(f"平均每颗闪光转化 {per_flash:.1f} 次击杀/白杀(代理指标:victim_blind 击杀 + 落地3秒内的协同击杀)。"
                        "推荐练习:进攻前协同闪(pop flash)、贴墙短闪、与队友报点配合。"),
                evidence=[Evidence(round=g.round, tick=g.throw_tick,
                                   description=f"第{g.round}回合在 ({g.throw_x:.0f},{g.throw_y:.0f}) 丢出闪光")
                          for g in nades if g.grenade_type == "Flash"][:8],
            ))
        elif per_flash >= 1.0:
            res.strengths.append(Strength(
                title=f"闪光使用高效:每颗闪光平均转化 {per_flash:.1f} 次击杀",
                detail=f"{res.flash.thrown} 颗闪光带来 {res.flash.blind_kills + res.flash.followup_kills} 次白杀/协同击杀。",
            ))
    if res.flash.deaths_while_blind >= 3:
        res.problems.append(Problem(
            type="blind_death", severity="medium",
            title=f"{res.flash.deaths_while_blind} 次被白状态死亡",
            detail="被白后仍暴露在枪线或未背闪。建议:听到闪落点立即转身背闪、被白后撤掩体。",
            evidence=[Evidence(round=k.round, tick=k.tick,
                               description=f"第{k.round}回合被 {k.attacker_name} 击杀时处于被白状态")
                      for k in match.kills if k.victim_steamid == target_steamid and k.victim_blind][:8],
        ))
    if res.smoke.thrown >= 3 and res.smoke.avg_duration > 0 and res.smoke.avg_duration < SMOKE_FULL_S - 6:
        res.problems.append(Problem(
            type="smoke_effectiveness_low", severity="low",
                title=f"烟雾平均仅持续 {res.smoke.avg_duration:.0f} 秒(理论 {SMOKE_FULL_S:.0f} 秒)",
            detail="烟雾频繁被道具处理或落点不佳(穿透缝隙),封锁价值降低。",
            evidence=[Evidence(round=g.round, tick=g.throw_tick,
                               description=f"第{g.round}回合烟雾落地 ({g.throw_x:.0f},{g.throw_y:.0f})")
                      for g in nades if g.grenade_type == "Smoke"][:6],
        ))
    return res

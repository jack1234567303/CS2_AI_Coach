"""示例比赛数据生成器。

没有真实 Demo 时,用脚本化的合成比赛驱动/验证整条分析链路。
生成的比赛刻意包含以下可检测模式(用于验证分析器与测试):
- R2/R8/R10  孤立无支援的单摸死亡(A Ramp 热点)
- R3/R6/R19  主动先手但无队友支援、无协同闪的 peek 死亡(Mid / B Apps)
- R4/R22/R18 近距离队友倒地未补枪(补枪意识问题)
- R5/R20     突破首杀成功
- R7/R23     协同闪光下的击杀(优势样本)
- R13/R14/R17/R19 CT 回合开局 20 秒内的前压死亡
- R15        被白状态死亡
- R24        队友全灭后的残局死亡
所有数据确定性生成(固定 seed),测试可精确断言。
"""
from __future__ import annotations

import random
import zlib
from typing import Dict, List, Optional, Tuple

from backend.common.models import (
    DamageEvent, Frame, GrenadeEvent, KillEvent, MatchData, PlayerFrame,
    PlayerInfo, RoundData, SIDE_CT, SIDE_T,
)

SEED = 42
MAP = "de_mirage"

# mirage 主要区域布局(虚拟坐标系:x 向右增大、y 向上增大)
# 再经仿射变换映射进真实 de_mirage 雷达范围(pos_x=-3230, pos_y=1713, scale=5,
# 即世界 x∈[-3230,1890]、y∈[-3407,1713]),保证热力点落在雷达图内
_RAW_AREAS: Dict[str, Tuple[float, float]] = {
    "T Spawn": (700, 2400),
    "A Ramp": (-1400, 2200),
    "Palace": (-600, 2600),
    "A Site": (-1100, 1300),
    "Jungle": (-100, 1100),
    "Connector": (-50, 700),
    "Mid": (400, 600),
    "Top Mid": (600, 1400),
    "Window": (300, -50),
    "Catwalk": (800, 900),
    "B Apps": (1500, 1800),
    "B Site": (1300, 200),
    "Kitchen": (900, 100),
    "CT Spawn": (700, -600),
    "Short Cat": (600, 1000),
}


def _to_radar(x: float, y: float) -> Tuple[float, float]:
    rx = -2800.0 + (x + 1400.0) * (4000.0 / 2900.0)
    ry = 1500.0 - (y + 600.0) * (4000.0 / 3200.0)
    return rx, ry


AREAS: Dict[str, Tuple[float, float]] = {name: _to_radar(x, y)
                                         for name, (x, y) in _RAW_AREAS.items()}

# 默认走位路线的途经点:spawn -> waypoint -> 计划点位
# 让所有玩家每回合都有"出生-移动-就位"的活动轨迹,而不是钉死在一个点上
_ROUTE_WAYPOINTS_T = {
    "Mid": "Top Mid", "A Ramp": "Top Mid", "B Apps": "T Spawn", "Palace": "Top Mid",
    "Top Mid": "T Spawn", "Catwalk": "Top Mid", "A Site": "Top Mid", "Kitchen": "B Apps",
    "B Site": "B Apps", "CT Spawn": "Mid", "Window": "Top Mid", "Jungle": "Top Mid",
    "Short Cat": "Top Mid", "Connector": "Top Mid",
}
_ROUTE_WAYPOINTS_CT = {
    "A Site": "Jungle", "Mid": "Window", "B Site": "Kitchen", "A Ramp": "Jungle",
    "B Apps": "Kitchen", "Top Mid": "Mid", "Palace": "Jungle", "Kitchen": "CT Spawn",
    "Catwalk": "Mid", "Window": "CT Spawn", "Jungle": "CT Spawn", "CT Spawn": "Jungle",
    "Short Cat": "Mid", "Connector": "Jungle",
}

ALPHA = [("76561198000000001", "小明"), ("76561198000000002", "阿伟"),
         ("76561198000000003", "强子"), ("76561198000000004", "老王"),
         ("76561198000000005", "大刘")]
BRAVO = [("76561198000000011", "暗影"), ("76561198000000012", "幽灵"),
         ("76561198000000013", "夜枭"), ("76561198000000014", "毒蛇"),
         ("76561198000000015", "猎鹰")]
ALPHA_NAME, BRAVO_NAME = "阿尔法战队", "布拉沃战队"

_ALPHA_IDS = {sid for sid, _ in ALPHA}
_BRAVO_IDS = {sid for sid, _ in BRAVO}

TARGET = ALPHA[0][0]
TICK_RATE = 64
FREEZE_S = 15.0


def _jit(rng: random.Random, key: str, scale: float = 120.0) -> Tuple[float, float]:
    # 确定性抖动:hash() 跨进程随机,用 crc32 保证示例数据可复现
    h1 = zlib.crc32(key.encode("utf-8"))
    h2 = zlib.crc32(key[::-1].encode("utf-8"))
    return ((h1 % 1000) / 1000 - 0.5) * 2 * scale, ((h2 % 1000) / 1000 - 0.5) * 2 * scale


class RoundBuilder:
    """构造单个回合的事件与帧。时间单位:秒(相对回合开始)。"""

    def __init__(self, rnum: int, start_tick: int, alpha_side: int, rng: random.Random):
        self.rnum = rnum
        self.start_tick = start_tick
        self.rng = rng
        self.alpha_side = alpha_side
        self.t2s = FREEZE_S   # 当前剧情时间(秒),事件从冻结时间结束开始排
        self.kills: List[KillEvent] = []
        self.damages: List[DamageEvent] = []
        self.grenades: List[GrenadeEvent] = []
        self.area_at: Dict[str, List[Tuple[float, str]]] = {}   # steamid -> [(t, area)]
        self._route_idx: Dict[str, List[int]] = {}              # 默认路线节点下标
        self.dead_at: Dict[str, float] = {}
        self.blind_until: Dict[str, float] = {}                 # steamid -> t
        self.last_t: float = FREEZE_S

    # -- 基础 --
    def side_of(self, sid: str) -> int:
        return self.alpha_side if sid in _ALPHA_IDS else (
            SIDE_CT if self.alpha_side == SIDE_T else SIDE_T)

    def name_of(self, sid: str) -> str:
        for s, n in ALPHA + BRAVO:
            if s == sid:
                return n
        return sid

    def t(self, seconds: Optional[float] = None) -> int:
        """剧情时间 -> tick(传入 None 则推进 0.6~2s)。"""
        if seconds is None:
            self.t2s += self.rng.uniform(0.6, 1.6)
        else:
            self.t2s = max(self.t2s, seconds)
        self.last_t = max(self.last_t, self.t2s)
        return self.start_tick + int(self.t2s * TICK_RATE)

    def move(self, sid: str, area: str, at_s: float, route: bool = False):
        """记录走位。route=True 表示默认路线节点,场景脚本可用更晚的 move 覆盖。"""
        tl = self.area_at.setdefault(sid, [])
        if route:
            self._route_idx.setdefault(sid, []).append(len(tl))
        tl.append((at_s, area))

    def apply_routes(self, plan_map: Dict[str, str]):
        """给所有玩家安排默认走位:出生点 -> 途经点 -> 计划点位。"""
        for sid, area in plan_map.items():
            side = self.side_of(sid)
            spawn = "T Spawn" if side == SIDE_T else "CT Spawn"
            wp_map = _ROUTE_WAYPOINTS_T if side == SIDE_T else _ROUTE_WAYPOINTS_CT
            waypoint = wp_map.get(area, spawn)
            self.move(sid, spawn, FREEZE_S + 1, route=True)
            if waypoint not in (spawn, area):
                self.move(sid, waypoint, FREEZE_S + self.rng.uniform(3.5, 6), route=True)
            self.move(sid, area, FREEZE_S + self.rng.uniform(8, 12), route=True)

    def truncate_routes(self):
        """场景脚本的 move 之后,删除更晚的默认路线节点,保证脚本走位意图不被覆盖。"""
        for sid, tl in list(self.area_at.items()):
            ridx = set(self._route_idx.get(sid, []))
            if not ridx:
                continue
            last_scripted = max((t for i, (t, _) in enumerate(tl) if i not in ridx),
                                default=-1.0)
            if last_scripted < 0:
                continue
            self.area_at[sid] = [e for i, e in enumerate(tl)
                                 if i not in ridx or e[0] <= last_scripted]

    def pos(self, sid: str, at_s: float) -> Tuple[float, float, str]:
        timeline = self.area_at.get(sid)
        if not timeline:
            area = "T Spawn" if self.side_of(sid) == SIDE_T else "CT Spawn"
        else:
            area = timeline[0][1]
            for tt, aa in timeline:
                if tt <= at_s:
                    area = aa
        jx, jy = _jit(self.rng, f"{sid}-{area}-{int(at_s)}")
        x, y = AREAS.get(area, (0.0, 0.0))
        return x + jx, y + jy, area

    # -- 事件 --
    def damage(self, attacker: str, victim: str, at_s: float, dmg: int, weapon: str):
        tick = self.start_tick + int(at_s * TICK_RATE)
        ax, ay, aa = self.pos(attacker, at_s)
        vx, vy, va = self.pos(victim, at_s)
        self.damages.append(DamageEvent(
            round=self.rnum, tick=tick, second=at_s,
            attacker_steamid=attacker, attacker_name=self.name_of(attacker),
            attacker_side=self.side_of(attacker), attacker_x=ax, attacker_y=ay,
            victim_steamid=victim, victim_name=self.name_of(victim),
            victim_side=self.side_of(victim), victim_x=vx, victim_y=vy,
            dmg_health=dmg, dmg_armor=0, weapon=weapon, hitgroup=1 if self.rng.random() < 0.3 else 4,
        ))

    def kill(self, attacker: str, victim: str, at_s: float, weapon: Optional[str] = None,
             headshot: bool = False, victim_blind: bool = False):
        weapon = weapon or ("ak47" if self.side_of(attacker) == SIDE_T else "m4a1")
        # 击杀前 2 笔伤害
        self.damage(attacker, victim, max(FREEZE_S + 0.2, at_s - 0.4),
                    self.rng.randint(18, 40), weapon)
        self.damage(attacker, victim, max(FREEZE_S + 0.2, at_s - 0.1),
                    self.rng.randint(22, 45), weapon)
        tick = self.start_tick + int(at_s * TICK_RATE)
        ax, ay, aa = self.pos(attacker, at_s)
        vx, vy, va = self.pos(victim, at_s)
        self.kills.append(KillEvent(
            round=self.rnum, tick=tick, second=at_s,
            attacker_steamid=attacker, attacker_name=self.name_of(attacker),
            attacker_side=self.side_of(attacker), attacker_x=ax, attacker_y=ay,
            attacker_area=aa,
            victim_steamid=victim, victim_name=self.name_of(victim),
            victim_side=self.side_of(victim), victim_x=vx, victim_y=vy,
            victim_area=va,
            weapon=weapon, headshot=headshot, victim_blind=victim_blind,
            distance=round(((ax - vx) ** 2 + (ay - vy) ** 2) ** 0.5, 1),
        ))
        self.dead_at[victim] = at_s
        self.t2s = at_s
        self.last_t = max(self.last_t, at_s)

    def nade(self, player: str, ntype: str, throw_s: float, land_area: str,
             duration_s: Optional[float] = None):
        throw_tick = self.start_tick + int(throw_s * TICK_RATE)
        fly = {"Smoke": 1.3, "Flash": 1.7, "HE": 1.0, "Molotov": 1.2, "Incendiary": 1.2}.get(ntype, 1.2)
        det_tick = throw_tick + int(fly * TICK_RATE)
        tx, ty, _ = self.pos(player, throw_s)
        lx, ly = AREAS.get(land_area, (tx, ty))
        jx, jy = _jit(self.rng, f"nade-{self.rnum}-{ntype}-{land_area}", 80)
        destroyed = det_tick + int((duration_s if duration_s else
                                   {"Smoke": 17.0, "Molotov": 6.5, "Incendiary": 6.5}.get(ntype, 1.5)) * TICK_RATE) \
            if ntype in ("Smoke", "Molotov", "Incendiary") else None
        self.grenades.append(GrenadeEvent(
            round=self.rnum, grenade_type=ntype,
            player_steamid=player, player_name=self.name_of(player),
            player_side=self.side_of(player),
            throw_tick=throw_tick, throw_second=throw_s, throw_x=tx, throw_y=ty,
            detonation_tick=det_tick, detonation_x=lx + jx, detonation_y=ly + jy,
            destroyed_tick=destroyed,
            end_second=((destroyed - det_tick) / TICK_RATE) if destroyed else None,
        ))

    def flash_for(self, victim: str, until_s: float):
        """被白状态(用于 victim_blind 击杀)。"""
        self.blind_until[victim] = until_s

    # -- 输出 --
    def build_frames(self) -> List[Frame]:
        frames: List[Frame] = []
        end_s = self.last_t + 2.0
        t = FREEZE_S - 1.0
        while t <= end_s:
            tick = self.start_tick + int(t * TICK_RATE)
            players: List[PlayerFrame] = []
            ta = ca = 0
            for sid, _ in ALPHA + BRAVO:
                dead = sid in self.dead_at and self.dead_at[sid] <= t
                x, y, area = self.pos(sid, t)
                side = self.side_of(sid)
                dmg_taken = sum(d.dmg_health for d in self.damages
                                if d.victim_steamid == sid and d.second <= t)
                hp = 0 if dead else max(1, 100 - dmg_taken)
                alive = not dead
                if alive:
                    if side == SIDE_T:
                        ta += 1
                    else:
                        ca += 1
                blind = max(0.0, self.blind_until.get(sid, 0) - t)
                players.append(PlayerFrame(
                    steamid=sid, name=self.name_of(sid), side=side, alive=alive,
                    hp=hp, armor=100, helmet=True, x=x, y=y,
                    last_place=area, active_weapon="ak47" if side == SIDE_T else "m4a1",
                    blind=blind, money=4000,
                ))
            frames.append(Frame(round=self.rnum, tick=tick, second=t,
                                players=players, t_alive=ta, ct_alive=ca))
            t += 0.25
        return frames


def _weapon_for(side: int, rng: random.Random) -> str:
    return rng.choice(["ak47", "glock", "deagle"] if side == SIDE_T else ["m4a1", "usp", "awp"])


def generate_sample_match(match_id: str = "sample-0001") -> MatchData:
    rng = random.Random(SEED)
    kills: List[KillEvent] = []
    damages: List[DamageEvent] = []
    grenades: List[GrenadeEvent] = []
    frames: List[Frame] = []
    rounds: List[RoundData] = []

    # 每回合谁赢(True=Alpha,最终 13-11)
    alpha_wins = {1: True, 2: False, 3: True, 4: False, 5: True, 6: True, 7: True,
                  8: False, 9: True, 10: False, 11: True, 12: True, 13: False, 14: False,
                  15: False, 16: True, 17: False, 18: True, 19: False, 20: True, 21: False,
                  22: True, 23: True, 24: False}
    alpha_score = bravo_score = 0
    tick_cursor = 100000

    for rnum in range(1, 25):
        alpha_side = SIDE_T if rnum <= 12 else SIDE_CT
        bravo_side = SIDE_CT if alpha_side == SIDE_T else SIDE_T
        b = RoundBuilder(rnum, tick_cursor, alpha_side, rng)

        # 初始站位计划(T 进攻 / CT 防守),全部玩家走默认路线 spawn -> waypoint -> 点位
        if alpha_side == SIDE_T:
            plan_map = {sid: area for sid, area in zip(
                [x[0] for x in ALPHA], ["Mid", "A Ramp", "B Apps", "B Apps", "Mid"])}
            plan_map.update({sid: area for sid, area in zip(
                [x[0] for x in BRAVO], ["A Site", "A Site", "Window", "B Site", "B Site"])})
        else:
            plan_map = {sid: area for sid, area in zip(
                [x[0] for x in ALPHA], ["Mid", "A Site", "A Site", "B Site", "B Site"])}
            plan_map.update({sid: area for sid, area in zip(
                [x[0] for x in BRAVO], ["A Ramp", "Top Mid", "B Apps", "Mid", "Palace"])})
        b.apply_routes(plan_map)

        # 通用道具:队友常规道具 + 目标自己的低效闪光(丢在远离交火的位置)
        for sid, nm in ALPHA[1:]:
            b.nade(sid, "Smoke", FREEZE_S + rng.uniform(3, 8),
                   rng.choice(["Mid", "Window", "CT Spawn"]))
        b.nade(ALPHA[1][0], "Flash", FREEZE_S + rng.uniform(4, 9),
               "T Spawn" if alpha_side == SIDE_T else "CT Spawn")
        if rnum % 2 == 0:
            b.nade(ALPHA[2][0], "Flash", FREEZE_S + rng.uniform(4, 9),
                   "T Spawn" if alpha_side == SIDE_T else "CT Spawn")
        if rng.random() < 0.5:
            b.nade(ALPHA[3][0], "Molotov", FREEZE_S + rng.uniform(5, 10), rng.choice(["A Site", "B Site"]))
        b.nade(TARGET, "Flash", FREEZE_S + rng.uniform(2, 6),
               "T Spawn" if alpha_side == SIDE_T else "CT Spawn")
        if rnum % 3 == 0:
            b.nade(TARGET, "Smoke", FREEZE_S + rng.uniform(3, 7), rng.choice(["Mid", "Window"]),
                   duration_s=rng.choice([12.0, 14.0, 16.0, 9.0]))

        # 部分回合由队友先拿下首杀(控制目标的首杀参与数)
        s = FREEZE_S
        early_kill_rounds = {2: 3, 3: 3, 4: 3, 6: 4, 8: 3, 10: 3, 13: 3, 14: 3,
                             17: 3, 19: 4, 21: 3, 22: 2}
        if rnum in early_kill_rounds:
            b.kill(ALPHA[4][0], BRAVO[early_kill_rounds[rnum]][0], s + 6)

        # -------- 场景脚本 --------
        if rnum == 1:
            # 正常团战:协同闪 + 目标双杀 + 存活
            b.nade(ALPHA[1][0], "Flash", s + 12.0, "Mid")
            b.flash_for(BRAVO[2][0], s + 14.5)
            b.kill(TARGET, BRAVO[2][0], s + 13.5, victim_blind=True)
            b.kill(TARGET, BRAVO[0][0], s + 22)
        elif rnum == 2:
            # 单摸死亡:目标独走 A Ramp,先被打击(未先手),队友全在 B 区
            for i in range(1, 5):
                b.move(ALPHA[i][0], "B Apps", s + 2)
            b.move(TARGET, "A Ramp", s + 6)
            b.kill(BRAVO[0][0], TARGET, s + 22, headshot=True)
        elif rnum == 3:
            # 无保护 peek:目标在 Mid 先开枪(先手伤害),队友远,无协同闪,死亡
            b.move(ALPHA[4][0], "T Spawn", s + 2)
            b.damage(TARGET, BRAVO[2][0], s + 18.0, 41, "ak47")
            b.kill(BRAVO[2][0], TARGET, s + 18.6)
        elif rnum == 4:
            # 补枪迟到死亡:阿伟在远处(A Ramp)倒地,目标未补,3.5秒后被同一凶手击杀
            b.move(ALPHA[1][0], "A Ramp", s + 8)
            b.move(TARGET, "Mid", s + 8)
            b.kill(BRAVO[1][0], ALPHA[1][0], s + 20)
            b.kill(BRAVO[1][0], TARGET, s + 23.5)
        elif rnum == 5:
            # 突破首杀成功(Palace),目标后被击杀但被补枪
            b.move(TARGET, "Palace", s + 7)
            b.kill(TARGET, BRAVO[0][0], s + 19, headshot=True)
            b.kill(BRAVO[2][0], TARGET, s + 26)
            b.kill(ALPHA[4][0], BRAVO[2][0], s + 28)
        elif rnum == 6:
            # 无保护 peek 死亡(B Apps)
            b.move(TARGET, "B Apps", s + 9)
            b.move(ALPHA[2][0], "Catwalk", s + 2)
            b.move(ALPHA[3][0], "Catwalk", s + 2)
            b.damage(TARGET, BRAVO[3][0], s + 21.0, 55, "ak47")
            b.kill(BRAVO[3][0], TARGET, s + 21.8)
        elif rnum == 7:
            # 协同闪优势局
            b.nade(ALPHA[1][0], "Flash", s + 15.0, "Mid")
            b.kill(TARGET, BRAVO[4][0], s + 16.6)
            b.kill(TARGET, BRAVO[1][0], s + 24)
        elif rnum == 8:
            # 再次单摸 A Ramp(死亡热点)
            for i in range(1, 5):
                b.move(ALPHA[i][0], "B Apps", s + 2)
            b.move(TARGET, "A Ramp", s + 5)
            b.kill(BRAVO[0][0], TARGET, s + 19, headshot=True)
        elif rnum == 9:
            # 目标完成补枪(强子倒地 1.5s 后)+ 额外输出
            b.damage(TARGET, BRAVO[1][0], s + 14, 60, "ak47")
            b.kill(BRAVO[0][0], ALPHA[2][0], s + 17)
            b.kill(TARGET, BRAVO[0][0], s + 18.5, headshot=True)
        elif rnum == 10:
            # 无保护 peek,A Ramp 第三次死亡
            b.move(ALPHA[1][0], "B Apps", s + 2)
            b.move(ALPHA[2][0], "B Apps", s + 2)
            b.move(TARGET, "A Ramp", s + 4)
            b.damage(TARGET, BRAVO[1][0], s + 17.5, 33, "ak47")
            b.kill(BRAVO[1][0], TARGET, s + 18.2)
        elif rnum == 11:
            # 首杀尝试失败:目标第一个倒,但被队友补
            b.kill(BRAVO[4][0], TARGET, s + 16)
            b.kill(ALPHA[3][0], BRAVO[4][0], s + 18)
        elif rnum == 12:
            # 正常有支援死亡(先拿一个人头)
            b.kill(TARGET, BRAVO[3][0], s + 18)
            b.kill(BRAVO[2][0], TARGET, s + 24)
        elif rnum == 13:
            # CT 前压死亡(Mid,开局 10s)
            b.move(ALPHA[3][0], "CT Spawn", s + 2)
            b.move(ALPHA[4][0], "CT Spawn", s + 2)
            b.move(TARGET, "Mid", s + 5)
            b.kill(BRAVO[1][0], TARGET, s + 10)
        elif rnum == 14:
            # CT 前压 + 无保护 peek 死亡(A Ramp,开局 8s,先手伤害)
            b.move(TARGET, "A Ramp", s + 4)
            b.damage(TARGET, BRAVO[0][0], s + 7.6, 44, "m4a1")
            b.kill(BRAVO[0][0], TARGET, s + 8.1)
        elif rnum == 15:
            # 被白死亡
            b.nade(BRAVO[2][0], "Flash", s + 14.0, "Mid")
            b.kill(BRAVO[2][0], TARGET, s + 15.2, victim_blind=True)
        elif rnum == 16:
            # CT 正常局:老王倒地后目标补枪,双杀存活
            b.kill(BRAVO[3][0], ALPHA[3][0], s + 17)
            b.kill(TARGET, BRAVO[3][0], s + 19)
            b.kill(TARGET, BRAVO[4][0], s + 26, weapon="awp", headshot=True)
        elif rnum == 17:
            # CT 前压死亡(B Apps,开局 15s)
            b.move(TARGET, "B Apps", s + 7)
            b.kill(BRAVO[4][0], TARGET, s + 15)
        elif rnum == 18:
            # 强子近距离倒地未补(漏补枪),阿伟回防后有支援死亡
            b.move(ALPHA[2][0], "Mid", s + 10)
            b.kill(BRAVO[0][0], ALPHA[2][0], s + 19)
            b.move(ALPHA[1][0], "Mid", s + 20)
            b.kill(TARGET, BRAVO[3][0], s + 24)
            b.kill(BRAVO[1][0], TARGET, s + 27)
        elif rnum == 19:
            # CT 主动 peek 死亡 + 前压(开局 12s)
            b.move(TARGET, "B Apps", s + 6)
            b.damage(TARGET, BRAVO[3][0], s + 11.4, 38, "m4a1")
            b.kill(BRAVO[3][0], TARGET, s + 12.0)
        elif rnum == 20:
            # CT 突破首杀成功
            b.kill(TARGET, BRAVO[1][0], s + 14, headshot=True)
            b.kill(TARGET, BRAVO[2][0], s + 20)
        elif rnum == 21:
            # 回转支援时孤立死亡 + 强子中距离倒地未补(漏补枪)
            b.move(ALPHA[3][0], "CT Spawn", s + 2)
            b.move(ALPHA[4][0], "CT Spawn", s + 2)
            b.move(ALPHA[2][0], "Mid", s + 10)
            b.damage(TARGET, BRAVO[1][0], s + 23.5, 50, "m4a1")
            b.kill(BRAVO[2][0], ALPHA[2][0], s + 22)
            b.kill(BRAVO[2][0], TARGET, s + 30)
        elif rnum == 22:
            # 漏补枪:阿伟近距离倒地无人补,目标之后正常死亡
            b.move(ALPHA[1][0], "Mid", s + 10)
            b.kill(BRAVO[4][0], ALPHA[1][0], s + 18)
            b.kill(TARGET, BRAVO[3][0], s + 26.5)
            b.kill(BRAVO[0][0], TARGET, s + 33)
        elif rnum == 23:
            # 协同闪优势局 + 目标补枪
            b.kill(BRAVO[2][0], ALPHA[2][0], s + 15)
            b.nade(ALPHA[4][0], "Flash", s + 16.0, "Mid")
            b.kill(TARGET, BRAVO[0][0], s + 17.4)
            b.kill(TARGET, BRAVO[2][0], s + 19)
        else:  # 24
            # 残局:队友先全灭,目标最后死亡
            b.kill(BRAVO[1][0], ALPHA[1][0], s + 14)
            b.kill(BRAVO[2][0], ALPHA[2][0], s + 16)
            b.kill(BRAVO[3][0], ALPHA[3][0], s + 18)
            b.kill(BRAVO[4][0], ALPHA[4][0], s + 20)
            b.kill(BRAVO[0][0], TARGET, s + 31)

        # 场景脚本走位优先于默认路线:删除脚本之后残留的路线节点
        b.truncate_routes()

        # -------- 收尾:补齐歼灭链(留 6 秒空档,避免误触发补枪窗口)--------
        dead_alpha = {sid for sid, _ in ALPHA if sid in b.dead_at}
        dead_bravo = {sid for sid, _ in BRAVO if sid in b.dead_at}
        winner_alpha = alpha_wins[rnum]
        t_fill = b.last_t + 6.0
        # 输的一方全灭;赢的一方保留脚本中已死的人
        losers = [sid for sid, _ in (ALPHA if not winner_alpha else BRAVO) if sid not in dead_alpha | dead_bravo]
        winners_alive = [sid for sid, _ in (BRAVO if not winner_alpha else ALPHA)
                         if sid not in dead_alpha | dead_bravo]
        for i, victim in enumerate(losers):
            killer = winners_alive[i % len(winners_alive)]
            b.kill(killer, victim, t_fill + i * rng.uniform(1.5, 3.5))
        end_s = b.last_t + 2.0

        rd = RoundData(
            round=rnum, start_tick=b.start_tick,
            freeze_time_end_tick=b.start_tick + int(FREEZE_S * TICK_RATE),
            end_tick=b.start_tick + int(end_s * TICK_RATE),
            winner=alpha_side if winner_alpha else bravo_side,
            reason="elimination",
        )
        if winner_alpha:
            alpha_score += 1
        else:
            bravo_score += 1
        rd.t_score = alpha_score if alpha_side == SIDE_T else bravo_score
        rd.ct_score = bravo_score if alpha_side == SIDE_T else alpha_score
        rounds.append(rd)
        kills += b.kills
        damages += b.damages
        grenades += b.grenades
        frames += b.build_frames()
        tick_cursor = rd.end_tick + int(5 * TICK_RATE)

    players = [PlayerInfo(steamid=s, name=n, team_name=ALPHA_NAME) for s, n in ALPHA] + \
              [PlayerInfo(steamid=s, name=n, team_name=BRAVO_NAME) for s, n in BRAVO]
    duration = (rounds[-1].end_tick - rounds[0].start_tick) / TICK_RATE
    return MatchData(
        match_id=match_id, map_name=MAP, tick_rate=TICK_RATE,
        duration_seconds=round(duration, 1), source="sample",
        team_names=[ALPHA_NAME, BRAVO_NAME],
        final_score={ALPHA_NAME: alpha_score, BRAVO_NAME: bravo_score},
        players=players, rounds=rounds,
        kills=sorted(kills, key=lambda k: (k.round, k.tick)),
        damages=sorted(damages, key=lambda d: (d.round, d.tick)),
        grenades=sorted(grenades, key=lambda g: (g.round, g.throw_tick)),
        frames=sorted(frames, key=lambda f: (f.round, f.tick)),
    )

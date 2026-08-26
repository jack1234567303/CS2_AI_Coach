"""规范化比赛数据模型。

awpy 输出(不同版本字段名有差异)统一在本层收敛为这些模型,
后续所有分析模块只依赖这里的结构,不直接接触 awpy 原始 dict。
"""
from __future__ import annotations

from typing import Optional, List
from pydantic import BaseModel, Field

SIDE_T = 2
SIDE_CT = 3
SIDE_LABEL = {SIDE_T: "T", SIDE_CT: "CT"}


class KillEvent(BaseModel):
    round: int                      # 1-based 回合数
    tick: int                       # demo tick(绝对)
    second: float = 0.0             # 回合开始起算的秒数
    attacker_steamid: Optional[str] = None   # None = 世界/自杀
    attacker_name: str = "World"
    attacker_side: Optional[int] = None
    attacker_x: float = 0.0
    attacker_y: float = 0.0
    attacker_z: float = 0.0
    attacker_area: Optional[str] = None
    victim_steamid: str = ""
    victim_name: str = ""
    victim_side: Optional[int] = None
    victim_x: float = 0.0
    victim_y: float = 0.0
    victim_z: float = 0.0
    victim_area: Optional[str] = None
    assister_steamid: Optional[str] = None
    assister_name: Optional[str] = None
    weapon: str = ""
    headshot: bool = False
    penetrated: bool = False
    noscope: bool = False
    thrusmoke: bool = False
    attacker_blind: bool = False
    victim_blind: bool = False
    distance: Optional[float] = None  # CS2 世界单位


class DamageEvent(BaseModel):
    round: int
    tick: int
    second: float = 0.0
    attacker_steamid: Optional[str] = None
    attacker_name: str = "World"
    attacker_side: Optional[int] = None
    attacker_x: float = 0.0
    attacker_y: float = 0.0
    attacker_z: float = 0.0
    victim_steamid: str = ""
    victim_name: str = ""
    victim_side: Optional[int] = None
    victim_x: float = 0.0
    victim_y: float = 0.0
    victim_z: float = 0.0
    dmg_health: int = 0
    dmg_armor: int = 0
    weapon: str = ""
    hitgroup: int = 0   # 1=头 4=胸 5=胃 6=左手臂 7=右手臂 8=左腿 9=右腿


class GrenadeEvent(BaseModel):
    round: int
    grenade_type: str        # Smoke / Flash / HE / Molotov / Incendiary / Decoy
    player_steamid: str = ""
    player_name: str = ""
    player_side: Optional[int] = None
    throw_tick: int = 0
    throw_second: float = 0.0
    throw_x: float = 0.0
    throw_y: float = 0.0
    throw_z: float = 0.0
    detonation_tick: Optional[int] = None    # 爆开/落地/开始燃烧
    detonation_x: Optional[float] = None
    detonation_y: Optional[float] = None
    detonation_z: Optional[float] = None
    destroyed_tick: Optional[int] = None     # 烟雾消散 / 火焰熄灭
    destroyed_by_steamid: Optional[str] = None
    end_second: Optional[float] = None       # 持续秒数(若有 destroyed_tick)


class PlayerFrame(BaseModel):
    steamid: str
    name: str
    side: int = SIDE_T
    alive: bool = True
    hp: int = 100
    armor: int = 0
    helmet: bool = False
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    last_place: Optional[str] = None
    active_weapon: Optional[str] = None
    blind: float = 0.0        # 剩余被闪光致盲秒数
    has_bomb: bool = False
    has_defuser: bool = False
    money: Optional[int] = None


class Frame(BaseModel):
    round: int
    tick: int
    second: float = 0.0
    players: List[PlayerFrame] = Field(default_factory=list)
    t_alive: int = 0
    ct_alive: int = 0
    bomb_planted: bool = False
    bomb_site: Optional[str] = None


class RoundData(BaseModel):
    round: int                       # 1-based
    start_tick: int = 0
    freeze_time_end_tick: int = 0
    end_tick: int = 0
    winner: Optional[int] = None     # SIDE_T / SIDE_CT
    reason: Optional[str] = None
    t_score: int = 0                 # 本回合结束后的比分
    ct_score: int = 0
    bomb_planted: bool = False
    bomb_site: Optional[str] = None


class PlayerInfo(BaseModel):
    steamid: str
    name: str
    team_name: str = ""


class RecoveredPosition(BaseModel):
    """Demo 中缺失 tick 位置的玩家,用道具投掷原点恢复的稀疏位置样本。"""
    steamid: str
    name: str = ""
    round: int
    tick: int
    x: float
    y: float


class MatchData(BaseModel):
    """一场比赛的完整规范化数据(分析引擎的唯一输入)。"""
    match_id: str
    map_name: str = ""               # 如 de_mirage
    tick_rate: int = 64
    duration_seconds: float = 0.0
    source: str = "awpy"             # awpy | sample
    team_names: List[str] = Field(default_factory=list)
    final_score: dict = Field(default_factory=dict)   # {"team_name": rounds}
    players: List[PlayerInfo] = Field(default_factory=list)
    rounds: List[RoundData] = Field(default_factory=list)
    kills: List[KillEvent] = Field(default_factory=list)
    damages: List[DamageEvent] = Field(default_factory=list)
    grenades: List[GrenadeEvent] = Field(default_factory=list)
    frames: List[Frame] = Field(default_factory=list)  # 已采样的帧
    recovered_positions: List[RecoveredPosition] = Field(default_factory=list)


# ---------------- 分析输出模型 ----------------

class Evidence(BaseModel):
    """一条数据依据:必须能定位到具体回合/tick/事件。"""
    round: Optional[int] = None
    tick: Optional[int] = None
    description: str = ""


class Problem(BaseModel):
    type: str                 # 如 bad_peek / solo_death / flash_utilization_low
    severity: str = "medium"  # high / medium / low
    title: str = ""
    detail: str = ""
    evidence: List[Evidence] = Field(default_factory=list)


class Strength(BaseModel):
    title: str
    detail: str
    evidence: List[Evidence] = Field(default_factory=list)

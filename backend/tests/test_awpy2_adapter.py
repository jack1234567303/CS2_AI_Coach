"""awpy 2.x 转换器测试:用合成 DataFrame 行验证字段映射。"""
import pytest

from backend.parser.awpy_adapter import convert_awpy2


class FakeDemo:
    """模拟 awpy 2.x Demo 对象的最小结构(行列表即可,_rows 兼容)。"""

    def __init__(self):
        self.header = {"map_name": "de_mirage", "playback_ticks": 64000, "playback_time": 1000.0}
        self.rounds = [
            {"round_num": 1, "start": 1000, "freeze_end": 1960, "end": 4000,
             "official_end": 4100, "winner": "ct", "reason": "ct_killed",
             "bomb_plant": None, "bomb_site": "not_planted"},
            {"round_num": 2, "start": 4200, "freeze_end": 5160, "end": 8000,
             "official_end": 8100, "winner": "t", "reason": "target_bombed",
             "bomb_plant": 7000, "bomb_site": "bombsite_b"},
        ]
        self.kills = [
            {"tick": 2600, "round_num": 1,
             "attacker_steamid": "A1", "attacker_name": "alice",
             "attacker_team_name": "CT", "attacker_X": 1.0, "attacker_Y": 2.0,
             "attacker_last_place_name": "Jungle",
             "victim_steamid": "B1", "victim_name": "bob",
             "victim_team_name": "TERRORIST", "victim_X": 4.0, "victim_Y": 5.0,
             "victim_last_place_name": "Mid",
             "weapon": "ak47", "headshot": True, "penetrated": False,
             "attackerblind": False, "distance": 800.5},
            {"tick": 8100, "round_num": 2,
             "attacker_steamid": "B1", "attacker_name": "bob",
             "attacker_team_name": "TERRORIST",
             "victim_steamid": "A2", "victim_name": "carol",
             "victim_team_name": "CT", "weapon": "awp", "headshot": True},
        ]
        self.damages = [
            {"tick": 2590, "round_num": 1, "attacker_steamid": "A1",
             "attacker_name": "alice", "victim_steamid": "B1", "victim_name": "bob",
             "dmg_health": 55, "dmg_armor": 10, "weapon": "ak47", "hitgroup": "head"},
        ]
        self.grenades = [
            # 闪光投掷(entity 77):轨迹流会产生同一 entity 的多行,须只留最早一行
            {"tick": 2400, "round_num": 1, "thrower": "alice", "thrower_steamid": "A1",
             "grenade_type": "flashbang", "X": 10.0, "Y": 20.0, "Z": 0.0, "entity_id": 77},
            {"tick": 2408, "round_num": 1, "thrower": "alice", "thrower_steamid": "A1",
             "grenade_type": "flashbang", "X": 10.5, "Y": 20.5, "Z": 0.0, "entity_id": 77},
            {"tick": 2416, "round_num": 1, "thrower": "alice", "thrower_steamid": "A1",
             "grenade_type": "flashbang", "X": 11.0, "Y": 21.0, "Z": 0.0, "entity_id": 77},
            # HE:内部类型名 chegrenade(HE 手雷)也应归一化
            {"tick": 2500, "round_num": 1, "thrower": "bob", "thrower_steamid": "B1",
             "grenade_type": "chegrenade", "X": 5.0, "Y": 6.0, "Z": 0.0, "entity_id": 79},
            # 烟雾投掷行应被跳过(由 smokes 实体表提供)
            {"tick": 2450, "round_num": 1, "thrower": "bob", "thrower_steamid": "B1",
             "grenade_type": "smoke", "X": 30.0, "Y": 40.0, "Z": 0.0, "entity_id": 78},
        ]
        self.smokes = [
            {"round_num": 1, "start_tick": 2500, "end_tick": 3600, "entity_id": 78,
             "thrower_steamid": "B1", "thrower_name": "bob", "X": 31.0, "Y": 41.0, "Z": 0.0},
        ]
        self.infernos = []
        self.events = {
            "flashbang_detonate": [
                {"tick": 2440, "entityid": 77, "x": 11.0, "y": 21.0, "z": 0.0},
            ],
            "hegrenade_detonate": [],
        }
        self.ticks = [
            # 同一 tick 的多个玩家必须都保留
            {"tick": 2400, "round_num": 1, "name": "alice", "steamid": "A1",
             "side": "ct", "X": 1.0, "Y": 2.0, "Z": 64.0, "health": 100, "place": "Jungle"},
            {"tick": 2400, "round_num": 1, "name": "bob", "steamid": "B1",
             "side": "t", "X": 4.0, "Y": 5.0, "Z": 64.0, "health": 100, "place": "Mid"},
            {"tick": 2401, "round_num": 1, "name": "alice", "steamid": "A1",
             "side": "ct", "X": 1.5, "Y": 2.5, "Z": 64.0, "health": 90, "place": "Jungle"},
            {"tick": 2700, "round_num": 1, "name": "alice", "steamid": "A1",
             "side": "ct", "X": 2.0, "Y": 3.0, "Z": 64.0, "health": 80, "place": "Connector"},
        ]


def test_convert_awpy2_basic_mapping():
    m = convert_awpy2(FakeDemo(), "m2")
    assert m.map_name == "de_mirage"
    assert m.tick_rate == 64            # playback_ticks / playback_time
    assert len(m.rounds) == 2
    r1, r2 = m.rounds
    assert r1.winner == 3 and r2.winner == 2
    assert r2.bomb_planted and r2.bomb_site == "bombsite_b"

    k = m.kills[0]
    assert k.attacker_steamid == "A1" and k.attacker_side == 3
    assert k.victim_side == 2 and k.headshot and k.weapon == "ak47"
    assert k.attacker_area == "Jungle" and k.victim_area == "Mid"
    assert k.distance == pytest.approx(800.5)
    assert k.round == 1 and k.second > 0

    d = m.damages[0]
    assert d.dmg_health == 55

    # 闪光:轨迹流按 entity 去重后只留一行;爆裂 tick/坐标联动;烟雾来自实体表(含生命周期)
    flash_rows = [g for g in m.grenades if g.grenade_type == "Flash"]
    assert len(flash_rows) == 1, "同一 entity 的轨迹行必须去重为一行"
    flash = flash_rows[0]
    assert flash.player_steamid == "A1"
    assert flash.throw_tick == 2400            # 最早一行(投掷瞬间)
    assert flash.detonation_tick == 2440
    assert flash.detonation_x == pytest.approx(11.0)
    # HE 类型归一化(chegrenade -> HE)
    assert sum(1 for g in m.grenades if g.grenade_type == "HE") == 1
    smoke = next(g for g in m.grenades if g.grenade_type == "Smoke")
    assert smoke.player_steamid == "B1"
    assert smoke.destroyed_tick == 3600
    assert smoke.end_second == pytest.approx((3600 - 2500) / 64, rel=0.01)
    # 投掷表里的 smoke 不应重复出现
    assert sum(1 for g in m.grenades if g.grenade_type == "Smoke") == 1

    # 帧:同 tick 两个玩家都在,2401 被采样丢弃,2700 保留
    frames = [f for f in m.frames if f.round == 1]
    ticks = [f.tick for f in frames]
    assert 2400 in ticks and 2401 not in ticks and 2700 in ticks
    f0 = next(f for f in frames if f.tick == 2400)
    assert len(f0.players) == 2
    assert f0.t_alive == 1 and f0.ct_alive == 1
    bob = next(p for p in f0.players if p.steamid == "B1")
    assert bob.side == 2 and bob.last_place == "Mid"

    # 队伍:A1(carol) 为 A 队,第 1 回合 CT 方 -> 第 1 回合 winner ct 计给 A 队
    assert m.final_score == {"A 队": 1, "B 队": 1}
    teams = {p.steamid: p.team_name for p in m.players}
    assert teams["A1"] == "A 队" and teams["B1"] == "B 队"


def test_convert_awpy2_null_position_fallback():
    """真实 Demo 中 demoparser2 对死亡/未出生玩家返回 null 坐标:
    tick 前向填充 + 事件坐标 asof 回填,不允许再出现 (0,0) 坍缩。"""
    import polars as pl

    class NullPosDemo:
        """最小 Demo:只有回个回合 + 带空坐标的 polars kills/damages/ticks。"""

        def __init__(self):
            self.header = {"map_name": "de_mirage", "playback_ticks": 64000,
                           "playback_time": 1000.0}
            self.rounds = [{"round_num": 1, "start": 1000, "freeze_end": 1960,
                            "end": 4000, "official_end": 4100, "winner": "ct",
                            "reason": "ct_killed", "bomb_site": "not_planted"}]
            self.grenades = []
            self.smokes = []
            self.infernos = []
            self.events = {"flashbang_detonate": [], "hegrenade_detonate": []}
            self._pl_ticks = pl.DataFrame([
                # bob 在 2400 有位置,2600 起死亡 -> 坐标 null
                {"tick": 2400, "round_num": 1, "name": "bob", "steamid": "B1",
                 "side": "t", "X": 4.0, "Y": 5.0, "Z": 64.0, "health": 100, "place": "Mid"},
                {"tick": 2400, "round_num": 1, "name": "alice", "steamid": "A1",
                 "side": "ct", "X": 1.0, "Y": 2.0, "Z": 64.0, "health": 100, "place": "Jungle"},
                {"tick": 2600, "round_num": 1, "name": "bob", "steamid": "B1",
                 "side": "t", "X": None, "Y": None, "Z": None, "health": 0, "place": None},
                {"tick": 2600, "round_num": 1, "name": "alice", "steamid": "A1",
                 "side": "ct", "X": 1.5, "Y": 2.5, "Z": 64.0, "health": 100, "place": "Connector"},
                {"tick": 2608, "round_num": 1, "name": "bob", "steamid": "B1",
                 "side": "t", "X": None, "Y": None, "Z": None, "health": 0, "place": None},
            ])
            # 击杀(tick 2600):受害者坐标 null(死亡瞬间 pawn 丢失)
            self._pl_kills = pl.DataFrame([dict(
                tick=2600, round_num=1,
                attacker_steamid="A1", attacker_name="alice", attacker_team_name="CT",
                attacker_X=1.0, attacker_Y=2.0, attacker_place="Connector",
                victim_steamid="B1", victim_name="bob", victim_team_name="TERRORIST",
                victim_X=None, victim_Y=None, victim_place=None,
                weapon="m4a1", headshot=True,
            )])
            self._pl_damages = pl.DataFrame([
                dict(tick=2590, round_num=1, attacker_steamid="A1", attacker_name="alice",
                     victim_steamid="B1", victim_name="bob", victim_X=None,
                     victim_Y=None, dmg_health=40, dmg_armor=0, weapon="m4a1", hitgroup=4),
            ])

        @property
        def ticks(self):
            return self._pl_ticks

        @property
        def kills(self):
            return self._pl_kills

        @property
        def damages(self):
            return self._pl_damages

    m = convert_awpy2(NullPosDemo(), "m3")
    # 击杀的受害者坐标从同回合最近非空 tick(2400)回填
    k = m.kills[0]
    assert k.victim_x == pytest.approx(4.0)
    assert k.victim_y == pytest.approx(5.0)
    assert k.victim_area == "Mid"
    # 回填 join 不得丢失玩家 steamid(回归:join 中间列曾覆盖原列)
    assert k.attacker_steamid == "A1"
    assert k.victim_steamid == "B1"
    # 伤害事件的空坐标同样回填
    d = m.damages[0]
    assert d.victim_x == pytest.approx(4.0)
    # 帧:2600 的 bob 死亡行继承 2400 位置(尸体留在死亡点);2608 被采样丢弃
    frames = {f.tick: f for f in m.frames}
    assert 2600 in frames and 2608 not in frames
    bob = next(p for p in frames[2600].players if p.steamid == "B1")
    assert bob.x == pytest.approx(4.0) and not bob.alive
    assert bob.last_place == "Mid"

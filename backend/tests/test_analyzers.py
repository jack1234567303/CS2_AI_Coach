"""分析器单元测试:基于确定性示例比赛断言脚本化场景。"""
import pytest

from backend.analyzer.common import MatchContext
from backend.analyzer.engine import run_full_analysis
from backend.sample_data import BRAVO, TARGET, generate_sample_match


@pytest.fixture(scope="module")
def analysis():
    match = generate_sample_match("test-match")
    return run_full_analysis(match, TARGET)


def _causes(analysis):
    return {d.round: d.cause for d in analysis.deaths.deaths}


class TestDeathAnalysis:
    def test_scripted_causes_detected(self, analysis):
        c = _causes(analysis)
        # 无保护 peek 死亡(先手伤害 + 无支援)
        for r in (3, 6, 10, 14, 19):
            assert c[r] == "peek_death", f"round {r}: {c[r]}"
        # 孤立无支援死亡(未先手 + 队友远)
        for r in (2, 8, 13, 17, 21):
            assert c[r] == "isolated_death", f"round {r}: {c[r]}"
        # 其他脚本场景
        assert c[4] == "late_trade"      # 队友远处倒地后补枪迟到
        assert c[15] == "blind_death"    # 被白死亡
        assert c[24] == "clutch"         # 残局孤军
        assert c[5] == "traded_death"    # 死后被补枪
        assert c[12] == "protected_death"

    def test_every_death_has_round_and_distance(self, analysis):
        assert analysis.deaths.total_deaths == 18
        for d in analysis.deaths.deaths:
            assert d.round > 0 and d.tick > 0
            # 残局(队友全灭)时最近队友距离为 None 是合法的
            if d.teammates_alive > 0:
                assert d.nearest_teammate_distance is not None
            assert "第" in d.description and "回合" in d.description

    def test_problems_have_evidence(self, analysis):
        assert analysis.problems, "应当检出问题"
        for p in analysis.problems:
            assert p.evidence, f"问题 {p.type} 缺少数据依据"
            for e in p.evidence:
                assert e.round and e.description


class TestPeekAnalysis:
    def test_unprotected_peeks_detected(self, analysis):
        p = analysis.peeks
        assert p.unprotected_peeks >= 5
        assert p.unprotected_peek_deaths >= 5
        areas = {a.area for a in p.top_areas}
        assert any(a in areas for a in ("A Ramp", "B Apps", "Mid"))

    def test_flash_support_detected(self, analysis):
        # R1/R7/R23 有队友协同闪
        assert analysis.peeks.with_flash_support >= 3


class TestTeamwork:
    def test_trade_kills(self, analysis):
        rounds = {t.round for t in analysis.teamwork.trade_kills}
        assert 9 in rounds and 16 in rounds and 23 in rounds

    def test_missed_trades_flagged(self, analysis):
        # R18/R21/R22 近距离队友倒地未补
        rounds = {m.round for m in analysis.teamwork.missed_trades}
        assert {18, 21, 22} <= rounds
        assert any(p.type == "missed_trade" for p in analysis.problems)


class TestStats:
    def test_target_boxscore(self, analysis):
        ts = analysis.target_stats
        assert ts.kills == 21 and ts.deaths == 18
        assert 60 <= ts.adr <= 85
        assert ts.entry_attempts >= 5 and ts.entry_successes >= 4
        assert 0 < ts.kast <= 1

    def test_all_players_have_stats(self, analysis):
        assert len(analysis.stats.players) == 10
        for p in analysis.stats.players:
            assert p.rounds_played == 24
            assert p.rating > 0


class TestUtility:
    def test_flash_usage_low_flagged(self, analysis):
        u = analysis.utility
        assert u.flash.thrown >= 20
        assert any(p.type == "flash_utilization_low" for p in analysis.problems)

    def test_smoke_stats(self, analysis):
        assert analysis.utility.smoke.thrown >= 5
        assert analysis.utility.smoke.avg_duration > 0


class TestPosition:
    def test_heatmap_points_normalized(self, analysis):
        pts = analysis.position.points
        assert len(pts) > 100
        assert all(0.0 <= p.x <= 1.0 and 0.0 <= p.y <= 1.0 for p in pts)
        # 点位带阵营信息,且目标上/下半场两种阵营都有足迹
        assert all(p.side in (2, 3, None) for p in pts)
        assert any(p.side == 2 for p in pts), "应有 T 方点位"
        assert any(p.side == 3 for p in pts), "应有 CT 方点位"

    def test_sample_players_have_movement(self):
        """所有玩家都应有活动范围(修复:非目标玩家不再钉死在一个点)。"""
        from backend.analyzer.common import MatchContext
        from backend.sample_data import ALPHA, generate_sample_match
        m = generate_sample_match("test-move")
        ctx = MatchContext(m)
        for sid, name in ALPHA:
            a = run_full_analysis(m, sid)
            areas = {(round(p.x, 2), round(p.y, 2)) for p in a.position.points
                     if p.kind == "presence"}
            assert len(areas) >= 5, f"{name} 的活动范围过小: {len(areas)} 个点"

    def test_ct_aggression(self, analysis):
        assert analysis.position.ct_aggressive_death_rate == pytest.approx(0.4, abs=0.2)
        assert any(p.type == "ct_over_aggressive" for p in analysis.problems)

    def test_death_hotspot(self, analysis):
        assert analysis.position.area_stats[0].deaths >= 4


class TestCoach:
    def test_template_report_structure(self):
        from backend.ai.coach import generate_report
        match = generate_sample_match("test-coach")
        a = run_full_analysis(match, TARGET)
        result = generate_report(match, a, use_llm=False)
        assert result["llm_used"] is False
        report = result["report"]
        for section in ("# 玩家分析", "## 优势", "## 最大问题", "## 数据依据", "## 训练建议"):
            assert section in report
        assert "第" in report  # 引用回合


class TestParserAdapter:
    def test_normalize_awpy_kills(self):
        """验证 awpy 输出 -> 规范化模型的字段映射。"""
        from backend.parser.awpy_adapter import normalize_awpy
        raw = {
            "map_name": "de_inferno", "tick_rate": 64,
            "rounds": [{
                "round": 1, "start_tick": 1000, "freeze_time_end_tick": 1960,
                "end_tick": 5000, "winner": 2, "reason": "elimination",
                "t_score": 1, "ct_score": 0, "bomb_planted": False,
                "kills": [{
                    "tick": 2600, "attacker_steamid": "A1", "attacker_name": "alice",
                    "attacker_side": 2, "attacker_x": 1.0, "attacker_y": 2.0, "attacker_z": 3.0,
                    "victim_steamid": "B1", "victim_name": "bob", "victim_side": 3,
                    "victim_x": 4.0, "victim_y": 5.0, "victim_z": 6.0,
                    "weapon": "weapon_ak47", "headshot": True, "thrusmoke": False,
                }],
                "damages": [{
                    "tick": 2590, "attacker_steamid": "A1", "attacker_name": "alice",
                    "victim_steamid": "B1", "victim_name": "bob",
                    "dmg_health": 55, "dmg_armor": 10, "weapon": "weapon_ak47", "hitgroup": 1,
                }],
                "grenades": [{
                    "grenade_type": "Smoke", "throw_tick": 2100, "player_steamid": "A1",
                    "player_name": "alice", "x": 10.0, "y": 20.0, "z": 0.0,
                    "detonation_tick": 2200, "destroyed_by_tick": 3400,
                }],
                "frames": [{
                    "tick": 2500,
                    "t": {"players": [{"steamid": "A1", "name": "alice", "side": 2,
                                       "x": 1.0, "y": 2.0, "hp": 100, "alive": True,
                                       "last_place": "Banana", "blind": 0.0}]},
                    "ct": {"players": [{"steamid": "B1", "name": "bob", "side": 3,
                                        "x": 4.0, "y": 5.0, "hp": 55, "alive": True,
                                        "last_place": "Car", "blind": 1.2}]},
                }],
            }],
        }
        m = normalize_awpy(raw, "m1")
        assert m.map_name == "de_inferno" and m.tick_rate == 64
        assert len(m.kills) == 1
        k = m.kills[0]
        assert k.round == 1 and k.weapon == "ak47" and k.headshot
        assert k.attacker_steamid == "A1" and k.victim_steamid == "B1"
        assert m.damages[0].dmg_health == 55
        g = m.grenades[0]
        assert g.grenade_type == "Smoke" and g.destroyed_tick == 3400
        assert g.end_second == pytest.approx((3400 - 2200) / 64)
        assert len(m.frames) == 1
        f = m.frames[0]
        assert f.t_alive == 1 and f.ct_alive == 1
        assert f.players[1].blind == pytest.approx(1.2)
        assert m.players and {p.steamid for p in m.players} == {"A1", "B1"}

    def test_zip_extraction(self, tmp_path):
        import zipfile
        from backend.parser.awpy_adapter import extract_dem_from_zip
        zp = tmp_path / "demo.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("perfectworld/match_123.dem", b"PBDEMSYNC")
        dem = extract_dem_from_zip(zp, tmp_path / "out")
        assert dem.exists() and dem.suffix == ".dem"
